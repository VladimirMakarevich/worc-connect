"""The tracker contract: the protocol an adapter implements and the failures it may report.

The core is written against this protocol and nothing else, so a tracker is one adapter behind an
optional dependency and the loop contains no branch per tracker. An adapter is resolved by name
through the ``worc_connect.trackers`` entry-point group, so an adapter shipped from another
distribution is picked up without a change here.

The error vocabulary is deliberately tiny. Everything an adapter can fail at that is not the
connector's business — a login that expired, an API quota, a network that is down — arrives as one
of three classes, and the loop turns all three into the same decision: skip this tick, change no
state, try again next time. Failing closed like that is what makes an unreachable tracker harmless
rather than a source of duplicate or half-finished tasks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from worc_connect.core.items import ItemState, PullRequest, WorkItem


class TrackerError(Exception):
    """Base class for every infrastructure failure an adapter reports to the loop."""


class TrackerUnavailable(TrackerError):
    """The tracker could not be reached, or answered with something unusable.

    The catch-all of the three: a network failure, a timeout, a missing command-line tool, an
    unparseable response, a repository the host does not resolve.
    """


class TrackerAuth(TrackerError):
    """The tracker refused the operator's credentials.

    A separate class because it is the one failure that will not fix itself: the operator has to log
    in again on the host. The connector holds no token of its own, so it has nothing to refresh.
    """


class TrackerRateLimited(TrackerError):
    """The tracker is throttling this operator; the next tick is the retry."""


@runtime_checkable
class TrackerAdapter(Protocol):
    """One tracker, reduced to the reads the core performs on it.

    Implementations translate their tracker into :class:`~worc_connect.core.items.WorkItem` and
    :class:`~worc_connect.core.items.PullRequest` records and raise :class:`TrackerError` (one of
    its three subclasses) for anything infrastructural. They pin every call to the one configured
    repository, and they never place item-derived text in an argument.
    """

    def list_items(self, since: datetime | None) -> list[WorkItem]:
        """The repository's open items updated at or after ``since``, oldest update first.

        ``None`` lists every open item. The order is part of the contract: the loop advances its
        watermark to the newest update it has seen, and a page the tracker truncated may only ever
        cut off the *newer* end, or an item would be skipped for good.
        """
        ...

    def get_item(self, identifier: str) -> WorkItem:
        """One item by its tracker identifier, whatever its state.

        Used where the listing cannot answer — an item that has dropped out of the open listing but
        still carries a live task. Raises :class:`TrackerUnavailable` when the item does not exist.
        """
        ...

    def find_pull_request(self, branch: str, *, url: str | None = None) -> PullRequest | None:
        """The pull request the task opened, or ``None`` while there is none.

        ``url`` is worc's own record of that request, where worc published one. It is exact and it
        outlives the head — a squash merge that deleted the branch leaves a branch query nothing to
        find — so an implementation resolves it first and falls back to searching by the branch the
        connector itself named, in any state, so that a request already merged or closed is found
        too. Once found, the caller remembers its number and reads it by number from then on.
        """
        ...

    def get_pull_request(self, number: int) -> PullRequest:
        """One pull request by number, whatever has happened to it since.

        This is what makes the follow-through survive the owner: more commits, a new title, a
        squash merge, a deleted branch and a reopening all leave the number alone. Raises
        :class:`TrackerUnavailable` when the request cannot be read.
        """
        ...

    def current_state(self, item: WorkItem) -> ItemState | None:
        """The connector-owned state ``item`` currently shows, or ``None`` when it shows none.

        Read from the item itself rather than from the connector's cache, because the item is the
        visible state machine: it is what a human sees, what a restarted connector rebuilds from,
        and what makes re-applying a state a no-op instead of a second notification.
        """
        ...

    def set_state(self, identifier: str, state: ItemState, *, previous: ItemState | None) -> None:
        """Put ``state`` on the item and take ``previous`` off, leaving exactly one in place."""
        ...

    def comment(self, identifier: str, body_path: Path) -> None:
        """Post the comment whose body is the file at ``body_path``.

        The body travels as a **file** on purpose. Nothing an item wrote is ever in it — every
        comment is a connector-authored template carrying a task id, a status name and URLs — and a
        body file is what keeps that true by construction rather than by review.
        """
        ...

    def close(self, identifier: str, message: str) -> None:
        """Close the item with ``message``, which is a connector-authored template."""
        ...

    def ensure_labels(self, states: tuple[ItemState, ...], *, triggers: tuple[str, ...]) -> None:
        """Create whichever of the connector's labels the tracker does not have yet.

        ``states`` are the connector-owned states this configuration can publish; ``triggers`` are
        the gate's trigger labels, spelled as the operator configured them. Called on the first tick
        of a process rather than at ``init``, which is an offline command that touches nothing but
        the connector's own home. A label that exists is never edited, and one that turns out to
        exist after all — a listing the tracker capped — is tolerated rather than reported.
        """
        ...

    def closing_reference(self, item: WorkItem) -> str | None:
        """The line that makes this tracker close ``item`` when the pull request merges.

        It travels in the task's ``references``, which worc appends verbatim to the pull-request
        body without interpreting it: the keyword is the adapter's knowledge — GitHub's ``Fixes
        #<n>``, another host's own spelling — and worc learns none of them. An adapter whose
        tracker has no such keyword returns ``None``, and the task then carries no reference.
        """
        ...


class AdapterFactory(Protocol):
    """What an entry point in the ``worc_connect.trackers`` group must resolve to.

    Keyword-only and deliberately narrow: the repository pin is the only thing every tracker needs,
    and an adapter that accepted the whole configuration object would be coupled to a schema the
    core owns — which is also why ``import-linter`` forbids an adapter from importing it.
    """

    def __call__(self, *, repo: str, labels_prefix: str) -> TrackerAdapter:
        """Build an adapter bound to ``repo``, publishing state under ``labels_prefix``.

        The prefix is configuration the operator owns, and turning a state into the tracker's own
        vocabulary is the adapter's job, so it is handed over at construction: the core names a
        state, never a label.
        """
        ...
