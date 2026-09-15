"""Turning ``gh``'s JSON into the records the core speaks, and refusing what it cannot read.

Kept apart from the calls so that the adapter reads as the list of things the connector does to
GitHub, and this module as the single account of what GitHub's payloads mean. Anything a payload
does not look like is an infrastructure failure rather than a guess: an unreadable listing costs one
tick and changes no state, where a guess could cost a duplicate task or a wrongly closed issue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from worc_connect.core.items import PullRequest, PullRequestState, WorkItem, WorkItemState
from worc_connect.trackers.base import TrackerUnavailable


def search_stamp(since: datetime) -> str:
    """``since`` as GitHub's search syntax documents it: UTC, second precision, a ``+00:00`` offset.

    The documented form of a datetime qualifier ends in a UTC offset; a qualifier GitHub did not
    recognise would silently widen or empty the listing, so the spelling its documentation shows is
    the one used.
    """
    return since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def entries(payload: Any) -> list[dict[str, Any]]:
    """The payload as a list of objects, or :class:`TrackerUnavailable`."""
    if not isinstance(payload, list) or any(not isinstance(entry, dict) for entry in payload):
        raise TrackerUnavailable("`gh` returned something other than a list of objects")
    entries: list[dict[str, Any]] = payload
    return entries


def work_item(entry: dict[str, Any], *, state: WorkItemState | None) -> WorkItem:
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
    parsed = optional_timestamp(value, field=field)
    if parsed is None:
        raise TrackerUnavailable(f"a payload carries no `{field}` timestamp")
    return parsed


def optional_timestamp(value: Any, *, field: str) -> datetime | None:
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


def pull_request(entry: dict[str, Any]) -> PullRequest:
    """One pull-request payload as a :class:`~worc_connect.core.items.PullRequest`."""
    number = entry.get("number")
    if not isinstance(number, int):
        raise TrackerUnavailable("a pull-request payload carries no number")
    return PullRequest(
        number=number,
        url=_string(entry, "url"),
        state=_pull_request_state(entry),
        merged_at=optional_timestamp(entry.get("mergedAt"), field="mergedAt"),
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
