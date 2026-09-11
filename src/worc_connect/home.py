"""The connector's own directory inside a clone: where its files live and how it is created.

``.worc-connect/`` is a sibling of worc's private home, never a subdirectory of it. The connector
caches untrusted issue text and its own bookkeeping here, and worc's control home is a boundary that
worc drew deliberately around its own state; a foreign process writing inside it would blur exactly
that line.

Creating the home is the only thing ``worc-connect init`` does: it writes a configuration the
operator then edits, and it appends the home to the clone's tracked ``.gitignore`` so nothing the
connector writes can reach a commit. The gitignore probe is textual because the connector never runs
``git`` in the clone — not even to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from worc_connect.config import (
    DEFAULT_BRANCH_PREFIX,
    DEFAULT_ID_PREFIX,
    DEFAULT_LABELS_PREFIX,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TRIAGE_FLOW,
    DEFAULT_TRIGGER_LABEL,
    DEFAULT_WORC_COMMAND,
    SCHEMA_VERSION,
)

HOME_DIRNAME: Final = ".worc-connect"
CONFIG_FILENAME: Final = "config.yaml"
STATE_FILENAME: Final = "state.db"
LOG_FILENAME: Final = "connect.log"
PID_FILENAME: Final = "connect.pid"
STOP_FILENAME: Final = "connect.stop"
TRIAGE_DIRNAME: Final = "triage"

GITIGNORE_FILENAME: Final = ".gitignore"
GITIGNORE_COMMENT: Final = "# worc-connect runtime home (auto-appended by `worc-connect init`)"
GITIGNORE_LINE: Final = f"{HOME_DIRNAME}/"


@dataclass(frozen=True)
class ConnectorHome:
    """The connector's directory in the clone and the fixed names of the files it owns."""

    path: Path

    @classmethod
    def beside(cls, clone_path: Path) -> ConnectorHome:
        """The home directory the connector uses for the clone rooted at ``clone_path``."""
        return cls(clone_path / HOME_DIRNAME)

    @property
    def clone_path(self) -> Path:
        """The clone the home belongs to — its parent directory."""
        return self.path.parent

    @property
    def config_path(self) -> Path:
        """The connector's configuration file."""
        return self.path / CONFIG_FILENAME

    @property
    def state_path(self) -> Path:
        """The SQLite cache of per-item rows and the watermark."""
        return self.path / STATE_FILENAME

    @property
    def log_path(self) -> Path:
        """The connector's own log."""
        return self.path / LOG_FILENAME

    @property
    def pid_path(self) -> Path:
        """The self-managed PID file that keeps a second watcher out of this clone."""
        return self.path / PID_FILENAME

    @property
    def stop_path(self) -> Path:
        """The stop sentinel the watch loop polls; a file, so Windows behaves like POSIX."""
        return self.path / STOP_FILENAME

    @property
    def triage_path(self) -> Path:
        """Where a triage flow leaves its report, inside a directory the connector owns."""
        return self.path / TRIAGE_DIRNAME


def render_config(*, tracker: str, repo: str) -> str:
    """The configuration file ``init`` writes: every key the loader accepts, with its default.

    Written out in full, comments included, because this file is the operator's whole interface to
    the connector's behaviour and a key they cannot see is a key they will not set. Two values are
    deliberately left out: ``task.commit_type_by_label``, which has no sensible default mapping,
    and ``task.auto_merge`` — an issue-sourced task that merges itself is where a prompt injection
    in a stranger's issue becomes merged code with no human in between, so the connector never sets
    it and says so where the operator reads it.
    """
    return f"""\
# worc-connect — tracker connector for wastech-orchestrator.
# Written by `worc-connect init`; edit it and re-run `worc-connect watch --once --dry-run`.
schema_version: {SCHEMA_VERSION}

tracker: {tracker}
repo: {repo} # pinned onto every tracker call
poll_interval_seconds: {DEFAULT_POLL_INTERVAL_SECONDS}

# The perimeter. An item becomes a task only if a rule here admits it; with no rule at all the
# connector refuses to start, because "nothing configured" must never read as "every item".
gate:
  labels: [{DEFAULT_TRIGGER_LABEL}] # trigger label(s); an item needs at least one
  authors: [] # optional allow-list; empty means the labels alone decide
  allow_all: false # true, explicitly, to accept every item of the repository

# Dispatch fields for the generated task. A key left out here is a key the task file does not carry,
# so worc's own default stands instead of the connector's opinion of it.
task:
  task_type: implementation
  queue: default
  priority: mid
  commit_type: feat
  # commit_type_by_label: {{ bug: fix, documentation: docs }}
  branch_prefix: {DEFAULT_BRANCH_PREFIX}
  id_prefix: {DEFAULT_ID_PREFIX}
  # auto_merge: false
  #   Left unset on purpose. An issue-sourced task under `auto_merge: true` is where a prompt
  #   injection in an issue turns into merged code with nobody in between; worc's own policy and
  #   your review of the pull request are what should decide.

worc:
  command: {DEFAULT_WORC_COMMAND} # resolved on PATH, launched as an argument list
  repo_path: . # the clone worc runs in, relative to the directory holding this home

write_back:
  labels_prefix: "{DEFAULT_LABELS_PREFIX}"
  comment: true
  close_on_merge: true

triage:
  enabled: false
  flow: {DEFAULT_TRIAGE_FLOW}
"""


def ensure_gitignore_entry(clone_path: Path) -> bool:
    """Append the connector's home to the clone's ``.gitignore``; True when a line was added.

    Idempotent through a textual probe of the file's own lines: the connector never runs ``git``, so
    ``git check-ignore`` is not available to it, and re-appending a line the operator already wrote
    would be noise in a file they own. A pattern an operator has expressed differently (a broader
    glob, say) therefore still gets this line — visible, and one deletion away from undone.
    """
    path = clone_path / GITIGNORE_FILENAME
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if GITIGNORE_LINE in {line.strip() for line in existing.splitlines()}:
        return False
    separator = "" if existing == "" or existing.endswith("\n") else "\n"
    block = f"{separator}{GITIGNORE_COMMENT}\n{GITIGNORE_LINE}\n"
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(block)
    return True
