"""Following the pull request a task opened, for as long as it exists and whatever happens to it.

A published pull request belongs to the person who owns the repository, not to the connector. They
may push more commits to its branch, retitle it, close it, reopen it, merge it with any strategy
and delete the branch afterwards — and the item must still be closed and the task still followed.

Two properties make that hold, and both live here. The request is found **once**, by the branch the
connector itself named, and read by its **number** ever after, so a deleted branch and a new title
change nothing. And every phase derived from it is **recomputed from its live state on every tick**,
never frozen: a request closed without a merge makes the row fail, and the same request reopened
puts the row back.

The connector never pushes. It only looks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from worc_connect.core.items import PullRequest, PullRequestState
from worc_connect.core.state import ItemRow, Phase
from worc_connect.trackers.base import TrackerAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PullRequestWatcher:
    """The pull-request half of a reconcile, for one tracker."""

    adapter: TrackerAdapter

    def advance(self, row: ItemRow, *, now: datetime) -> ItemRow:
        """Recompute ``row``'s phase from the pull request it is waiting on, if there is one."""
        if row.pr_number is not None:
            return _from_live(row, self.adapter.get_pull_request(row.pr_number), now=now)
        if not _is_looking(row) or row.branch is None:
            return row
        found = self.adapter.find_pull_request(row.branch)
        if found is None:
            # worc has finished and opened nothing. The branch query is answered from the pull
            # requests themselves rather than from a search index, so "none" is an answer and not a
            # lag: the row ends here, and nothing is closed, because nothing was merged.
            return replace(row, phase=Phase.DONE, updated_at=now) if _is_finished(row) else row
        stored = replace(row, pr_number=found.number, pr_url=found.url)
        logger.info(
            "item=%s task=%s action=pull-request result=found-%s",
            row.item_id,
            row.task_id or "-",
            found.number,
        )
        return _from_live(stored, found, now=now)


def _is_looking(row: ItemRow) -> bool:
    """Whether this row should be searching for its pull request.

    Either worc has finished the task — which is when the request exists, since worc reports a task
    done from the moment it published — or the row was rebuilt from an item already showing that a
    request is open, and has to find the number it lost with the database.
    """
    return _is_finished(row) or row.phase is Phase.PR_OPEN


def _is_finished(row: ItemRow) -> bool:
    """Whether worc's own listing has reported this task finished."""
    return row.phase is Phase.DONE or row.last_status == "done"


def _from_live(row: ItemRow, request: PullRequest, *, now: datetime) -> ItemRow:
    """The row as the pull request's current state makes it — computed fresh, never carried over."""
    phase = _phase_of(request)
    return replace(
        row,
        phase=phase,
        pr_number=request.number,
        pr_url=request.url,
        pr_merged=request.merged_at is not None or request.state is PullRequestState.MERGED,
        updated_at=now,
    )


def _phase_of(request: PullRequest) -> Phase:
    """The phase a pull request in this state puts the item in."""
    if request.merged_at is not None or request.state is PullRequestState.MERGED:
        return Phase.DONE
    if request.state is PullRequestState.CLOSED:
        return Phase.FAILED
    return Phase.PR_OPEN
