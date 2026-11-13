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
    DEFAULT_MAX_LINE_BYTES,
    DEFAULT_MAX_TASK_BYTES,
    DEFAULT_MAX_TASK_LINES,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_RESEARCH_FLOW,
    DEFAULT_TASKS_DIR,
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

    @property
    def report_dir(self) -> str:
        """The flow's ``report_dir``: this home's report directory, relative to the clone.

        POSIX and repo-relative because that is the shape worc's flow validator accepts and the
        shape the shipped flow declares; the two have to agree or the report lands somewhere the
        connector does not read.
        """
        return f"{HOME_DIRNAME}/{TRIAGE_DIRNAME}"


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

# How to reach worc, and the two things both sides have to agree on. The connector never opens
# worc's own configuration — its only read out of worc is `worc list` — so if you changed
# `paths.tasks_dir` or any `validation.max_*` over there, restate the value here.
worc:
  command: {DEFAULT_WORC_COMMAND} # resolved on PATH, launched as an argument list
  repo_path: . # the clone worc runs in, relative to the directory holding this home
  tasks_dir: {DEFAULT_TASKS_DIR} # worc's `paths.tasks_dir`; the task is staged in its preparing/
  max_task_bytes: {DEFAULT_MAX_TASK_BYTES} # worc's `validation.max_task_bytes`
  max_task_lines: {DEFAULT_MAX_TASK_LINES} # worc's `validation.max_task_lines`
  max_line_bytes: {DEFAULT_MAX_LINE_BYTES} # worc's `validation.max_line_bytes`

write_back:
  labels_prefix: "{DEFAULT_LABELS_PREFIX}"
  comment: true
  close_on_merge: true
  #   Off lets the code host close the item itself: the generated task carries the tracker's own
  #   closing keyword (`Fixes #<n>` on GitHub) and worc appends it to the pull-request body, so
  #   merging into the default branch closes the item and the connector only labels it done.

# The optional analysis step. `off` sends a gated item straight to an implementation task. `worc`
# queues a *triage* task first — one agent run per item, inside worc's own sandbox — and builds the
# implementation task from the report it leaves in `.worc-connect/triage/<task-id>/report.md`.
# Install the shipped flow with `worc-connect install-flow` before turning this on.
research:
  # Quoted on purpose: YAML reads a bare `off` as the boolean false, not as this mode's name.
  mode: "off" # "off" | "worc"
  flow: {DEFAULT_RESEARCH_FLOW} # the flow a triage task names; `install-flow` ships this one
"""


def gitignore_line(home: ConnectorHome) -> str:
    """The ``.gitignore`` line ``init`` writes for ``home``: its directory name, as a directory.

    Unanchored on purpose for the default name — ``.worc-connect/`` is the line operators already
    have, and a second spelling of it would be noise. A home under another name gets its own name,
    because a line for the default would leave the directory actually in use trackable.
    """
    return f"{home.path.name}/"


def ensure_gitignore_entry(home: ConnectorHome) -> bool:
    """Append ``home`` to its clone's ``.gitignore``; True when a line was added.

    Idempotent through a textual probe of the file's own lines: the connector never runs ``git``, so
    ``git check-ignore`` is not available to it, and re-appending a line the operator already wrote
    would be noise in a file they own. A pattern an operator has expressed differently (a broader
    glob, say) therefore still gets this line — visible, and one deletion away from undone.
    """
    path = home.clone_path / GITIGNORE_FILENAME
    line = gitignore_line(home)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if line in {entry.strip() for entry in existing.splitlines()}:
        return False
    separator = "" if existing == "" or existing.endswith("\n") else "\n"
    block = f"{separator}{GITIGNORE_COMMENT}\n{line}\n"
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(block)
    return True


def unignored_home(home: ConnectorHome, clone_path: Path) -> str | None:
    """Why ``home`` is not kept out of ``clone_path``'s commits, or ``None`` when it is.

    The home caches issue text, so a home git would track is a leak waiting for the next ``git
    add -A`` — and the line that prevents it lives in a tracked file anybody, a worc agent tidying
    "unrelated" changes included, can revert. The watch loop therefore asks this before every tick
    and refuses to run while the answer is not ``None``.

    Textual, like the probe ``init`` uses, because the connector never runs ``git``. Only the
    clone's root ``.gitignore`` is read, and only the spellings that name this directory exactly
    count — the path, anchored or not, and the bare directory name or ``**/name`` — with a later
    ``!`` negation of the same spelling undoing it. A broader glob, a nested ``.gitignore``,
    ``.git/info/exclude`` and a global excludes file do not count: the last two live in git's own
    files, which the connector does not open, and an answer this probe cannot give is
    the fail-closed one. A home outside the clone needs no line at all.
    """
    try:
        relative = home.path.resolve().relative_to(clone_path.resolve()).as_posix()
    except ValueError:
        return None
    path = clone_path / GITIGNORE_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    spellings = _spellings(relative)
    ignored = False
    for raw in lines:
        entry = raw.strip()
        if entry in spellings:
            ignored = True
        elif entry.startswith("!") and entry[1:] in spellings:
            ignored = False
    if ignored:
        return None
    return (
        f"the connector's home `{relative}/` is not ignored by "
        f"{path.as_posix()} — it caches issue text and must never reach a commit; add the line "
        f"`{relative}/` to that file and commit it"
    )


def _spellings(relative: str) -> frozenset[str]:
    """The ``.gitignore`` patterns that name the directory at ``relative`` and nothing broader.

    The path itself, anchored or not; and the bare directory name, which git matches at any depth
    because it has no slash but a trailing one — the line ``init`` writes is of that kind.
    """
    name = relative.rsplit("/", 1)[-1]
    anchored = {relative, f"{relative}/", f"/{relative}", f"/{relative}/"}
    return frozenset(anchored | {name, f"{name}/", f"**/{name}", f"**/{name}/"})
