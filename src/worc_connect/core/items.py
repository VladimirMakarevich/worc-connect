"""The normalized work-item model every layer above an adapter speaks.

This is the whole vocabulary the core is allowed to know: an item has an identifier, a title, a
body, an author, labels, a state, a last-updated stamp and a URL — nothing tracker-shaped. An
adapter translates its tracker into these records, and the gate, the builder and the loop never
learn that GitHub calls them issues or that another tracker calls them work items.

``title`` and ``body`` are **untrusted text written by strangers**. They have exactly one
destination, the task file, and reach it through the builder's sanitizer (the title) or verbatim
(the body). They are never an argument, an environment value, a log line, a label or a comment; the
only item-derived values that may appear anywhere else are the identifier and the URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class WorkItemState(StrEnum):
    """Whether the tracker still considers the item open — the item's own state, not the task's."""

    OPEN = "open"
    CLOSED = "closed"


class ItemState(StrEnum):
    """The connector-owned states an item is published in, and their canonical names.

    These six (eight with triage on) are the visible state machine: an adapter maps each to its
    tracker's vocabulary — GitHub to the label ``<prefix><state>`` — and exactly one is present on
    an item at a time. The connector's own row is a cache of what these already say, which is what
    lets a restarted connector, and a human reading the item, see the same picture.
    """

    QUEUED = "queued"
    IN_PROGRESS = "in-progress"
    PR_OPEN = "pr-open"
    DONE = "done"
    FAILED = "failed"
    NEEDS_INFO = "needs-info"
    DECLINED = "declined"


class PullRequestState(StrEnum):
    """A pull request's state as every supported code host reports it."""

    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


@dataclass(frozen=True)
class WorkItem:
    """One work item as the core sees it, normalized by an adapter.

    ``identifier`` is the tracker's own item id kept as a string (GitHub's issue number, another
    tracker's key), because it is a name to the core and never arithmetic. ``updated_at`` is
    timezone-aware: the watermark is compared against it across processes and hosts, so a naive
    stamp would silently compare a local clock with a tracker's UTC.
    """

    identifier: str
    title: str
    body: str
    author: str
    labels: tuple[str, ...]
    state: WorkItemState
    updated_at: datetime
    url: str


@dataclass(frozen=True)
class PullRequest:
    """The pull request a task opened, as much of it as the connector needs to follow through.

    ``number`` is what makes the follow-through survive the owner's edits: a pull request is found
    once by the branch the connector named and read by number ever after, so a retitled request, a
    branch deleted after a squash merge, or commits nobody in worc made change nothing.
    """

    number: int
    url: str
    state: PullRequestState
    merged_at: datetime | None
