"""Moving one item's row forward, from the truth on disk and in worc's listing.

The row is a cache; this module is where it is corrected. Every tick it re-reads the two sources
that actually know — the lifecycle folders in the clone and ``worc list --format json --all`` — and
moves the row to match, which is what makes a deleted database, a crash between writing a task file
and promoting it, and a connector restarted mid-run all cost exactly one tick.

Nothing here interprets worc's result beyond the vocabulary worc publishes. A task that is queued,
running or finished is worc's; the connector follows it and never edits, re-queues or re-runs it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core import builder, handoff
from worc_connect.core.items import WorkItem
from worc_connect.core.state import ItemRow, Phase, StateStore
from worc_connect.core.worc_cli import WorcCommand, status_token

logger = logging.getLogger(__name__)

# worc's task statuses, mapped to the connector's phases. The listing renders a status for humans,
# so the mapping is applied to the leading token of the label, never to the whole string.
_STATUS_PHASES: Final = {
    "new": Phase.QUEUED,
    "validated": Phase.QUEUED,
    "preparing": Phase.QUEUED,
    "pending": Phase.QUEUED,
    "running": Phase.RUNNING,
    "parked": Phase.RUNNING,
    "done": Phase.DONE,
    "failed": Phase.FAILED,
    "manual_action_required": Phase.FAILED,
}


@dataclass
class Reconciler:
    """One tick's view of worc, applied to the rows the connector holds.

    Not frozen, unlike the records it moves: it holds the listing it read, which is the one piece
    of per-tick state worth keeping. The listing is fetched at most once and only when a row
    actually needs it, so a quiet tick — every row terminal, nothing staged — launches no process
    at all.
    """

    config: ConnectorConfig
    store: StateStore
    worc: WorcCommand
    _listing: dict[str, str] | None = None

    def advance(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Move ``row`` as far as this tick can and persist it; returns the row as it now stands."""
        if row.phase is Phase.GATED:
            row = self._stage(row, item, now=now)
        if row.phase is Phase.STAGED:
            row = self._promote(row, now=now)
        return row

    def listing(self) -> dict[str, str]:
        """worc's statuses for this tick, read once and reused by every row that needs them."""
        if self._listing is None:
            self._listing = self.worc.list_tasks()
        return self._listing

    def _stage(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Compose the task file and write it into worc's staging directory."""
        draft = builder.build(item, self.config, seq=row.seq)
        handoff.stage(draft, self.config)
        staged = replace(
            row, task_id=draft.task_id, branch=draft.branch, phase=Phase.STAGED, updated_at=now
        )
        self.store.save(staged)
        _log(staged, "stage", "ok")
        return staged

    def _promote(self, row: ItemRow, *, now: datetime) -> ItemRow:
        """Hand the staged file to worc, or work out what happened to a file that is gone."""
        task_id = row.task_id
        if task_id is None:  # pragma: no cover - a staged row always carries its task id
            return row
        if not handoff.task_path(self.config, task_id).is_file():
            return self._resolve_missing(row, task_id, now=now)
        outcome = handoff.promote(task_id, self.worc)
        if outcome is handoff.PromoteOutcome.RETRY:
            return row
        queued = replace(row, phase=Phase.QUEUED, updated_at=now)
        self.store.save(queued)
        _log(queued, "promote", str(outcome))
        return queued

    def _resolve_missing(self, row: ItemRow, task_id: str, *, now: datetime) -> ItemRow:
        """Decide what a staged file that is no longer staged means.

        Three things can have happened: worc already holds it (it is in the queue folder, or the
        listing knows it), or somebody removed it. The last one is also what worc's own gate
        rejecting the task looks like from outside worc's home — the file is gone and no row exists
        — so it is reported as a failure the operator can see on the item rather than as a task the
        connector keeps waiting for.
        """
        if handoff.is_handed_over(self.config, task_id) or task_id in self.listing():
            resolved = replace(row, phase=Phase.QUEUED, updated_at=now)
            self.store.save(resolved)
            _log(resolved, "promote", "already-pending")
            return resolved
        failed = replace(row, phase=Phase.FAILED, updated_at=now)
        self.store.save(failed)
        _log(failed, "promote", "no-task")
        return failed


def phase_for_status(status: str) -> Phase | None:
    """The connector phase worc's ``status`` label means, or ``None`` for one it does not know.

    An unknown status is not guessed at: the row keeps the phase it has and the next tick asks
    again, which is the fail-closed direction for a vocabulary that may grow.
    """
    return _STATUS_PHASES.get(status_token(status))


def _log(row: ItemRow, action: str, result: str) -> None:
    """One line per action, carrying identifiers only."""
    logger.info(
        "item=%s task=%s action=%s result=%s", row.item_id, row.task_id or "-", action, result
    )
