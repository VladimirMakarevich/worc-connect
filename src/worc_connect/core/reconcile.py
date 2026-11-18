"""Moving one item's row forward, from the truth on disk, in worc's listing and on the code host.

The row is a cache; this module is where it is corrected. Every tick it re-reads the three sources
that actually know — the lifecycle folders in the clone, ``worc list --format json --all``, and the
pull request the task opened — and moves the row to match. That is what makes a deleted database, a
crash between writing a task file and promoting it, and a connector restarted mid-run all cost
exactly one tick.

Nothing here interprets worc's result beyond the vocabulary worc publishes, and nothing re-runs it.
A task that is queued, running or finished is worc's; the connector follows it and never edits,
re-queues or reruns it. Recovery is the operator's, on the host — and a task the operator reran
after a failure is followed again, for a bounded while, rather than left showing the failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import cached_property
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core import handoff, pullrequest, triage
from worc_connect.core.drafting import Drafter
from worc_connect.core.items import ItemState, WorkItem
from worc_connect.core.rebuild import Rebuilder
from worc_connect.core.state import TERMINAL_PHASES, ItemRow, Phase, Stage, StateStore
from worc_connect.core.triage import Report, Verdict
from worc_connect.core.worc_cli import (
    DONE_STATUS,
    REJECTED_STATUS,
    ListedTask,
    WorcCommand,
    status_token,
)
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerAdapter

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
    # An id worc's gate refused. It has no task row, so this entry is the only place outside worc's
    # private home that names it at all — and the only place its reason can be read from.
    REJECTED_STATUS: Phase.FAILED,
}

# The phases worc's own listing still has something to say about.
_FOLLOWED_PHASES: Final = frozenset({Phase.QUEUED, Phase.RUNNING})

# worc's statuses for a task that ran and ended badly — the ones `worc rerun`, the recovery the
# operator is told to use, takes back. A task worc's gate refused never ran and is not among them,
# and neither is a pull request closed unmerged: that one comes back by being reopened.
_RERUNNABLE_STATUSES: Final = frozenset({"failed", "manual_action_required"})

# The phases a rerun task shows in worc's listing once worc has taken it back.
_TAKEN_BACK_PHASES: Final = frozenset({Phase.QUEUED, Phase.RUNNING, Phase.DONE})

# How long a failed task is watched for a rerun. Long enough for an operator to come back to it
# after a weekend; bounded, so a repository's failures do not cost a `worc list` on every tick for
# ever. A rerun later than this is not seen — the item keeps `failed` until somebody relabels it.
RERUN_WINDOW: Final = timedelta(days=7)

# Where each triage verdict leaves the attempt. Only ``actionable`` produces work; the other three
# end it on the item, which is what the whole step is for — an item nobody can act on costs one
# analysis instead of an implementation run. ``duplicate`` is published as ``declined`` because that
# is the state vocabulary the tracker side owns; which duplicate it was is in the comment.
_VERDICT_PHASES: Final = {
    Verdict.ACTIONABLE: Phase.RESEARCHED,
    Verdict.NEEDS_INFO: Phase.NEEDS_INFO,
    Verdict.DUPLICATE: Phase.DECLINED,
    Verdict.DECLINED: Phase.DECLINED,
}

# What the item is told when a triage task ended but left nothing this build can read. Connector
# authored: there is no report to quote, which is exactly the problem being reported.
_NO_REPORT_NOTE: Final = (
    "the triage task finished but left no readable report at {path} — nothing was queued from it"
)


@dataclass
class Reconciler:
    """One tick's view of worc, applied to the rows the connector holds.

    Not frozen, unlike the records it moves: it holds the listing it read, which is the one piece
    of per-tick state worth keeping. The listing is fetched at most once and only when a row
    actually needs it, so a quiet tick — every row terminal, nothing staged, no failure recent
    enough to be rerun — launches no worc at all.
    """

    config: ConnectorConfig
    store: StateStore
    worc: WorcCommand
    adapter: TrackerAdapter
    home: ConnectorHome
    _listing: dict[str, ListedTask] | None = None

    def advance(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Move ``row`` as far as this tick can and persist it; returns the row as it now stands.

        A row the handoff moved this tick stops there. worc has not had a chance to record the task
        yet, so asking its listing about it would be a second launch that could only answer "I have
        never heard of it" — and the tick that hands an item over stays one ``promote`` and nothing
        else.
        """
        moved = self._hand_over(row, item, now=now)
        if moved.phase is not row.phase:
            self.store.save(moved)
            return moved
        moved = self._follow_task(moved, now=now)
        if moved.stage is Stage.RESEARCH:
            # A triage task publishes nothing, so there is no pull request to watch: what it leaves
            # behind is a report, and reading it is what moves this row on.
            return self._after_research(moved, item, now=now)
        moved = pullrequest.PullRequestWatcher(adapter=self.adapter).advance(
            moved, url=self._recorded_pr_url(moved), now=now
        )
        if moved != row:
            self.store.save(moved)
        return moved

    def rebuild(
        self, item: WorkItem, state: ItemState, *, now: datetime, persist: bool = True
    ) -> ItemRow | None:
        """The row an item that already shows a connector state should have had, or ``None``.

        Called when the database is gone but the item is not; see :mod:`.rebuild`. ``persist`` is
        off for a dry run, which plans from the row the real tick would rebuild without writing it.
        """
        return Rebuilder(
            config=self.config, store=self.store, home=self.home, listing=self.listing
        ).rebuild(item, state, now=now, persist=persist)

    @cached_property
    def _drafter(self) -> Drafter:
        """The task-file composer for this tick, holding what it read once for every row."""
        return Drafter(
            config=self.config,
            store=self.store,
            worc=self.worc,
            adapter=self.adapter,
            home=self.home,
        )

    def listing(self) -> dict[str, ListedTask]:
        """worc's entries for this tick, read once and reused by every row that needs them."""
        if self._listing is None:
            self._listing = self.worc.list_tasks()
        return self._listing

    def heard(self, task_id: str | None) -> str | None:
        """worc's status for ``task_id`` as this tick's listing gave it, or ``None`` if not asked.

        What the item's log line quotes as worc's own word. A status the row cached on an earlier
        tick is not worc's answer now, and printing it as one is how a rerun went unseen.
        """
        if task_id is None or self._listing is None:
            return None
        entry = self._listing.get(task_id)
        return None if entry is None else entry.status

    def taken_back(self, row: ItemRow) -> bool:
        """Whether worc lists ``row``'s failed task as queued, running or done again — a rerun."""
        entry = None if row.task_id is None else self.listing().get(row.task_id)
        return entry is not None and phase_for_status(entry.status) in _TAKEN_BACK_PHASES

    # --- the handoff ---------------------------------------------------------------------------

    def _hand_over(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Get the task into worc, from wherever the previous tick left it."""
        if row.phase is Phase.GATED:
            row = self._stage(row, item, now=now)
        if row.phase is Phase.STAGED:
            row = self._promote(row, now=now)
        return row

    def _stage(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Compose the task file and write it into worc's staging directory.

        A branch already on the row means this attempt continues the previous one's pull request
        rather than opening a second one, so the task is built with that branch as its ref.
        """
        draft = self._drafter.draft(row, item)
        if row.stage is Stage.RESEARCH:
            # Marked before the file exists, so no crash can leave a promoted triage task without
            # the one signal a rebuilt row has that it is following a triage task.
            triage.mark_triage_task(self.home.triage_path, draft.task_id)
        handoff.stage(draft, self.config)
        staged = replace(
            row, task_id=draft.task_id, branch=draft.branch, phase=Phase.STAGED, updated_at=now
        )
        # Saved before the promote, not after: the id has to survive a crash in between, and it is
        # what the next tick re-promotes rather than re-allocating.
        self.store.save(staged)
        _log(staged, "stage", "ok")
        return staged

    def _report(self, task_id: str | None) -> Report | None:
        """The triage report ``task_id`` left in the connector's home, if it left a usable one."""
        if task_id is None:
            return None
        return triage.read(self.home.triage_path, task_id)

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
        _log(row, "promote", str(outcome))
        return replace(row, phase=Phase.QUEUED, updated_at=now)

    def _resolve_missing(self, row: ItemRow, task_id: str, *, now: datetime) -> ItemRow:
        """Decide what a staged file that is no longer staged means.

        The file on disk is consulted before worc's listing so that the common answer — worc holds
        it — costs no launch at all; and the listing that decides the rest is read after the disk,
        so a file worc claimed between the two reads is worc's and not a task that vanished.
        """
        if handoff.is_handed_over(self.config, task_id):
            _log(row, "promote", "already-pending")
            return replace(row, phase=Phase.QUEUED, updated_at=now)
        entry = self._relisted().get(task_id)
        if entry is None:
            _log(row, "promote", "no-task")
            return replace(row, phase=Phase.FAILED, last_status=None, updated_at=now)
        if status_token(entry.status) == REJECTED_STATUS:
            return self._refused(row, entry, now=now)
        _log(row, "promote", "already-pending")
        return replace(row, phase=Phase.QUEUED, updated_at=now)

    def _refused(self, row: ItemRow, entry: ListedTask, *, now: datetime) -> ItemRow:
        """worc's gate refused the task: terminal, and carrying the reason worc published for it."""
        _log(row, "follow", REJECTED_STATUS)
        return replace(
            row,
            phase=Phase.FAILED,
            last_status=entry.status,
            validation_reason=entry.validation_reason,
            updated_at=now,
        )

    # --- following worc ------------------------------------------------------------------------

    def _follow_task(self, row: ItemRow, *, now: datetime) -> ItemRow:
        """Read worc's own status for the task and move the row to match."""
        task_id = row.task_id
        if task_id is not None and awaits_rerun(row, now=now):
            return self._rerun(row, task_id, now=now)
        if row.phase not in _FOLLOWED_PHASES or task_id is None:
            return row
        entry = self.listing().get(task_id)
        if entry is None:
            return self._unknown_to_worc(row, task_id, now=now)
        return self._from_entry(row, entry, now=now)

    def _rerun(self, row: ItemRow, task_id: str, *, now: datetime) -> ItemRow:
        """A failed task worc lists as taken back — the operator's ``worc rerun`` — followed anew.

        The row is put back in worc's hands before worc's status is applied, so a rerun that
        already finished is read exactly as a first run that finished: its pull request looked for,
        or its report read. A task worc still lists as failed leaves the row untouched, stamp
        included, so the watch window keeps measuring from the failure.
        """
        if not self.taken_back(row):
            return row
        _log(row, "follow", "rerun")
        revived = replace(row, phase=Phase.RUNNING)
        return self._from_entry(revived, self.listing()[task_id], now=now)

    def _from_entry(self, row: ItemRow, entry: ListedTask, *, now: datetime) -> ItemRow:
        """The row as worc's own entry for its task makes it.

        ``done`` deliberately does not end the row here: worc reports a task done from the moment
        it published, so what that status means to the connector is "now watch the pull request".
        An unknown status is not guessed at either: the row keeps its phase and the next tick asks
        again, which is the fail-closed direction for a vocabulary that may grow. ``parked`` maps
        to running: worc holds the task and will run it, so nothing about the item changes.
        """
        if status_token(entry.status) == REJECTED_STATUS:
            return self._refused(row, entry, now=now)
        phase = _STATUS_PHASES.get(status_token(entry.status))
        if phase is None or phase is Phase.DONE:
            return replace(row, last_status=entry.status, updated_at=now)
        # Not logged here: the loop's own line for this item carries worc's status next to the
        # phase it maps to, and a second line per followed item per tick was noise, not news.
        return replace(row, phase=phase, last_status=entry.status, updated_at=now)

    def _unknown_to_worc(self, row: ItemRow, task_id: str, *, now: datetime) -> ItemRow:
        """A task worc's listing does not know: still a queued file, claimed since, or gone.

        Gone is what worc's validation gate rejecting the task looks like from outside its private
        home — the file leaves ``pending/`` and no row is ever created — so it becomes a failure
        the operator can see on the item rather than a task the connector waits on forever. But the
        listing may predate the look at the disk, and a file that left ``pending/`` in between is a
        task worc claimed, not one that vanished: the listing is read once more, after the disk,
        before anything terminal is concluded.
        """
        if handoff.is_handed_over(self.config, task_id):
            return row
        entry = self._relisted().get(task_id)
        if entry is not None:
            return self._from_entry(row, entry, now=now)
        _log(row, "follow", "no-task")
        return replace(row, phase=Phase.FAILED, last_status=None, updated_at=now)

    def _relisted(self) -> dict[str, ListedTask]:
        """worc's entries read again, for a conclusion the cached listing is too old to carry."""
        self._listing = self.worc.list_tasks()
        return self._listing

    # --- the triage step -------------------------------------------------------------------------

    def _after_research(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Read the finished triage task's report and act on the verdict it carries.

        A row whose task has not finished yet is saved as it stands and returns; every other
        outcome is terminal for *this* row — a triage task is asked once, and its answer stands.
        A row worc itself ended this tick — failed, refused, gone — has no report to read, and is
        saved where it ended.

        Whatever the outcome, the scratch the task left beside its report goes once the task is
        over: it sits inside the clone, where a project tool that ignores ``.gitignore`` would
        take it for the project's own code and fail every later task on it.
        """
        if row.phase in TERMINAL_PHASES:
            if row != self.store.latest_row(row.tracker, row.item_id):
                self._clear_scratch(row)
                self.store.save(row)
            return row
        if row.last_status is None or status_token(row.last_status) != DONE_STATUS:
            if row != self.store.latest_row(row.tracker, row.item_id):
                self.store.save(row)
            return row
        report = self._report(row.task_id)
        self._clear_scratch(row)
        if report is None:
            return self._without_report(row, now=now)
        _log(row, "triage", str(report.verdict))
        if report.verdict is Verdict.ACTIONABLE:
            return self._implement(row, item, report, now=now)
        return self._concluded(row, _VERDICT_PHASES[report.verdict], _note(report), now=now)

    def _clear_scratch(self, row: ItemRow) -> None:
        """Remove what the triage task left beside its report, saying so only where it could not."""
        if row.task_id is None:  # pragma: no cover - a followed triage row always has its id
            return
        misses = triage.clear_scratch(self.home.triage_path, row.task_id)
        if misses:
            logger.warning(
                "item=%s task=%s action=triage-cleanup result=left-%d",
                row.item_id,
                row.task_id,
                misses,
            )

    def _without_report(self, row: ItemRow, *, now: datetime) -> ItemRow:
        """A triage task that ended leaving nothing readable where the flow declares it writes.

        There is deliberately no second place to look: a fallback is how a connector ends up
        reading worc's private home, and the report directory is the flow's own declaration.
        """
        path = triage.report_path(self.home.triage_path, row.task_id or "")
        _log(row, "triage", "no-report")
        return self._concluded(
            row, Phase.FAILED, _NO_REPORT_NOTE.format(path=path.as_posix()), now=now
        )

    def _concluded(self, row: ItemRow, phase: Phase, note: str, *, now: datetime) -> ItemRow:
        """End this attempt in ``phase``, carrying what the item is to be told about it."""
        ended = replace(row, phase=phase, research_note=note, updated_at=now)
        self.store.save(ended)
        return ended

    def _implement(self, row: ItemRow, item: WorkItem, report: Report, *, now: datetime) -> ItemRow:
        """Turn an actionable report into the implementation task, in this same tick.

        The triage row ends in a phase the item is never shown: what the item should show now is
        the implementation task's own state, and announcing "analysed" between the two would be a
        step nobody can act on. The new attempt takes the next sequence number, so the triage task's
        id is never reused and the item's history reads as the two tasks it actually cost.
        """
        self.store.save(
            replace(row, phase=Phase.RESEARCHED, research_note=_note(report), updated_at=now)
        )
        follow_on = ItemRow(
            tracker=row.tracker,
            item_id=row.item_id,
            seq=row.seq + 1,
            phase=Phase.GATED,
            created_at=now,
            updated_at=now,
            item_updated_at=row.item_updated_at,
            stage=Stage.IMPLEMENTATION,
            research_task_id=row.task_id,
        )
        self.store.save(follow_on)
        handed = self._hand_over(follow_on, item, now=now)
        # Saved here rather than by the caller: this row was created inside the tick, so there is
        # no earlier version of it for `advance` to compare against and decide to persist.
        self.store.save(handed)
        return handed

    def _recorded_pr_url(self, row: ItemRow) -> str | None:
        """worc's own record of the pull request this task opened, read only where it is needed.

        Asked for exactly while the row still has to discover its request, which keeps the quiet
        tick quiet: a row that already holds a number, or that is not looking at all, costs no
        worc launch to establish that.
        """
        if row.task_id is None or not pullrequest.needs_discovery(row):
            return None
        entry = self.listing().get(row.task_id)
        return None if entry is None else entry.pr_url


def awaits_rerun(row: ItemRow, *, now: datetime) -> bool:
    """Whether ``row`` is a recent failure an operator may still bring back with ``worc rerun``.

    Only a failure worc itself reported qualifies — a refusal, a vanished task, a pull request
    closed unmerged and a triage report nobody could read are not undone by a rerun — and only
    inside :data:`RERUN_WINDOW`.
    """
    return (
        row.phase is Phase.FAILED
        and row.task_id is not None
        and row.pr_number is None
        and row.last_status is not None
        and status_token(row.last_status) in _RERUNNABLE_STATUSES
        and now - row.updated_at <= RERUN_WINDOW
    )


def phase_for_status(status: str) -> Phase | None:
    """The connector phase worc's ``status`` label means, or ``None`` for one it does not know."""
    return _STATUS_PHASES.get(status_token(status))


def _note(report: Report) -> str:
    """What the item is told about a triage verdict: the report's own words, as it wrote them.

    Agent prose, published to whoever reads the item. It is repeated rather than summarized because
    the reporter is the person who has to act on it — a ``needs-info`` verdict the connector
    paraphrased would be a question nobody can answer.
    """
    if report.duplicate_of is not None:
        return f"{report.reason}\n\nDuplicate of {report.duplicate_of}."
    return report.reason


def _log(row: ItemRow, action: str, result: str) -> None:
    """One line per action, carrying identifiers only."""
    logger.info(
        "item=%s task=%s action=%s result=%s", row.item_id, row.task_id or "-", action, result
    )
