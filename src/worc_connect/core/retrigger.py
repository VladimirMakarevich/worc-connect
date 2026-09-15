"""When a sighting of an item the connector already handled is a *new* request, and what decides it.

A task id is never reused, so a re-trigger is a fresh attempt at the next sequence number; what this
module owns is whether a sighting *is* one. Two mechanisms answer that, and both rest on events the
connector observed rather than inferred.

A row is **armed** while its trigger is withdrawn: the label taken off, or the item closed by the
connector's own merge. A trigger re-applied to an armed row whose task has nothing left in flight is
a new request. A trigger put back while the task still runs disarms the row instead — a label cycled
mid-run is a correction, and it must not become a second task the moment this one ends. So "armed"
means the trigger is currently withdrawn, never that it was withdrawn once.

A row that ended by asking the reporter a question needs no arming. The reporter answering — the
item changing after the question was posted — is the new request, and the stamp the question itself
left on the item is what an answer has to beat: the connector's own label and comment are updates
too, and measured from the start of the attempt they would read as the answer on every tick.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from worc_connect.core.items import WorkItem
from worc_connect.core.state import TERMINAL_PHASES, ItemRow, Phase, StateStore
from worc_connect.trackers.base import TrackerAdapter, TrackerError

logger = logging.getLogger(__name__)

# The phases a re-trigger may start a new attempt from: worc is finished with the task, or has
# handed it to a pull request the connector is only watching. Anything earlier is still in flight,
# and a second task for an item already being worked on is exactly what the id rule forbids.
RETRIGGERABLE_PHASES: Final = TERMINAL_PHASES | {Phase.PR_OPEN}


@dataclass(frozen=True)
class Retrigger:
    """One watcher's re-trigger bookkeeping.

    Reads the item back through ``adapter`` where a stamp has to come from the tracker, persists
    every change to a row through ``store``, and falls back to ``now_fn`` where the tracker cannot
    be asked.
    """

    adapter: TrackerAdapter
    store: StateStore
    now_fn: Callable[[], datetime]

    @staticmethod
    def is_new_request(row: ItemRow, item: WorkItem, *, admitted: bool) -> bool:
        """Whether this sighting starts a **new** attempt at an item the connector already handled.

        Only a row that is finished — or waiting on a pull request, which worc is done with — can
        be re-triggered, and only while it is armed: the connector has seen the trigger withdrawn,
        or closed the item itself, and now sees the trigger again. A row the triage step left at
        ``needs-info`` is the one case that needs no arming: the connector asked the reporter a
        question, so the reporter answering it — the item changing after the question was posted —
        *is* the new request, and waiting for a label to be cycled would make the round trip a step
        the operator has to perform by hand.
        """
        if not admitted:
            return False
        if row.phase is Phase.NEEDS_INFO:
            return _answered(row, item)
        return row.retrigger_armed and row.phase in RETRIGGERABLE_PHASES

    def withdrawn(self, row: ItemRow, *, dry_run: bool, now: datetime) -> ItemRow:
        """The row once its trigger was seen taken off: armed, so the next re-application is new."""
        return self._rearm(row, armed=True, dry_run=dry_run, now=now)

    def restored(self, row: ItemRow, *, dry_run: bool, now: datetime) -> ItemRow:
        """The row once its trigger was seen back on while its task is still in flight: disarmed.

        A row that could be re-triggered from where it stands is left alone — for such a row a
        restored trigger is the new request, and that is decided before this is asked. For every
        earlier phase the label coming back undoes the withdrawal, so that the task ending later
        does not start a second one nobody asked for.
        """
        if row.phase in RETRIGGERABLE_PHASES:
            return row
        return self._rearm(row, armed=False, dry_run=dry_run, now=now)

    def closed(self, row: ItemRow, *, now: datetime) -> ItemRow:
        """The row once the connector itself closed its item: armed.

        A closed item leaves the tracker's open listing, so only somebody reopening it or
        re-applying the label brings it back — and either is a new request. Armed only once the
        close actually happened: a close the tracker refused must not leave a row the next tick
        reads as a fresh request.
        """
        return self._rearm(row, armed=True, dry_run=False, now=now)

    def question_posted(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Record the item's update stamp as it stands *after* the question was posted on it.

        Publishing ``needs-info`` writes a label and a comment, and a tracker stamps both as updates
        to the item. Measured from the stamp the attempt started with, the connector's own question
        would read as the reporter's answer and re-run triage on every tick; measured from the stamp
        the question left behind, only a later change does. The item is read back for that stamp
        because the tracker's clock is the one its listing compares against. Where it cannot be
        read, the connector's own clock at this moment stands in: a reply inside the skew between
        the two clocks is missed, which is the side that costs nothing, where a stale stamp would
        cost a triage run.
        """
        try:
            stamp = self.adapter.get_item(item.identifier).updated_at
        except TrackerError as exc:
            logger.warning(
                "item=%s task=%s action=retrigger result=stamped-from-clock (%s: %s)",
                row.item_id,
                row.task_id or "-",
                type(exc).__name__,
                exc,
            )
            stamp = self.now_fn()
        stamped = replace(row, item_updated_at=stamp, updated_at=now)
        self.store.save(stamped)
        return stamped

    def _rearm(self, row: ItemRow, *, armed: bool, dry_run: bool, now: datetime) -> ItemRow:
        """Persist ``armed`` on the row where it differs; a dry run changes nothing."""
        if row.retrigger_armed is armed or dry_run:
            return row
        changed = replace(row, retrigger_armed=armed, updated_at=now)
        self.store.save(changed)
        logger.info(
            "item=%s task=%s action=retrigger result=%s",
            row.item_id,
            row.task_id or "-",
            "armed" if armed else "disarmed",
        )
        return changed


def _answered(row: ItemRow, item: WorkItem) -> bool:
    """Whether the item has changed since the connector posted its question on it.

    A row with no stamp to compare against is not treated as answered: the connector would then
    re-trigger on every tick, which is the one direction a fail-closed gate must not lean.
    """
    return row.item_updated_at is not None and item.updated_at > row.item_updated_at
