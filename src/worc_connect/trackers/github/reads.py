"""The GitHub adapter's reads: issues, their replies, pull requests, and the label vocabulary.

Nothing above this package knows that GitHub calls a work item an issue, that its identifier is a
number, or that a state is a label. This module is the reading half of that mapping — every call
that turns GitHub into a normalized item, reply or pull request — and :mod:`.adapter` is the
writing half.

The identifier is proven to be a number before it reaches an argument list, which is what makes it
the one item-derived value the connector ever passes to a command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from worc_connect.core.items import (
    ItemHeadline,
    ItemState,
    PullRequest,
    PullRequestState,
    Reply,
    WorkItem,
    WorkItemState,
)
from worc_connect.trackers.base import TrackerUnavailable
from worc_connect.trackers.github.gh import GhCommand
from worc_connect.trackers.github.payloads import (
    entries,
    headlines,
    pull_request,
    replies,
    search_stamp,
    work_item,
)

# The fields every issue read asks for. `state` is requested only where it can differ: a listing is
# pinned to open issues, so asking the listing for it would be one more field to keep in step.
_ISSUE_FIELDS: Final = "number,title,body,author,labels,updatedAt,url"
_ISSUE_VIEW_FIELDS: Final = f"{_ISSUE_FIELDS},state"
_PULL_REQUEST_FIELDS: Final = "number,url,state,mergedAt"
# Read only when a reporter's answer starts the next triage run, never on the listing: a listing
# that carried every comment of every open issue would cost a page of comments per item per tick.
_COMMENT_FIELDS: Final = "comments"
_HEADLINE_FIELDS: Final = "number,title"

# One page per tick. `gh` defaults to 30, which would silently drop items on a busy repository; the
# listing is ordered oldest-update-first, so even a page this cap truncates leaves a contiguous run
# of updates behind it and the watermark walks forward without skipping anything.
_ISSUE_PAGE_LIMIT: Final = 200

# How many open issues a triage run is shown to recognise a duplicate among. `gh issue list` orders
# them newest first, so a repository with more than this keeps its most recent ones in view — the
# ones a fresh report is likeliest to repeat.
_HEADLINE_PAGE_LIMIT: Final = 200

# A branch can carry a handful of pull requests over its life (one merged, one reopened). Enough to
# see them all, small enough that a surprising answer stays readable.
_PULL_REQUEST_PAGE_LIMIT: Final = 10

# The label suffixes this adapter recognises as a connector state. Built once: a label under the
# prefix that is not one of them is somebody else's, and reading it as a state would let a hand
# written label drive the connector.
_STATE_NAMES: Final = {str(state) for state in ItemState}

# GitHub's search orders by relevance unless told otherwise, and the watermark depends on the order:
# the loop advances it to the newest update in the page, which is only safe if a truncated page can
# have cut off the newer end alone.
_SORT_OLDEST_UPDATE_FIRST: Final = "sort:updated-asc"

# The pull-request number inside a GitHub pull-request URL. Matched rather than parsed as a URL
# because the only thing wanted out of it is the number, and the number is what may reach an
# argument list — the URL itself never does.
_PULL_REQUEST_URL: Final = re.compile(r"/pull/(\d+)(?:[/?#]|$)")

# GitHub's own closing keyword. Written into the task's `references`, which worc appends to the
# pull-request body verbatim; the keyword is this adapter's knowledge and worc learns none of it.
_CLOSING_KEYWORD: Final = "Fixes"


@dataclass(frozen=True)
class GitHubReads:
    """The GitHub reads the core performs, over the operator's own ``gh`` login."""

    command: GhCommand
    labels_prefix: str

    def list_items(self, since: datetime | None) -> list[WorkItem]:
        """The repository's open issues updated at or after ``since``, oldest update first."""
        search = _SORT_OLDEST_UPDATE_FIRST
        if since is not None:
            search = f"updated:>={search_stamp(since)} {search}"
        payload = self.command.read_json(
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(_ISSUE_PAGE_LIMIT),
            "--json",
            _ISSUE_FIELDS,
            "--search",
            search,
        )
        return [work_item(entry, state=WorkItemState.OPEN) for entry in entries(payload)]

    def get_item(self, identifier: str) -> WorkItem:
        """One issue by number, open or closed.

        The identifier is proven to be a number before it reaches the argument list. It is the one
        item-derived value the connector ever passes to a command, and a number cannot be read as a
        flag, a path or a search expression whatever the tool's parser does with it.
        """
        payload = self.command.read_json(
            "issue", "view", issue_number(identifier), "--json", _ISSUE_VIEW_FIELDS
        )
        if not isinstance(payload, dict):
            raise TrackerUnavailable("`gh issue view` returned no issue object")
        return work_item(payload, state=None)

    def replies(self, identifier: str, *, since: datetime) -> list[Reply]:
        """The issue's comments posted after ``since``, by number, for the next triage run."""
        payload = self.command.read_json(
            "issue", "view", issue_number(identifier), "--json", _COMMENT_FIELDS
        )
        return replies(payload, since=since)

    def open_items(self) -> list[ItemHeadline]:
        """The repository's open issues by number and title, newest first, for a triage run."""
        payload = self.command.read_json(
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(_HEADLINE_PAGE_LIMIT),
            "--json",
            _HEADLINE_FIELDS,
        )
        return headlines(payload)

    def find_pull_request(self, branch: str, *, url: str | None = None) -> PullRequest | None:
        """The pull request the task opened, by the URL worc recorded or by the branch.

        worc's own record wins: it names one request exactly, and it is still true after a squash
        merge deleted the head, which is the case a branch query cannot answer at all. The URL
        never reaches an argument list — the number is read out of it and is a number by the time
        it does, exactly as an issue identifier is.

        Falling back to the branch, an open request wins over a closed one when the branch carries
        both, because that is the one the task is still working through; the caller stores its
        number and stops searching by branch, which is what keeps the follow-through alive after
        the branch is deleted.
        """
        recorded = _pull_request_number(url)
        if recorded is not None:
            return self.get_pull_request(recorded)
        payload = self.command.read_json(
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--limit",
            str(_PULL_REQUEST_PAGE_LIMIT),
            "--json",
            _PULL_REQUEST_FIELDS,
        )
        requests = [pull_request(entry) for entry in entries(payload)]
        if not requests:
            return None
        return next(
            (request for request in requests if request.state is PullRequestState.OPEN),
            requests[0],
        )

    def get_pull_request(self, number: int) -> PullRequest:
        """One pull request by number, however the owner has edited it since.

        The number is the connector's own stored value, never anything an item wrote, and reading
        by it is what survives a retitled request, a squash merge and a deleted branch.
        """
        payload = self.command.read_json("pr", "view", str(number), "--json", _PULL_REQUEST_FIELDS)
        if not isinstance(payload, dict):
            raise TrackerUnavailable("`gh pr view` returned no pull-request object")
        return pull_request(payload)

    def current_state(self, item: WorkItem) -> ItemState | None:
        """The connector-owned state the issue currently carries, read from its own labels.

        An unknown label under the prefix reads as no state at all, which is the fail-closed
        direction: the connector then publishes the state it believes in rather than trusting a
        name it does not recognise.
        """
        for label in item.labels:
            if label.startswith(self.labels_prefix):
                name = label[len(self.labels_prefix) :]
                if name in _STATE_NAMES:
                    return ItemState(name)
        return None

    def closing_reference(self, item: WorkItem) -> str | None:
        """The line that makes GitHub close this issue when the pull request is merged.

        Carried in the task's ``references`` and published by worc into the pull-request body
        verbatim, which is why the number is proven here: the one value of the item's that reaches
        a body worc writes is a number, and a number can say nothing but which issue it names.
        """
        return f"{_CLOSING_KEYWORD} #{issue_number(item.identifier)}"

    def _label_for(self, state: ItemState) -> str:
        """The label name this tracker publishes ``state`` as."""
        return f"{self.labels_prefix}{state}"


def _pull_request_number(url: str | None) -> int | None:
    """The request number inside a GitHub pull-request URL, or ``None`` for anything else.

    A shape this adapter does not recognise answers ``None`` rather than a guess: the branch query
    is the fallback, and a number invented out of an unfamiliar URL would read somebody else's
    pull request.
    """
    if url is None:
        return None
    found = _PULL_REQUEST_URL.search(url)
    return None if found is None else int(found.group(1))


def issue_number(identifier: str) -> str:
    """``identifier`` proven to be an issue number before it reaches an argument list.

    The one item-derived value the connector passes to a command, and a number cannot be read as a
    flag, a path or a search expression by any parser.
    """
    if not identifier.isdigit():
        raise TrackerUnavailable(f"not a GitHub issue number: {identifier!r}")
    return identifier
