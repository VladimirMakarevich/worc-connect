"""Rebuilding a row the cache lost, from what the item, worc's listing and the queue still show.

The row is a cache, so deleting the database is a supported operation; this module is what makes it
one. An item that already shows a connector state is an item the connector handled, and the state
label says how far it got — worc's listing plus the queue folder say which task it got there with.
The point is not to restore every field: it is that an item the connector already handled never
gets a second task.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core import handoff, triage
from worc_connect.core.items import ItemState, WorkItem
from worc_connect.core.naming import allocate_branch, allocate_task_id
from worc_connect.core.sanitize import sanitize_title
from worc_connect.core.state import ItemRow, Stage, StateStore
from worc_connect.core.worc_cli import ListedTask
from worc_connect.core.writeback import STATE_PHASES, TRIAGE_STATES
from worc_connect.home import ConnectorHome

logger = logging.getLogger(__name__)

# The states an item shows only once it was told the link to its pull request: the move into
# `pr-open` posts it, and `done` is reached through that or announces the link itself.
_ANNOUNCED_STATES: Final = frozenset({ItemState.PR_OPEN, ItemState.DONE})

# How many attempts at one item a rebuild looks for. An item re-triggered more times than this is
# not a case worth a listing scan; the highest attempt found is what the row is rebuilt from.
_MAX_REBUILT_SEQ: Final = 50


@dataclass(frozen=True)
class Rebuilder:
    """One tick's rebuild of rows the cache lost, over the reconciler's listing of worc."""

    config: ConnectorConfig
    store: StateStore
    home: ConnectorHome
    listing: Callable[[], dict[str, ListedTask]]

    def rebuild(
        self, item: WorkItem, state: ItemState, *, now: datetime, persist: bool = True
    ) -> ItemRow | None:
        """The row an item that already shows a connector state should have had, or ``None``.

        ``None`` when no task is known for the item at all: a label with nothing behind it is not
        the connector's — written by hand, or worc's own records gone — and adopting it would be
        trusting a name. ``persist`` is off for a dry run, which plans from the row the real tick
        would rebuild without writing it anywhere.
        """
        task_id, seq = self._latest_attempt(item.identifier)
        if task_id is None:
            logger.info("item=%s task=- action=rebuild result=no-task", item.identifier)
            return None
        stage = self._rebuilt_stage(task_id, state)
        rebuilt = ItemRow(
            tracker=self.config.tracker,
            item_id=item.identifier,
            seq=seq,
            phase=STATE_PHASES[state],
            created_at=now,
            updated_at=now,
            task_id=task_id,
            branch=self._branch_for(task_id, item),
            item_updated_at=item.updated_at,
            stage=stage,
            research_task_id=(
                None if stage is Stage.RESEARCH else self._triage_before(item.identifier, seq)
            ),
            # The item already shows a state, so its task was announced by the run that set it —
            # and so was its pull request, where the state is one only that announcement reaches.
            queued_announced=True,
            pr_announced=state in _ANNOUNCED_STATES,
        )
        if persist:
            self.store.save(rebuilt)
        logger.info(
            "item=%s task=%s action=rebuild result=%s", rebuilt.item_id, task_id, str(state)
        )
        return rebuilt

    def _latest_attempt(self, item_id: str) -> tuple[str | None, int]:
        """The highest-numbered task this item already has, from worc's listing and its queue."""
        known = set(self.listing()) | {
            path.stem for path in handoff.pending_dir(self.config).glob("*.md")
        }
        prefix = self.config.task.id_prefix
        found: tuple[str | None, int] = (None, 1)
        for seq in range(1, _MAX_REBUILT_SEQ + 1):
            candidate = allocate_task_id(prefix, item_id, seq)
            if candidate in known:
                found = (candidate, seq)
        return found

    def _rebuilt_stage(self, task_id: str, state: ItemState) -> Stage:
        """Which of the item's two possible tasks a rebuilt row is following.

        Only the triage step publishes ``needs-info`` and ``declined``, and only a triage task has
        a directory under the connector's own triage home — created when the task is staged, so the
        signal is there from the first tick rather than from the moment a report lands. With the
        step off neither signal is consulted: nothing of the triage path is reachable then, a
        directory an earlier run left behind included.
        """
        if not self.config.research.in_worc:
            return Stage.IMPLEMENTATION
        if state in TRIAGE_STATES or triage.is_triage_task(self.home.triage_path, task_id):
            return Stage.RESEARCH
        return Stage.IMPLEMENTATION

    def _triage_before(self, item_id: str, seq: int) -> str | None:
        """The previous attempt's id if it was a triage task: the report this one was built from."""
        if not self.config.research.in_worc or seq < 2:
            return None
        previous = allocate_task_id(self.config.task.id_prefix, item_id, seq - 1)
        return previous if triage.is_triage_task(self.home.triage_path, previous) else None

    def _branch_for(self, task_id: str, item: WorkItem) -> str:
        """The branch that task was published from, re-derived the way it was allocated.

        Deterministic from the item's title, which is what lets a rebuilt row find the pull request
        again. A title edited since the task was created is the one case this cannot recover, and
        it costs a pull-request link, not a duplicate task.
        """
        title = sanitize_title(item.title, fallback=f"Issue #{item.identifier}")
        return allocate_branch(self.config.task.branch_prefix, task_id, title)
