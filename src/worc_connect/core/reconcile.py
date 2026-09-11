"""Moving one item's row forward, from the truth on disk, in worc's listing and on the code host.

The row is a cache; this module is where it is corrected. Every tick it re-reads the three sources
that actually know — the lifecycle folders in the clone, ``worc list --format json --all``, and the
pull request the task opened — and moves the row to match. That is what makes a deleted database, a
crash between writing a task file and promoting it, and a connector restarted mid-run all cost
exactly one tick.

Nothing here interprets worc's result beyond the vocabulary worc publishes, and nothing re-runs it.
A task that is queued, running or finished is worc's; the connector follows it and never edits,
re-queues or reruns it. Recovery is the operator's, on the host.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core import builder, handoff, pullrequest, triage
from worc_connect.core.items import ItemState, WorkItem
from worc_connect.core.naming import allocate_branch, allocate_task_id
from worc_connect.core.sanitize import sanitize_title
from worc_connect.core.state import TERMINAL_PHASES, ItemRow, Phase, Stage, StateStore
from worc_connect.core.triage import Report, Verdict
from worc_connect.core.worc_cli import REJECTED_STATUS, ListedTask, WorcCommand, status_token
from worc_connect.core.writeback import STATE_PHASES
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

# The status worc gives a task from the moment it published. For an implementation task that means
# "now watch the pull request"; for a triage task, which publishes nothing, it means the report is
# there to read.
_DONE_STATUS: Final = "done"

# How many attempts at one item a rebuild looks for. An item re-triggered more times than this is
# not a case worth a listing scan; the highest attempt found is what the row is rebuilt from.
_MAX_REBUILT_SEQ: Final = 50

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
    actually needs it, so a quiet tick — every row terminal, nothing staged — launches no worc at
    all.
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

    def rebuild(self, item: WorkItem, state: ItemState, *, now: datetime) -> ItemRow:
        """The row an item that already shows a connector state should have had.

        Called when the database is gone but the item is not: the state label says how far this
        item got, and worc's listing plus the queue folder say which task it got there with. The
        point is not to restore every field — it is that an item the connector already handled
        never gets a second task.
        """
        task_id, seq = self._latest_attempt(item.identifier)
        branch = None if task_id is None else self._branch_for(task_id, item)
        rebuilt = ItemRow(
            tracker=self.config.tracker,
            item_id=item.identifier,
            seq=seq,
            phase=STATE_PHASES[state],
            created_at=now,
            updated_at=now,
            task_id=task_id,
            branch=branch,
            item_updated_at=item.updated_at,
        )
        self.store.save(rebuilt)
        _log(rebuilt, "rebuild", str(state))
        return rebuilt

    def listing(self) -> dict[str, ListedTask]:
        """worc's entries for this tick, read once and reused by every row that needs them."""
        if self._listing is None:
            self._listing = self.worc.list_tasks()
        return self._listing

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
        draft = self._draft(row, item)
        handoff.stage(draft, self.config)
        staged = replace(
            row, task_id=draft.task_id, branch=draft.branch, phase=Phase.STAGED, updated_at=now
        )
        # Saved before the promote, not after: the id has to survive a crash in between, and it is
        # what the next tick re-promotes rather than re-allocating.
        self.store.save(staged)
        _log(staged, "stage", "ok")
        return staged

    def _draft(self, row: ItemRow, item: WorkItem) -> builder.TaskDraft:
        """The task this row stands for: the triage task, or the implementation task it produced.

        The report is re-read here rather than carried on the row because this is the one place a
        task file is composed, and a crash between reading a report and writing the file must cost
        a re-read rather than a task built from nothing.
        """
        if row.stage is Stage.RESEARCH:
            return builder.build_research(item, self.config, seq=row.seq)
        report = self._report(row.research_task_id)
        if report is None:
            return builder.build(
                item,
                self.config,
                seq=row.seq,
                branch_ref=row.branch,
                references=self._references(item),
            )
        return builder.build_from_report(
            item,
            self.config,
            report,
            seq=row.seq,
            branch_ref=row.branch,
            references=self._references(item),
        )

    def _report(self, task_id: str | None) -> Report | None:
        """The triage report ``task_id`` left in the connector's home, if it left a usable one."""
        if task_id is None:
            return None
        return triage.read(self.home.triage_path, task_id)

    def _references(self, item: WorkItem) -> tuple[str, ...]:
        """The closing line this task may carry, if the installed worc accepts the key at all.

        The adapter authors the line in its own tracker's keyword and worc appends it to the pull
        request it opens without interpreting it, which is how the item can be closed by the code
        host itself. A worc that does not know the key gets a task without one: an unknown
        front-matter key is a hard reject, not a warning.
        """
        if not self.worc.accepts_references():
            return ()
        line = self.adapter.closing_reference(item)
        return () if line is None else (line,)

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
        it — costs no launch at all.
        """
        if handoff.is_handed_over(self.config, task_id):
            _log(row, "promote", "already-pending")
            return replace(row, phase=Phase.QUEUED, updated_at=now)
        entry = self.listing().get(task_id)
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
        """Read worc's own status for the task and move the row to match.

        ``done`` deliberately does not end the row here: worc reports a task done from the moment
        it published, so what that status means to the connector is "now watch the pull request".
        """
        task_id = row.task_id
        if row.phase not in _FOLLOWED_PHASES or task_id is None:
            return row
        entry = self.listing().get(task_id)
        if entry is None:
            return self._unknown_to_worc(row, task_id, now=now)
        if status_token(entry.status) == REJECTED_STATUS:
            return self._refused(row, entry, now=now)
        phase = _STATUS_PHASES.get(status_token(entry.status))
        if phase is None or phase is Phase.DONE:
            # An unknown status is not guessed at either: the row keeps its phase and the next tick
            # asks again, which is the fail-closed direction for a vocabulary that may grow.
            return replace(row, last_status=entry.status, updated_at=now)
        _log(row, "follow", entry.status)
        return replace(row, phase=phase, last_status=entry.status, updated_at=now)

    def _unknown_to_worc(self, row: ItemRow, task_id: str, *, now: datetime) -> ItemRow:
        """A task worc's listing does not know: still a queued file, or gone for good.

        Gone is what worc's validation gate rejecting the task looks like from outside its private
        home — the file leaves ``pending/`` and no row is ever created — so it becomes a failure
        the operator can see on the item rather than a task the connector waits on forever.
        """
        if handoff.is_handed_over(self.config, task_id):
            return row
        _log(row, "follow", "no-task")
        return replace(row, phase=Phase.FAILED, last_status=None, updated_at=now)

    # --- the triage step -------------------------------------------------------------------------

    def _after_research(self, row: ItemRow, item: WorkItem, *, now: datetime) -> ItemRow:
        """Read the finished triage task's report and act on the verdict it carries.

        A row whose task has not finished yet is saved as it stands and returns; every other
        outcome is terminal for *this* row — a triage task is asked once, and its answer stands.
        """
        if row.phase in TERMINAL_PHASES:
            return row
        if row.last_status is None or status_token(row.last_status) != _DONE_STATUS:
            if row != self.store.latest_row(row.tracker, row.item_id):
                self.store.save(row)
            return row
        report = self._report(row.task_id)
        if report is None:
            return self._without_report(row, now=now)
        _log(row, "triage", str(report.verdict))
        if report.verdict is Verdict.ACTIONABLE:
            return self._implement(row, item, report, now=now)
        return self._concluded(row, _VERDICT_PHASES[report.verdict], _note(report), now=now)

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

    # --- rebuilding ----------------------------------------------------------------------------

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

    def _branch_for(self, task_id: str, item: WorkItem) -> str:
        """The branch that task was published from, re-derived the way it was allocated.

        Deterministic from the item's title, which is what lets a rebuilt row find the pull request
        again. A title edited since the task was created is the one case this cannot recover, and
        it costs a pull-request link, not a duplicate task.
        """
        title = sanitize_title(item.title, fallback=f"Issue #{item.identifier}")
        return allocate_branch(self.config.task.branch_prefix, task_id, title)


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
