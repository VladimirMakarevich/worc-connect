"""The gate: the perimeter that decides which items may ever become tasks.

This is the security boundary of the whole connector, and it is deliberately the least clever module
in it. An item is admitted only because a rule the operator wrote admits it — a trigger label a
maintainer applied, an author on an allow-list, or the explicit ``gate.allow_all``. There is no
heuristic, no scoring, and no "probably meant for us": an item nobody gated never reaches an agent,
so a drive-by issue can neither spend budget nor steer a run.

A configuration that states no rule at all never reaches this module — the loader refuses it — so
"admitted" here always means a rule said yes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from worc_connect.config import GateConfig
from worc_connect.core.items import WorkItem


class GateReason(StrEnum):
    """Why the gate decided as it did — a closed vocabulary, because it is logged verbatim.

    Every value is a fixed token: the reason line carries no label an item invented and no author
    name, so a log stays free of text a stranger wrote.
    """

    ALLOW_ALL = "allow-all"
    TRIGGER_LABEL = "trigger-label"
    ALLOWED_AUTHOR = "allowed-author"
    NO_TRIGGER_LABEL = "no-trigger-label"
    AUTHOR_NOT_ALLOWED = "author-not-allowed"


@dataclass(frozen=True)
class GateVerdict:
    """The gate's decision on one item, with the reason it is logged and reported under."""

    admitted: bool
    reason: GateReason


def evaluate(item: WorkItem, gate: GateConfig) -> GateVerdict:
    """Whether ``item`` may become a task under ``gate``.

    Both configured rules are filters, and they are applied in sequence: a trigger label admits an
    item only if the author allow-list — when the operator wrote one — also admits it. That order is
    what makes a non-empty ``authors`` narrow the gate rather than widen it, so a label applied by
    someone outside the list is not enough.
    """
    if gate.allow_all:
        return GateVerdict(admitted=True, reason=GateReason.ALLOW_ALL)
    if gate.labels and not _carries_trigger_label(item, gate.labels):
        return GateVerdict(admitted=False, reason=GateReason.NO_TRIGGER_LABEL)
    if gate.authors and not _is_allowed_author(item, gate.authors):
        return GateVerdict(admitted=False, reason=GateReason.AUTHOR_NOT_ALLOWED)
    reason = GateReason.TRIGGER_LABEL if gate.labels else GateReason.ALLOWED_AUTHOR
    return GateVerdict(admitted=True, reason=reason)


def _carries_trigger_label(item: WorkItem, labels: tuple[str, ...]) -> bool:
    """Whether the item carries at least one trigger label, compared case-insensitively.

    Trackers treat label names as case-insensitive for uniqueness, so an operator who configured
    ``worc`` must not be defeated by a maintainer who applied ``Worc``.
    """
    wanted = {label.casefold() for label in labels}
    return any(label.casefold() in wanted for label in item.labels)


def _is_allowed_author(item: WorkItem, authors: tuple[str, ...]) -> bool:
    """Whether the item's author is on the allow-list, compared case-insensitively.

    Account names are case-insensitive on every tracker the connector targets, and the allow-list is
    a security control: it must not be bypassable by capitalising a letter.
    """
    return item.author.casefold() in {author.casefold() for author in authors}
