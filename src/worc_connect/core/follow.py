"""Which items a tick reads by identifier, because a row of theirs still has somewhere to go.

The tracker's listing answers "what changed", and an item whose task is running is exactly an item
nothing is changing: between the queue and the pull request the connector's own writes are the last
thing that touched it, and one comment on any other item moves the poll window past it for good. So
every item with a row still in flight is read by identifier when the listing did not return it.

So is an item whose task failed and that worc has since taken back — the operator's ``worc rerun``.
A failed row is not in flight, and its item is often closed (GitHub closes it when the rerun's pull
request merges), so nothing else would ever look at it again. Those rows cost a tracker read only
once worc says the task is moving: the listing of worc is asked first, once per tick.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from worc_connect.core.items import WorkItem
from worc_connect.core.reconcile import awaits_rerun
from worc_connect.core.state import ItemRow, StateStore
from worc_connect.trackers.base import TrackerAdapter, TrackerError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Follower:
    """The rows of one tracker worth reading by identifier this tick, and the reading of them."""

    adapter: TrackerAdapter
    store: StateStore
    tracker: str

    def items(
        self,
        listed: Sequence[WorkItem],
        *,
        taken_back: Callable[[ItemRow], bool],
        now: datetime,
    ) -> list[WorkItem]:
        """The items with a task still in flight that the listing left out, read one by one.

        An item closed by hand is included, since its task is still worc's — and so is the item of
        a recent failure that ``taken_back`` says worc is running again. An item that cannot be
        read costs its row one tick, not the whole tick: the row stays where it was, and the next
        tick asks again.
        """
        seen = {item.identifier for item in listed}
        rows = [row for row in self.store.live_rows(self.tracker) if row.item_id not in seen]
        rows += [
            row
            for row in self.store.failed_rows(self.tracker)
            if row.item_id not in seen and awaits_rerun(row, now=now) and taken_back(row)
        ]
        return [item for item in map(self._read, rows) if item is not None]

    def _read(self, row: ItemRow) -> WorkItem | None:
        """One row's item by identifier, or ``None`` when the tracker cannot answer this tick."""
        try:
            return self.adapter.get_item(row.item_id)
        except TrackerError as exc:
            logger.warning(
                "item=%s task=%s action=fetch result=skipped-%s (%s)",
                row.item_id,
                row.task_id or "-",
                type(exc).__name__,
                exc,
            )
            return None
