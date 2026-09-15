"""The tick and the watch loop: list, gate, record, and sleep — with no model call anywhere in it.

The loop is deliberately dull. It lists the items a tracker says changed, asks the gate about each
one, records what it decided, and advances a watermark. Everything that needs judgement is a worc
task the connector queues; the polling loop itself makes no model call, which is what keeps a tick
with nothing new free and a tick's behaviour predictable.

Two operational properties matter as much as the logic:

* **A failing tracker costs one tick.** All three adapter failures — unreachable, throttled, not
  logged in — are caught here, logged by class, and change no state. The next tick is the retry.
* **The listing is a filter, not the authority on what to follow.** It answers "what changed", and
  an item whose task is running is exactly an item nothing is changing — so every row still in
  flight is read by identifier when the poll window has moved past its item, and the watermark
  stays a cheap filter on updates rather than the thing that decides which tasks are watched.
* **Stopping is a file, not a signal** — a sentinel the loop polls and a PID file it holds, both of
  them in the connector's home. The mechanism lives next door; what the loop owns is when to ask.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core import builder, gate
from worc_connect.core.control import ProcessControl
from worc_connect.core.items import WorkItem
from worc_connect.core.reconcile import Reconciler
from worc_connect.core.retrigger import Retrigger
from worc_connect.core.state import (
    META_LAST_TICK_AT,
    META_LAST_TICK_RESULT,
    ItemRow,
    Phase,
    Stage,
    StateStore,
    read_watermark,
    write_watermark,
)
from worc_connect.core.worc_cli import WorcCommand, WorcUnavailable
from worc_connect.core.writeback import PHASE_STATES, Published, WriteBack
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerAdapter, TrackerError

logger = logging.getLogger(__name__)

# How far back of the watermark each listing reaches. The tracker's clock, the host's clock and the
# moment a tick finishes are three different things, and an item updated inside that spread would
# otherwise fall between two listings and never be seen. Re-listing an item is free: it already has
# a row, so the overlap is idempotent by construction.
WATERMARK_OVERLAP: Final = timedelta(minutes=10)

# Exit codes: 0 a completed pass, 1 an infrastructure failure or a refused start. A configuration
# problem is exit 2 and never reaches this module — the CLI refuses before a loop exists.
_EXIT_OK: Final = 0
_EXIT_FAILED: Final = 1


class Action(StrEnum):
    """What the tick decided to do about one item, in the vocabulary a dry run prints."""

    STAGE = "stage-task"
    FOLLOW = "follow"
    SKIP = "skip"


@dataclass(frozen=True)
class PlannedAction:
    """One item's outcome for this tick: what was decided and the reason it is reported under.

    Carries the item's identifier and URL and nothing else the item wrote — a plan an operator reads
    on a terminal is not a place for a stranger's text.
    """

    item_id: str
    url: str
    action: Action
    reason: str
    task_id: str | None = None
    branch: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class TickReport:
    """What one tick saw and did — the value ``status`` summarizes and a dry run prints.

    ``listed`` counts the items the tracker's listing returned; ``followed`` the ones read by
    identifier because a task of theirs is still in flight while the poll window no longer lists
    them. Both are handled the same way once read — the split is for the operator, who is owed the
    difference between "nothing changed" and "nothing changed, and two tasks are still watched".
    """

    listed: int
    actions: tuple[PlannedAction, ...]
    watermark: datetime | None
    followed: int = 0

    def counted(self, action: Action) -> int:
        """How many items this tick decided ``action`` for."""
        return sum(1 for planned in self.actions if planned.action is action)


def _utc_now() -> datetime:
    """The current time, timezone-aware, as the one clock the loop reads."""
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class Watcher:
    """One connector process against one tracker repository and one worc clone.

    Everything the loop touches arrives as a field — the adapter, the store, the home, the clock and
    the sleep — so the whole loop is exercised in tests without a real tracker, a real wait or a
    second process.
    """

    config: ConnectorConfig
    adapter: TrackerAdapter
    store: StateStore
    home: ConnectorHome
    worc: WorcCommand
    on_tick: Callable[[TickReport], None] = lambda report: None
    now_fn: Callable[[], datetime] = _utc_now
    sleep_fn: Callable[[float], None] = time.sleep

    @property
    def _retrigger(self) -> Retrigger:
        """The re-trigger bookkeeping, over this watcher's adapter, store and clock."""
        return Retrigger(adapter=self.adapter, store=self.store, now_fn=self.now_fn)

    def run(self, *, once: bool, dry_run: bool) -> int:
        """Run a single pass or the daemon loop; returns the process exit code."""
        if once:
            return self._single_pass(dry_run=dry_run)
        return self._daemon(dry_run=dry_run)

    def tick(self, *, dry_run: bool) -> TickReport:
        """One pass over the items the tracker reports as changed.

        Raises :class:`~worc_connect.trackers.base.TrackerError` when the tracker cannot be read
        and :class:`~worc_connect.core.worc_cli.WorcUnavailable` when worc cannot be: the caller
        decides whether that ends the process or costs one tick. Either way no row is moved on a
        guess — a step that cannot read its source leaves the row where it was, and the next tick
        is the retry.
        """
        now = self.now_fn()
        listed = self.adapter.list_items(self._listing_floor())
        items = [*listed, *self._followed(listed)]
        reconciler = Reconciler(
            config=self.config,
            store=self.store,
            worc=self.worc,
            adapter=self.adapter,
            home=self.home,
        )
        write_back = WriteBack(config=self.config, adapter=self.adapter)
        actions = tuple(
            self._plan(item, reconciler=reconciler, write_back=write_back, dry_run=dry_run, now=now)
            for item in items
        )
        # The watermark moves on what the listing saw and nothing else: an item read by identifier
        # was outside the window, and letting its stamp move the window would make the rows the
        # connector follows decide what the tracker is asked for next.
        watermark = self._advance_watermark(listed, dry_run=dry_run)
        report = TickReport(
            listed=len(listed),
            actions=actions,
            watermark=watermark,
            followed=len(items) - len(listed),
        )
        if not dry_run:
            self._note_tick(now, f"ok listed={report.listed} staged={report.counted(Action.STAGE)}")
        return report

    def _single_pass(self, *, dry_run: bool) -> int:
        """One tick; an unreachable tracker or worc ends the run rather than becoming a retry."""
        try:
            report = self.tick(dry_run=dry_run)
        except (TrackerError, WorcUnavailable) as exc:
            self._note_failure(exc, dry_run=dry_run)
            return _EXIT_FAILED
        self.on_tick(report)
        return _EXIT_OK

    def _daemon(self, *, dry_run: bool) -> int:
        """Tick until the stop sentinel appears, holding the clone's PID file while it runs.

        A dry run claims no PID file: it promises to write nothing at all, and one watcher's promise
        not to write must not turn into a lock on the next one.
        """
        control = ProcessControl(home=self.home, now_fn=self.now_fn, sleep_fn=self.sleep_fn)
        tracked = not dry_run
        if tracked and not control.claim():
            return _EXIT_FAILED
        try:
            while not control.stop_requested():
                self._guarded_tick(dry_run=dry_run)
                if not control.sleep(float(self.config.poll_interval_seconds)):
                    break
        finally:
            if tracked:
                control.release()
        return _EXIT_OK

    def _guarded_tick(self, *, dry_run: bool) -> None:
        """One tick whose infrastructure failure is logged and retried on the next one."""
        try:
            report = self.tick(dry_run=dry_run)
        except (TrackerError, WorcUnavailable) as exc:
            self._note_failure(exc, dry_run=dry_run)
            return
        self.on_tick(report)

    def _listing_floor(self) -> datetime | None:
        """The timestamp to list from: the watermark less the overlap, or everything open."""
        watermark = read_watermark(self.store)
        return None if watermark is None else watermark - WATERMARK_OVERLAP

    def _followed(self, listed: Sequence[WorkItem]) -> list[WorkItem]:
        """The items with a task still in flight that the listing left out, read one by one.

        Between the queue and the pull request the connector's own writes are the last thing that
        touched an item, and one comment on any other item moves the poll window past it for good;
        a row that is never listed again would never be advanced. So every item with a live row is
        read by identifier when the listing did not return it — an item closed by hand included,
        since its task is still worc's. An item that cannot be read costs its row one tick, not the
        whole tick: the row stays where it was, and the next tick asks again.
        """
        seen = {item.identifier for item in listed}
        followed: list[WorkItem] = []
        for row in self.store.live_rows(self.config.tracker):
            if row.item_id in seen:
                continue
            try:
                followed.append(self.adapter.get_item(row.item_id))
            except TrackerError as exc:
                logger.warning(
                    "item=%s task=%s action=fetch result=skipped-%s (%s)",
                    row.item_id,
                    row.task_id or "-",
                    type(exc).__name__,
                    exc,
                )
        return followed

    def _plan(
        self,
        item: WorkItem,
        *,
        reconciler: Reconciler,
        write_back: WriteBack,
        dry_run: bool,
        now: datetime,
    ) -> PlannedAction:
        """Decide — and, unless this is a dry run, carry out — what happens to one item."""
        verdict = gate.evaluate(item, self.config.gate)
        row = self._known_row(item, reconciler=reconciler, dry_run=dry_run, now=now)
        if row is not None and not Retrigger.is_new_request(row, item, admitted=verdict.admitted):
            return self._follow(
                item,
                row,
                work=(reconciler, write_back),
                admitted=verdict.admitted,
                dry_run=dry_run,
                now=now,
            )
        if not verdict.admitted:
            _log(item, None, Action.SKIP, str(verdict.reason))
            return PlannedAction(item.identifier, item.url, Action.SKIP, str(verdict.reason))
        seq = 1 if row is None else row.seq + 1
        branch = row.branch if row is not None and row.phase is Phase.PR_OPEN else None
        return self._take_on(
            item,
            attempt=(seq, branch),
            reason=str(verdict.reason),
            work=(reconciler, write_back),
            dry_run=dry_run,
            now=now,
        )

    def _known_row(
        self, item: WorkItem, *, reconciler: Reconciler, dry_run: bool, now: datetime
    ) -> ItemRow | None:
        """The row for ``item``, rebuilt from what the item itself shows when the cache has none.

        The state label on the item is the visible state machine, so an item already carrying one
        is an item the connector has handled — whatever became of its database. Rebuilding rather
        than starting over is what stops a deleted cache from producing a second task.
        """
        row = self.store.latest_row(self.config.tracker, item.identifier)
        if row is not None or dry_run:
            return row
        state = self.adapter.current_state(item)
        return None if state is None else reconciler.rebuild(item, state, now=now)

    def _take_on(
        self,
        item: WorkItem,
        *,
        attempt: tuple[int, str | None],
        reason: str,
        work: tuple[Reconciler, WriteBack],
        dry_run: bool,
        now: datetime,
    ) -> PlannedAction:
        """Record one attempt at ``item`` and hand it over, or describe that for a dry run.

        ``attempt`` is the sequence number and, when the previous attempt's pull request is still
        open, the branch to continue: worc then appends to that request instead of opening a
        second one for the same item.
        """
        seq, branch_ref = attempt
        row = self._new_row(item, seq=seq, branch=branch_ref, now=now)
        if dry_run:
            # The builder is pure, so the plan can name the id and the branch the real tick would
            # allocate without anything being written anywhere — the triage task's, where the
            # analysis step is on, because that is the task this tick would actually queue.
            draft = (
                builder.build_research(item, self.config, seq=seq)
                if self.config.research.in_worc
                else builder.build(item, self.config, seq=seq, branch_ref=branch_ref)
            )
            _log(item, draft.task_id, Action.STAGE, reason)
            return PlannedAction(
                item.identifier,
                item.url,
                Action.STAGE,
                reason,
                draft.task_id,
                draft.branch,
                str(PHASE_STATES[Phase.QUEUED]),
            )
        self.store.save(row)
        return self._carry_out(item, row, work=work, action=Action.STAGE, reason=reason, now=now)

    def _follow(
        self,
        item: WorkItem,
        row: ItemRow,
        *,
        work: tuple[Reconciler, WriteBack],
        admitted: bool,
        dry_run: bool,
        now: datetime,
    ) -> PlannedAction:
        """Follow an item that already has a row; its task belongs to worc from here on.

        A trigger label taken off afterwards does not withdraw the task — it is worc's now, and
        cancelling a run from a label would make a queue an operator cannot reason about. What the
        withdrawal does do is arm the next re-application of the label as a new request — provided
        the row has reached a phase a new request can start from by then. A label put back while
        the task still runs undoes the withdrawal instead: it is a correction, and it must not turn
        into a second task the moment this one ends.
        """
        if not admitted:
            _log(item, row.task_id, Action.FOLLOW, "gate-withdrawn")
            row = self._retrigger.withdrawn(row, dry_run=dry_run, now=now)
        else:
            row = self._retrigger.restored(row, dry_run=dry_run, now=now)
        if dry_run:
            return self._planned(item, row, Action.FOLLOW, str(row.phase))
        return self._carry_out(
            item, row, work=work, action=Action.FOLLOW, reason=str(row.phase), now=now
        )

    def _carry_out(
        self,
        item: WorkItem,
        row: ItemRow,
        *,
        work: tuple[Reconciler, WriteBack],
        action: Action,
        reason: str,
        now: datetime,
    ) -> PlannedAction:
        """Advance the row against worc and the pull request, then show the result on the item.

        The write-back runs last and only on what the reconcile concluded, so the item never shows
        a state the connector has not actually reached.
        """
        reconciler, write_back = work
        moved = reconciler.advance(row, item, now=now)
        published = write_back.publish(moved, item)
        if published is Published.CLOSED:
            moved = self._retrigger.closed(moved, now=now)
        elif published is Published.SHOWN and moved.phase is Phase.NEEDS_INFO:
            moved = self._retrigger.question_posted(moved, item, now=now)
        _log(item, moved.task_id, action, reason if action is Action.STAGE else str(moved.phase))
        return self._planned(
            item, moved, action, reason if action is Action.STAGE else str(moved.phase)
        )

    @staticmethod
    def _planned(item: WorkItem, row: ItemRow, action: Action, reason: str) -> PlannedAction:
        """One item's outcome, carrying the identifiers and the state it is shown in."""
        state = PHASE_STATES.get(row.phase)
        return PlannedAction(
            item.identifier,
            item.url,
            action,
            reason,
            row.task_id,
            row.branch,
            None if state is None else str(state),
        )

    def _new_row(self, item: WorkItem, *, seq: int, branch: str | None, now: datetime) -> ItemRow:
        """The row an attempt at an admitted item starts from.

        A branch already on a brand-new row is not a leftover: it is the previous attempt's, and it
        tells the builder to continue that branch and its pull request.
        """
        return ItemRow(
            tracker=self.config.tracker,
            item_id=item.identifier,
            seq=seq,
            phase=Phase.GATED,
            created_at=now,
            updated_at=now,
            branch=branch,
            item_updated_at=item.updated_at,
            stage=Stage.RESEARCH if self.config.research.in_worc else Stage.IMPLEMENTATION,
        )

    def _advance_watermark(self, items: Sequence[WorkItem], *, dry_run: bool) -> datetime | None:
        """Move the watermark to the newest update this tick saw, once the tick has handled it.

        Recorded after the items are handled, never before: a crash mid-tick leaves the old
        watermark, and the items are listed again on the next one.
        """
        current = read_watermark(self.store)
        if not items:
            return current
        newest = max(item.updated_at for item in items)
        if current is not None and newest <= current:
            return current
        if not dry_run:
            write_watermark(self.store, newest)
        return newest

    def _note_tick(self, now: datetime, result: str) -> None:
        """Record when the last tick ran and how it ended, for ``worc-connect status``."""
        self.store.set_meta(META_LAST_TICK_AT, now.astimezone(UTC).isoformat())
        self.store.set_meta(META_LAST_TICK_RESULT, result)

    def _note_failure(self, exc: Exception, *, dry_run: bool) -> None:
        """Log an infrastructure failure by class and leave every row untouched."""
        logger.warning("item=- task=- action=list result=skipped-%s (%s)", type(exc).__name__, exc)
        if not dry_run:
            self._note_tick(self.now_fn(), f"skipped {type(exc).__name__}")


def _log(item: WorkItem, task_id: str | None, action: Action, result: str) -> None:
    """One line per action, carrying only the identifiers — never a title, a body or an author."""
    logger.info(
        "item=%s task=%s action=%s result=%s", item.identifier, task_id or "-", action, result
    )
