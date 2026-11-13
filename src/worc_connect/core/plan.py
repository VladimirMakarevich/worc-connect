"""What one tick decided, item by item — the value a dry run prints and ``status`` summarizes.

Kept apart from the loop that produces it: the CLI renders it, the tests read it, and neither needs
the loop's machinery to do so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Action(StrEnum):
    """What the tick decided to do about one item, in the vocabulary a dry run prints."""

    STAGE = "stage-task"
    FOLLOW = "follow"
    SKIP = "skip"


@dataclass(frozen=True)
class PlannedAction:
    """One item's outcome for this tick: what was decided and the reason it is reported under.

    Carries the item's identifier and URL and nothing else the item wrote — a plan an operator reads
    on a terminal is not a place for a stranger's text.
    """

    item_id: str
    url: str
    action: Action
    reason: str
    task_id: str | None = None
    branch: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class TickReport:
    """What one tick saw and did — the value ``status`` summarizes and a dry run prints.

    ``listed`` counts the items the tracker's listing returned; ``followed`` the ones read by
    identifier because a task of theirs is still in flight while the poll window no longer lists
    them. Both are handled the same way once read — the split is for the operator, who is owed the
    difference between "nothing changed" and "nothing changed, and two tasks are still watched".
    """

    listed: int
    actions: tuple[PlannedAction, ...]
    watermark: datetime | None
    followed: int = 0

    def counted(self, action: Action) -> int:
        """How many items this tick decided ``action`` for."""
        return sum(1 for planned in self.actions if planned.action is action)
