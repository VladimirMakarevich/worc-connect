"""The handoff into worc: one file written into ``tasks/preparing/``, then ``worc promote``.

This is the whole of the connector's write side inside the clone. It writes nowhere else — not into
``tasks/pending/``, which is worc's queue and worc's to fill; not into ``.worc/``, which is worc's
private home; and it runs no ``git``, not even to read. ``preparing/`` is worc's documented staging
area, which its watch scanner never looks in, and ``promote`` is the atomic, refuse-to-overwrite
step worc built for exactly this race.

The write is atomic — a temporary file in the same directory, then :func:`os.replace` — so a half
written file is never visible to a promote, and the file is written with ``newline=""`` so its line
endings are LF on every operating system: worc reads the bytes, and a task file rewritten by
Windows text mode would be a different file than the one the connector composed.
"""

from __future__ import annotations

import logging
import os
import tempfile
from enum import StrEnum
from pathlib import Path

from worc_connect.config import ConnectorConfig
from worc_connect.core.builder import TaskDraft
from worc_connect.core.worc_cli import WorcCommand, WorcRun

logger = logging.getLogger(__name__)

# What worc prints when the task is already queued under that name. Its exit code is 1, the same as
# for a real error, so the line is the only thing that tells the two apart.
_ALREADY_PENDING = "already in pending"

PREPARING_DIRNAME = "preparing"
PENDING_DIRNAME = "pending"


class PromoteOutcome(StrEnum):
    """How a promote ended, in the three meanings the connector acts on differently."""

    PROMOTED = "promoted"
    ALREADY_PENDING = "already-pending"
    RETRY = "retry"


def preparing_dir(config: ConnectorConfig) -> Path:
    """worc's staging directory in the configured clone."""
    return config.worc.repo_path / config.worc.tasks_dir / PREPARING_DIRNAME


def pending_dir(config: ConnectorConfig) -> Path:
    """worc's queue directory — read to tell a promoted task from a vanished one, never written."""
    return config.worc.repo_path / config.worc.tasks_dir / PENDING_DIRNAME


def task_path(config: ConnectorConfig, task_id: str) -> Path:
    """Where the task file for ``task_id`` is staged."""
    return preparing_dir(config) / f"{task_id}.md"


def is_handed_over(config: ConnectorConfig, task_id: str) -> bool:
    """Whether worc holds the task file — that is, whether it sits in the queue directory.

    Read from disk rather than from worc's listing because a freshly promoted file has no database
    row yet; the listing answers for everything after that.
    """
    return (pending_dir(config) / f"{task_id}.md").is_file()


def stage(draft: TaskDraft, config: ConnectorConfig) -> Path:
    """Write ``draft`` into worc's staging directory atomically and return the path.

    Creating the directory is part of the operation: a clone whose lifecycle tree was never
    scaffolded is an ordinary starting state, not a failure, and the alternative would be a
    connector that refuses to run until the operator ran a worc command it did not need otherwise.
    """
    directory = preparing_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{draft.task_id}.md"
    # A temporary an earlier attempt at this item left behind — a crash between the write and the
    # rename — is the connector's own, and it is removed before a new one is written: `preparing/`
    # is worc's staging area, and nothing else would ever clean it.
    for stray in directory.glob(f".{draft.task_id}.*.tmp"):
        stray.unlink(missing_ok=True)
    handle, name = tempfile.mkstemp(dir=directory, prefix=f".{draft.task_id}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(draft.content)
        temporary.replace(destination)
    finally:
        # Whatever interrupted the write, the temporary must not stay where worc stages its tasks;
        # after a successful rename there is nothing left to remove.
        temporary.unlink(missing_ok=True)
    return destination


def promote(task_id: str, worc: WorcCommand) -> PromoteOutcome:
    """Hand ``task_id`` to worc, classifying what worc reports.

    "Already in pending" is a success: the file is worc's, which is exactly the state the connector
    wanted, and it is also what a crash between the write and the promote looks like on the next
    tick. Anything else is left as a retry — the row keeps its staged phase and the next tick runs
    promote again, which is safe because promote never overwrites a queued task.
    """
    completed = worc.promote(task_id)
    if completed.returncode == 0:
        return PromoteOutcome.PROMOTED
    if any(_ALREADY_PENDING in line for line in completed.stdout_lines()):
        return PromoteOutcome.ALREADY_PENDING
    logger.warning(
        "item=- task=%s action=promote result=retry (%s)", task_id, _diagnostic(completed)
    )
    return PromoteOutcome.RETRY


def _diagnostic(completed: WorcRun) -> str:
    """worc's own first line of complaint, for a log line that says why a retry is pending.

    worc's own text, never the item's: the connector's task ids and worc's outcome vocabulary are
    the only things that reach a log line.
    """
    lines = completed.stdout_lines() or [line.strip() for line in completed.stderr.splitlines()]
    return next((line for line in lines if line), "no diagnostic")
