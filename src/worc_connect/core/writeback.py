"""What the connector puts back on the item: one state, a few comments, and the close on merge.

The item is the visible state machine, and this module is the only thing that writes to it. Two
rules shape everything here.

**Exactly one state, and re-applying it is a no-op.** The state the item already shows is read from
the item itself, not from the connector's cache, so a hand edit is adopted rather than fought and a
restarted connector with no database at all still writes nothing it has already written.

**A comment is what a state change sounds like.** Comments are posted when the state actually
changes, which makes them idempotent for free: a quiet tick posts nothing, a rebuilt row posts
nothing, and the same failure is never reported twice. Every body is a template this package wrote,
carrying a task id, a status name and URLs — never the item's own text, never a log line, never a
diff, and never anything read out of worc's home. Bodies travel as files; the closing message is
the one exception the tracker's own command shape forces, and it is a template too.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from worc_connect.config import ConnectorConfig
from worc_connect.core.items import ItemState, WorkItem
from worc_connect.core.state import ItemRow, Phase
from worc_connect.trackers.base import TrackerAdapter

logger = logging.getLogger(__name__)

# Which of the connector's phases the item is shown. The phases before the handoff have no state of
# their own: an item whose task has not reached worc yet is an item nothing has happened to, and
# announcing "about to queue" would be a promise the next tick might not keep.
PHASE_STATES: Final = {
    Phase.QUEUED: ItemState.QUEUED,
    Phase.RUNNING: ItemState.IN_PROGRESS,
    Phase.PR_OPEN: ItemState.PR_OPEN,
    Phase.DONE: ItemState.DONE,
    Phase.FAILED: ItemState.FAILED,
}

# The reverse, for rebuilding a row from what the item already shows.
STATE_PHASES: Final = {state: phase for phase, state in PHASE_STATES.items()}

_COMMENT_FILENAME: Final = "comment.md"


@dataclass
class WriteBack:
    """The item-facing half of a tick: state labels, comments and the close on merge.

    Holds one piece of per-process state — whether the tracker's state labels have been created —
    because that is a question worth asking once per run rather than once per item.
    """

    config: ConnectorConfig
    adapter: TrackerAdapter
    _labels_ensured: bool = False

    def publish(self, row: ItemRow, item: WorkItem) -> bool:
        """Show ``row``'s phase on the item; returns whether that closed the item.

        The caller needs the answer because closing is what turns a later trigger on the same item
        into a new request: a closed item leaves the tracker's open listing, so only somebody
        reopening it — or re-applying the label — brings it back.
        """
        desired = PHASE_STATES.get(row.phase)
        if desired is None:
            return False
        current = self.adapter.current_state(item)
        if current is desired:
            return False
        self._ensure_labels()
        self.adapter.set_state(item.identifier, desired, previous=current)
        _log(row, "state", str(desired))
        return self._announce(row, item, desired)

    def _announce(self, row: ItemRow, item: WorkItem, state: ItemState) -> bool:
        """Say what changed, in the one place people who use the tracker will look.

        Closing is part of this rather than of the state write because a close is the connector's
        strongest action on an item, and it must happen only where the pull request was actually
        merged — never merely because a row reached a terminal phase.
        """
        if state is ItemState.DONE and row.pr_merged and self.config.write_back.close_on_merge:
            self.adapter.close(item.identifier, _closing_message(row))
            _log(row, "close", "merged")
            return True
        body = _comment_body(row, state)
        if body is None or not self.config.write_back.comment:
            return False
        with tempfile.TemporaryDirectory(prefix="worc-connect-") as directory:
            # Outside the clone on purpose: the only path the connector writes there is the task
            # file, and a transient body has no business next to worc's lifecycle tree.
            path = Path(directory) / _COMMENT_FILENAME
            path.write_text(body, encoding="utf-8", newline="")
            self.adapter.comment(item.identifier, path)
        _log(row, "comment", str(state))
        return False

    def _ensure_labels(self) -> None:
        """Create the connector's state labels once per process, before the first state is shown."""
        if self._labels_ensured:
            return
        self.adapter.ensure_labels(tuple(PHASE_STATES.values()))
        self._labels_ensured = True


def _comment_body(row: ItemRow, state: ItemState) -> str | None:
    """The comment a move into ``state`` deserves, or ``None`` where it deserves none.

    Three of the five states are worth a comment: the task exists, the pull request exists, the
    task ended without one. ``in-progress`` is not — the label already says it, and a comment per
    step would bury the item's own conversation. Nor is ``done``, whose closing message says it.
    """
    task = row.task_id or "the task"
    if state is ItemState.QUEUED:
        return f"Queued as worc task `{task}`.\n"
    if state is ItemState.PR_OPEN and row.pr_url:
        return f"worc task `{task}` opened a pull request: {row.pr_url}\n"
    if state is ItemState.FAILED:
        return _failure_body(row, task)
    return None


def _failure_body(row: ItemRow, task: str) -> str:
    """Why the task ended without a pull request, in as much detail as worc itself published.

    A task worc's validation gate refused has no run to point anybody at, so the reason worc gave
    is the whole of what can be said — and it is the only thing taken from that entry. Where worc
    published none, the comment says what it can and sends the operator to the host, which is the
    one place worc's own record of a task lives.
    """
    if row.validation_reason:
        return (
            f"worc's validation gate refused task `{task}`: `{row.validation_reason}`.\n\n"
            "Nothing was queued and no pull request was opened. The task file is in worc's "
            "quarantine on the host that runs worc; the connector does not read it and never "
            "re-queues a refused task on its own.\n"
        )
    return (
        f"worc task `{task}` ended as `{row.last_status or 'failed'}` and opened no pull "
        f"request.\n\nRun `worc status {task}` on the host that runs worc for the detail — "
        "the connector does not copy worc's logs onto the tracker.\n"
    )


def _closing_message(row: ItemRow) -> str:
    """The closing message, carrying only the task id and the pull-request URL."""
    reference = f" ({row.pr_url})" if row.pr_url else ""
    return f"Closed by the merged pull request of worc task `{row.task_id}`{reference}."


def _log(row: ItemRow, action: str, result: str) -> None:
    """One line per action, carrying identifiers only."""
    logger.info(
        "item=%s task=%s action=%s result=%s", row.item_id, row.task_id or "-", action, result
    )
