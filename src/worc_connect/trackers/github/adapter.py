"""The GitHub adapter's read side: issues and pull requests, normalized.

Nothing above this module knows that GitHub calls a work item an issue, that its identifier is a
number, or that a label is an object with a name. Everything that shape implies is decided here and
nowhere else, and anything the payload does not look like is an infrastructure failure rather than a
guess — an unreadable listing costs one tick and changes no state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from worc_connect.core.items import PullRequest, PullRequestState, WorkItem, WorkItemState
from worc_connect.trackers.base import TrackerUnavailable
from worc_connect.trackers.github.gh import GhCommand

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

# GitHub's search orders by relevance unless told otherwise, and the watermark depends on the order:
# the loop advances it to the newest update in the page, which is only safe if a truncated page can
# have cut off the newer end alone.
_SORT_OLDEST_UPDATE_FIRST: Final = "sort:updated-asc"


@dataclass(frozen=True)
class GitHubAdapter:
    """The GitHub reads the core performs, over the operator's own ``gh`` login."""

    command: GhCommand

    def list_items(self, since: datetime | None) -> list[WorkItem]:
        """The repository's open issues updated at or after ``since``, oldest update first."""
        search = _SORT_OLDEST_UPDATE_FIRST
        if since is not None:
            search = f"updated:>={_search_stamp(since)} {search}"
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
        return [_work_item(entry, state=WorkItemState.OPEN) for entry in _entries(payload)]

    def get_item(self, identifier: str) -> WorkItem:
        """One issue by number, open or closed.

        The identifier is proven to be a number before it reaches the argument list. It is the one
        item-derived value the connector ever passes to a command, and a number cannot be read as a
        flag, a path or a search expression whatever the tool's parser does with it.
        """
        if not identifier.isdigit():
            raise TrackerUnavailable(f"not a GitHub issue number: {identifier!r}")
        payload = self.command.read_json("issue", "view", identifier, "--json", _ISSUE_VIEW_FIELDS)
        if not isinstance(payload, dict):
            raise TrackerUnavailable("`gh issue view` returned no issue object")
        return _work_item(payload, state=None)

    def find_pull_request(self, branch: str) -> PullRequest | None:
        """The pull request opened for ``branch`` in any state, or ``None`` while there is none.

        An open request wins over a closed one when the branch carries both, because that is the one
        the task is still working through; the caller stores its number and stops searching by
        branch, which is what keeps the follow-through alive after the branch is deleted.
        """
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
        requests = [_pull_request(entry) for entry in _entries(payload)]
        if not requests:
            return None
        return next(
            (request for request in requests if request.state is PullRequestState.OPEN),
            requests[0],
        )


def build_adapter(*, repo: str) -> GitHubAdapter:
    """Build the adapter the ``worc_connect.trackers`` entry point for ``github`` resolves to."""
    return GitHubAdapter(command=GhCommand(repo=repo))


def _search_stamp(since: datetime) -> str:
    """``since`` as GitHub's search syntax wants it: UTC, second precision, no offset notation."""
    return since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entries(payload: Any) -> list[dict[str, Any]]:
    """The payload as a list of objects, or :class:`TrackerUnavailable`."""
    if not isinstance(payload, list) or any(not isinstance(entry, dict) for entry in payload):
        raise TrackerUnavailable("`gh` returned something other than a list of objects")
    entries: list[dict[str, Any]] = payload
    return entries


def _work_item(entry: dict[str, Any], *, state: WorkItemState | None) -> WorkItem:
    """One issue payload as a :class:`~worc_connect.core.items.WorkItem`.

    ``state`` is supplied where the call pinned it and read from the payload otherwise, so a listing
    of open issues needs no field for something the query already decided.
    """
    number = entry.get("number")
    if not isinstance(number, int):
        raise TrackerUnavailable("an issue payload carries no number")
    return WorkItem(
        identifier=str(number),
        title=_string(entry, "title"),
        body=_string(entry, "body"),
        author=_author(entry),
        labels=_labels(entry),
        state=state if state is not None else _item_state(entry),
        updated_at=_timestamp(entry.get("updatedAt"), field="updatedAt"),
        url=_string(entry, "url"),
    )


def _string(entry: dict[str, Any], field: str) -> str:
    """A string field, with an absent or null value read as empty rather than as a failure.

    GitHub renders an empty issue body as ``null``, and an item with no body is an ordinary item —
    the builder decides what an empty body means for a task, not the transport.
    """
    value = entry.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TrackerUnavailable(f"an issue payload's `{field}` is not a string")
    return value


def _author(entry: dict[str, Any]) -> str:
    """The author's login, or empty when GitHub reports none.

    An author the API cannot name (a deleted account) reads as empty, which an author allow-list can
    only ever reject — the fail-closed direction for a value the gate depends on.
    """
    author = entry.get("author")
    if not isinstance(author, dict):
        return ""
    login = author.get("login")
    return login if isinstance(login, str) else ""


def _labels(entry: dict[str, Any]) -> tuple[str, ...]:
    """The label names of an issue; anything without a usable name is dropped.

    Dropping is safe in the direction that matters: a label the adapter cannot read is a label the
    gate will not match, so a malformed payload can only ever fail to admit an item.
    """
    labels = entry.get("labels")
    if not isinstance(labels, list):
        return ()
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict):
            name = label.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    return tuple(names)


def _item_state(entry: dict[str, Any]) -> WorkItemState:
    """An issue's own state, with anything but an explicit ``OPEN`` read as closed."""
    value = entry.get("state")
    return WorkItemState.OPEN if str(value).upper() == "OPEN" else WorkItemState.CLOSED


def _timestamp(value: Any, *, field: str) -> datetime:
    """A required timestamp as a timezone-aware datetime."""
    parsed = _optional_timestamp(value, field=field)
    if parsed is None:
        raise TrackerUnavailable(f"a payload carries no `{field}` timestamp")
    return parsed


def _optional_timestamp(value: Any, *, field: str) -> datetime | None:
    """A timestamp that may legitimately be absent — an unmerged pull request's merge time."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TrackerUnavailable(f"a payload's `{field}` is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TrackerUnavailable(f"a payload's `{field}` is not a readable timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pull_request(entry: dict[str, Any]) -> PullRequest:
    """One pull-request payload as a :class:`~worc_connect.core.items.PullRequest`."""
    number = entry.get("number")
    if not isinstance(number, int):
        raise TrackerUnavailable("a pull-request payload carries no number")
    return PullRequest(
        number=number,
        url=_string(entry, "url"),
        state=_pull_request_state(entry),
        merged_at=_optional_timestamp(entry.get("mergedAt"), field="mergedAt"),
    )


def _pull_request_state(entry: dict[str, Any]) -> PullRequestState:
    """A pull request's state, with an unknown value read as closed.

    Closed is the conservative reading: it stops the connector from treating a state it does not
    understand as an open request it should keep waiting on, or as a merge it should act on.
    """
    value = str(entry.get("state")).upper()
    if value == "OPEN":
        return PullRequestState.OPEN
    if value == "MERGED":
        return PullRequestState.MERGED
    return PullRequestState.CLOSED
