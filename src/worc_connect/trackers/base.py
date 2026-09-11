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
from typing import Protocol, runtime_checkable

from worc_connect.core.items import PullRequest, WorkItem


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

    def find_pull_request(self, branch: str) -> PullRequest | None:
        """The pull request opened for ``branch``, or ``None`` while there is none.

        Searched by the branch the connector itself named, in any state, so that a request already
        merged or closed is found too. Once found, the caller remembers its number and reads it by
        number from then on.
        """
        ...


class AdapterFactory(Protocol):
    """What an entry point in the ``worc_connect.trackers`` group must resolve to.

    Keyword-only and deliberately narrow: the repository pin is the only thing every tracker needs,
    and an adapter that accepted the whole configuration object would be coupled to a schema the
    core owns — which is also why ``import-linter`` forbids an adapter from importing it.
    """

    def __call__(self, *, repo: str) -> TrackerAdapter:
        """Build an adapter bound to the repository named by ``repo``."""
        ...
