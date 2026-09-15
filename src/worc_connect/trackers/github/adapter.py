"""The GitHub adapter: everything the connector does to an issue, and nothing else.

Nothing above this module knows that GitHub calls a work item an issue, that its identifier is a
number, or that a state is a label. Both directions of that mapping live here: the reads that
produce a normalized item or pull request, and the writes that publish a connector state, post a
comment and close an issue.

Two disciplines are visible in the call shapes and are not negotiable. A comment body travels as a
**file**, never as an argument — an issue is written by strangers and the habit of putting text in
argv is one worth never starting. And the identifier is proven to be a number before it reaches an
argument list, which is what makes it the one item-derived value the connector ever passes to a
command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from worc_connect.core.items import (
    ItemState,
    PullRequest,
    PullRequestState,
    WorkItem,
    WorkItemState,
)
from worc_connect.trackers.base import TrackerUnavailable
from worc_connect.trackers.github.gh import GhCommand
from worc_connect.trackers.github.payloads import (
    entries,
    pull_request,
    search_stamp,
    work_item,
)

# The fields every issue read asks for. `state` is requested only where it can differ: a listing is
# pinned to open issues, so asking the listing for it would be one more field to keep in step.
_ISSUE_FIELDS: Final = "number,title,body,author,labels,updatedAt,url"
_ISSUE_VIEW_FIELDS: Final = f"{_ISSUE_FIELDS},state"
_PULL_REQUEST_FIELDS: Final = "number,url,state,mergedAt"

# One page per tick. `gh` defaults to 30, which would silently drop items on a busy repository; the
# listing is ordered oldest-update-first, so even a page this cap truncates leaves a contiguous run
# of updates behind it and the watermark walks forward without skipping anything.
_ISSUE_PAGE_LIMIT: Final = 200

# A branch can carry a handful of pull requests over its life (one merged, one reopened). Enough to
# see them all, small enough that a surprising answer stays readable.
_PULL_REQUEST_PAGE_LIMIT: Final = 10

# Repositories rarely carry more than a few dozen labels, and the listing is read once per process
# to find which of the connector's own are missing. The cap is an optimisation, not a limit: a label
# the capped listing hid is created anyway, and the tracker's "already exists" is read as present.
_LABEL_PAGE_LIMIT: Final = 200

# What a label the connector created says about itself. Fixed text, because a label description is
# repository furniture an operator may edit freely afterwards. The trigger label gets its own: it is
# the one maintainers apply by hand, so it should say what applying it does.
_LABEL_DESCRIPTION: Final = "Managed by worc-connect"
_TRIGGER_DESCRIPTION: Final = "Hands this issue to worc (worc-connect trigger label)"

# How `gh label create` reports a name that is already taken. Read as the answer it is rather than
# as a failure: a label that exists is exactly the state wanted, and treating it as an error would
# abort every tick for good on a repository whose label listing the cap truncated.
_ALREADY_EXISTS: Final = "already exists"

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
class GitHubAdapter:
    """The GitHub calls the core performs, over the operator's own ``gh`` login."""

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
            "issue", "view", _number(identifier), "--json", _ISSUE_VIEW_FIELDS
        )
        if not isinstance(payload, dict):
            raise TrackerUnavailable("`gh issue view` returned no issue object")
        return work_item(payload, state=None)

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

    def set_state(self, identifier: str, state: ItemState, *, previous: ItemState | None) -> None:
        """Move the issue to ``state`` in one edit, so it never carries two states or none."""
        arguments = ["issue", "edit", _number(identifier)]
        if previous is not None and previous is not state:
            arguments += ["--remove-label", self._label_for(previous)]
        arguments += ["--add-label", self._label_for(state)]
        self.command.run(*arguments)

    def comment(self, identifier: str, body_path: Path) -> None:
        """Post a comment whose body is read from a file, never composed into an argument."""
        self.command.run("issue", "comment", _number(identifier), "--body-file", str(body_path))

    def close(self, identifier: str, message: str) -> None:
        """Close the issue with a connector-authored closing message.

        ``gh issue close`` has no body-file form, so the message is an argument here. It is safe to
        be one for the same reason the label names are: it is a template this package wrote, and
        the only values interpolated into it are a task id and a URL.
        """
        self.command.run("issue", "close", _number(identifier), "--comment", message)

    def ensure_labels(self, states: tuple[ItemState, ...], *, triggers: tuple[str, ...]) -> None:
        """Create the labels the repository is missing, and leave the ones it has alone.

        Existing labels are never edited: their colour and description belong to whoever set them
        up, and a connector that reset them on every start would be fighting the maintainers. The
        trigger labels are configuration the operator wrote, which is what allows their names in an
        argument list at all. Names compare case-insensitively, as GitHub keeps them unique.
        """
        payload = self.command.read_json(
            "label", "list", "--limit", str(_LABEL_PAGE_LIMIT), "--json", "name"
        )
        present = {
            entry["name"].casefold()
            for entry in entries(payload)
            if isinstance(entry.get("name"), str)
        }
        wanted = [(self._label_for(state), _LABEL_DESCRIPTION) for state in states]
        wanted += [(name, _TRIGGER_DESCRIPTION) for name in triggers]
        for name, description in wanted:
            if name.casefold() not in present:
                self._create_label(name, description)

    def _create_label(self, name: str, description: str) -> None:
        """Create one label, reading "already exists" as the label being there after all."""
        try:
            self.command.run("label", "create", name, "--description", description)
        except TrackerUnavailable as exc:
            if _ALREADY_EXISTS not in str(exc).casefold():
                raise

    def closing_reference(self, item: WorkItem) -> str | None:
        """The line that makes GitHub close this issue when the pull request is merged.

        Carried in the task's ``references`` and published by worc into the pull-request body
        verbatim, which is why the number is proven here: the one value of the item's that reaches
        a body worc writes is a number, and a number can say nothing but which issue it names.
        """
        return f"{_CLOSING_KEYWORD} #{_number(item.identifier)}"

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


def _number(identifier: str) -> str:
    """``identifier`` proven to be an issue number before it reaches an argument list.

    The one item-derived value the connector passes to a command, and a number cannot be read as a
    flag, a path or a search expression by any parser.
    """
    if not identifier.isdigit():
        raise TrackerUnavailable(f"not a GitHub issue number: {identifier!r}")
    return identifier


def build_adapter(*, repo: str, labels_prefix: str) -> GitHubAdapter:
    """Build the adapter the ``worc_connect.trackers`` entry point for ``github`` resolves to."""
    return GitHubAdapter(command=GhCommand(repo=repo), labels_prefix=labels_prefix)
