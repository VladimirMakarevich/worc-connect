"""The connector's configuration: schema and fail-closed loader for ``.worc-connect/config.yaml``.

Shapes only — this module depends on nothing above it (enforced by ``import-linter``), so the CLI,
the core and the adapters can all read the resolved configuration without a cycle.

The loader **rejects rather than repairs**. A value of the wrong type, a key nobody declared, a gate
with no rule: each is an error that names its key and stops the process. The reason is not tidiness
but the security model — the gate is the only thing between a stranger's issue text and an agent
with write access to the repository, and a typo such as ``authros:`` would otherwise silently widen
it from "these authors" to "anyone who can apply the label". Unknown keys are therefore refused in
every section, not just at the top level.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

import yaml

# Re-exported explicitly: the configuration layer is one thing to everybody above it, and its one
# exception is part of that surface even though the reader and the rules raise it from two files.
from worc_connect.config_errors import ConfigError as ConfigError
from worc_connect.config_section import Section

# The one schema version this build understands. A file that declares another one is refused rather
# than read with today's meaning: the connector's write into worc is a task file, and a
# misunderstood dispatch field is a task the operator did not ask for.
SCHEMA_VERSION: Final = 1

DEFAULT_POLL_INTERVAL_SECONDS: Final = 300
DEFAULT_TRIGGER_LABEL: Final = "worc"
DEFAULT_LABELS_PREFIX: Final = "worc:"
DEFAULT_BRANCH_PREFIX: Final = "worc"
DEFAULT_ID_PREFIX: Final = "gh"
DEFAULT_WORC_COMMAND: Final = "worc"

# worc's own defaults for the two things the connector has to agree with it about: where the task
# lifecycle lives, and how large a task file may be. They are stated here rather than read out of
# worc's home, which the connector never opens — it reaches worc through `worc promote` and
# `worc list` and through nothing else. An operator who changed either in worc restates it here,
# and the connector's loader is what tells them the value is out of range.
DEFAULT_TASKS_DIR: Final = "tasks"
DEFAULT_MAX_TASK_BYTES: Final = 262_144
DEFAULT_MAX_TASK_LINES: Final = 5_000
DEFAULT_MAX_LINE_BYTES: Final = 8_192
DEFAULT_RESEARCH_FLOW: Final = "issue_triage"

# `OWNER/REPO` exactly: two non-empty segments of the characters a host allows in a namespace or a
# repository name. The value is pinned onto every tracker call, so a value with a slash too many (or
# a leading dash that a CLI would read as a flag) must never reach an argument list.
_REPO_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# A tracker name is the entry-point name the composition root looks up, so it stays a lowercase
# token: nothing that could be read as a path, a flag, or a shell word.
_TRACKER_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_-]*$")

# worc's task-id grammar, reproduced because the connector must produce ids worc accepts and cannot
# import worc (it is a test dependency only, never a runtime one).
_TASK_ID_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# Windows resolves these device names — with or without an extension — to the device rather than to
# a file. An id built on such a stem is rejected on every OS so that the same configuration behaves
# identically wherever the connector runs.
_WINDOWS_RESERVED_STEMS: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in "123456789"}
    | {f"LPT{digit}" for digit in "123456789"}
)


def is_valid_repo(value: str) -> bool:
    """Whether ``value`` names one repository as ``OWNER/REPO``.

    Public because ``init`` must refuse a bad value before it writes the file, and the loader must
    refuse it when reading one: two spellings of this rule would be one spelling too many.
    """
    return _REPO_PATTERN.fullmatch(value) is not None


def is_valid_task_id(candidate: str) -> bool:
    """Whether ``candidate`` is a task id worc's validation gate accepts as a path component.

    The grammar, the trailing-dot rule and the Windows device-name rule are worc's, applied here so
    a bad ``task.id_prefix`` is refused at configuration load instead of surfacing as a quarantined
    task file hours later. It lives in this module because the configuration is the leaf every other
    layer may import, and the prefix is the first thing validated against it.
    """
    if _TASK_ID_PATTERN.fullmatch(candidate) is None or candidate.endswith("."):
        return False
    return candidate.split(".", 1)[0].upper() not in _WINDOWS_RESERVED_STEMS


@dataclass(frozen=True)
class GateConfig:
    """The allow rules that decide which items may ever become tasks.

    An empty ``labels`` and an empty ``authors`` with ``allow_all`` false is not a permissive
    configuration but an invalid one: the loader refuses it, because "no rule" must never read as
    "every item".
    """

    labels: tuple[str, ...]
    authors: tuple[str, ...]
    allow_all: bool


@dataclass(frozen=True)
class TaskConfig:
    """The dispatch fields generated tasks carry, and the prefixes ids and branches are built from.

    Every field but the prefixes is optional: a value of ``None`` means the key is not emitted at
    all, so worc's own default stands rather than the connector's opinion of it.
    """

    task_type: str | None
    queue: str | None
    priority: str | None
    commit_type: str | None
    commit_type_by_label: dict[str, str]
    branch_prefix: str
    id_prefix: str
    auto_merge: bool | None


@dataclass(frozen=True)
class WorcConfig:
    """How to reach worc: the launcher, the clone, and the two facts both sides must agree on.

    ``tasks_dir`` and the three limits mirror worc's ``paths.tasks_dir`` and ``validation.max_*``.
    They are configured here, not read from worc's home: the connector's only read out of worc is
    ``worc list``, and a second read would make its own configuration file an incomplete account of
    what it does. The defaults are worc's defaults, so an operator who changed neither writes
    nothing.
    """

    command: str
    repo_path: Path
    tasks_dir: str
    max_task_bytes: int
    max_task_lines: int
    max_line_bytes: int


@dataclass(frozen=True)
class WriteBackConfig:
    """What the connector writes back onto the item: state labels, comments, the close on merge."""

    labels_prefix: str
    comment: bool
    close_on_merge: bool


class ResearchMode(StrEnum):
    """Who analyses a gated item before it becomes an implementation task, if anybody does.

    ``OFF`` is the default and the whole of phases up to the write-back: the item becomes an
    implementation task directly. ``WORC`` queues a triage task into worc first and builds the
    implementation task from the report that task leaves behind. ``LOCAL`` names a research runtime
    the connector would drive itself, which this build does not have — the loader refuses it rather
    than quietly doing something else, because an operator who asked for local research and got
    worc-side triage would be paying for a queue slot they deliberately avoided.
    """

    OFF = "off"
    WORC = "worc"
    LOCAL = "local"


#: The modes this build can actually carry out. ``LOCAL`` is a value of the vocabulary and not of
#: this list on purpose: naming it here is what lets the loader refuse it by name.
IMPLEMENTED_RESEARCH_MODES: Final = frozenset({ResearchMode.OFF, ResearchMode.WORC})


@dataclass(frozen=True)
class ResearchConfig:
    """The optional analysis step: off unless the operator turned it on, and then which flow."""

    mode: ResearchMode
    flow: str

    @property
    def in_worc(self) -> bool:
        """Whether a gated item becomes a worc triage task before it becomes an implementation."""
        return self.mode is ResearchMode.WORC


@dataclass(frozen=True)
class ConnectorConfig:
    """A validated configuration: every value present, every rule checked, nothing to re-derive."""

    schema_version: int
    tracker: str
    repo: str
    poll_interval_seconds: int
    gate: GateConfig
    task: TaskConfig
    worc: WorcConfig
    write_back: WriteBackConfig
    research: ResearchConfig


def _gate(section: Section) -> GateConfig:
    """The gate rules, refusing a section that would admit nothing (or, unstated, everything)."""
    section.reject_unknown("labels", "authors", "allow_all")
    gate = GateConfig(
        labels=section.string_list("labels"),
        authors=section.string_list("authors"),
        allow_all=section.flag("allow_all", default=False),
    )
    if not gate.allow_all and not gate.labels and not gate.authors:
        raise ConfigError(
            "`gate` admits nothing: set `gate.labels` and/or `gate.authors`, or state "
            "`gate.allow_all: true` to accept every item of the repository"
        )
    return gate


def _task(section: Section) -> TaskConfig:
    """The dispatch fields, with the id prefix checked against worc's id grammar."""
    section.reject_unknown(
        "task_type",
        "queue",
        "priority",
        "commit_type",
        "commit_type_by_label",
        "branch_prefix",
        "id_prefix",
        "auto_merge",
    )
    id_prefix = section.string("id_prefix", DEFAULT_ID_PREFIX)
    # Validated through a composed sample id rather than on its own: the prefix is only ever seen by
    # worc as the head of `<prefix>-<number>`, and that is the string that has to be a legal id.
    if not is_valid_task_id(f"{id_prefix}-1"):
        raise ConfigError(
            "`task.id_prefix` must build a valid worc task id: lowercase letters, digits, "
            "`.`, `-` and `_`, and not a Windows device name"
        )
    return TaskConfig(
        task_type=section.optional_string("task_type"),
        queue=section.optional_string("queue"),
        priority=section.optional_string("priority"),
        commit_type=section.optional_string("commit_type"),
        commit_type_by_label=section.string_map("commit_type_by_label"),
        branch_prefix=section.string("branch_prefix", DEFAULT_BRANCH_PREFIX),
        id_prefix=id_prefix,
        auto_merge=section.optional_flag("auto_merge"),
    )


def is_repo_relative_dir(value: str) -> bool:
    """Whether ``value`` names a directory inside the clone, on every operating system.

    worc applies this rule to its own ``paths.tasks_dir``; the connector applies it to its copy of
    the value because that copy becomes a path it writes a task file into. A backslash is refused
    rather than translated so one configuration file means the same directory on Windows and POSIX.
    """
    if not value or "\\" in value or value.startswith("/") or ":" in value:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _worc(section: Section, *, clone_root: Path) -> WorcConfig:
    """How to reach worc, with ``repo_path`` resolved and proven to be a directory."""
    section.reject_unknown(
        "command",
        "repo_path",
        "tasks_dir",
        "max_task_bytes",
        "max_task_lines",
        "max_line_bytes",
    )
    raw_path = section.string("repo_path", ".")
    # Relative to the directory that holds the connector's home, so the documented `.` means "the
    # clone this home sits in" however the operator's shell happened to be positioned.
    repo_path = Path(raw_path) if Path(raw_path).is_absolute() else clone_root / raw_path
    if not repo_path.is_dir():
        raise ConfigError(f"`worc.repo_path` is not a directory: {repo_path.as_posix()}")
    tasks_dir = section.string("tasks_dir", DEFAULT_TASKS_DIR)
    if not is_repo_relative_dir(tasks_dir):
        raise ConfigError(
            "`worc.tasks_dir` must be a directory inside the clone, written with forward "
            "slashes and no `..` segment"
        )
    return WorcConfig(
        command=section.string("command", DEFAULT_WORC_COMMAND),
        repo_path=repo_path.resolve(),
        tasks_dir=tasks_dir,
        max_task_bytes=section.positive_int("max_task_bytes", DEFAULT_MAX_TASK_BYTES),
        max_task_lines=section.positive_int("max_task_lines", DEFAULT_MAX_TASK_LINES),
        max_line_bytes=section.positive_int("max_line_bytes", DEFAULT_MAX_LINE_BYTES),
    )


def _write_back(section: Section) -> WriteBackConfig:
    """What the connector is allowed to write back onto an item."""
    section.reject_unknown("labels_prefix", "comment", "close_on_merge")
    return WriteBackConfig(
        labels_prefix=section.string("labels_prefix", DEFAULT_LABELS_PREFIX),
        comment=section.flag("comment", default=True),
        close_on_merge=section.flag("close_on_merge", default=True),
    )


def _named(modes: Iterable[ResearchMode]) -> str:
    """A set of modes as the operator writes them, for an error that has to list the choices."""
    return ", ".join(sorted(str(mode) for mode in modes))


def _research(section: Section) -> ResearchConfig:
    """The optional analysis step, off unless the operator states otherwise."""
    section.reject_unknown("mode", "flow")
    raw = section.string("mode", str(ResearchMode.OFF))
    try:
        mode = ResearchMode(raw)
    except ValueError:
        raise ConfigError(f"`research.mode` must be one of: {_named(ResearchMode)}") from None
    if mode not in IMPLEMENTED_RESEARCH_MODES:
        raise ConfigError(
            f"`research.mode: {mode}` names a research runtime this build does not carry; "
            f"this build implements: {_named(IMPLEMENTED_RESEARCH_MODES)}"
        )
    return ResearchConfig(mode=mode, flow=section.string("flow", DEFAULT_RESEARCH_FLOW))


def _document(path: Path) -> Section:
    """The parsed top-level mapping of the file at ``path``, or a :class:`ConfigError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"no configuration at {path.as_posix()} — run `worc-connect init` in the clone first"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path.as_posix()}: {exc.strerror}") from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.as_posix()} is not valid YAML: {exc}") from exc
    if loaded is None:
        raise ConfigError(f"{path.as_posix()} is empty")
    return Section(loaded)


def load(path: Path) -> ConnectorConfig:
    """Read and validate the configuration at ``path``, or raise :class:`ConfigError`.

    Nothing is defaulted that could change who gets a task: the gate must state a rule, the
    repository must be a pinnable ``OWNER/REPO``, and the schema version must be one this build
    understands. Paths are resolved against the directory holding the connector's home, so the file
    means the same thing from any working directory.
    """
    document = _document(path)
    document.reject_unknown(
        "schema_version",
        "tracker",
        "repo",
        "poll_interval_seconds",
        "gate",
        "task",
        "worc",
        "write_back",
        "research",
    )
    version = document.positive_int("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ConfigError(
            f"`schema_version` {version} is not supported by this build (expected {SCHEMA_VERSION})"
        )
    tracker = document.string("tracker")
    if _TRACKER_PATTERN.fullmatch(tracker) is None:
        raise ConfigError("`tracker` must be a lowercase name such as `github`")
    repo = document.string("repo")
    if not is_valid_repo(repo):
        raise ConfigError("`repo` must name one repository as `OWNER/REPO`")
    return ConnectorConfig(
        schema_version=version,
        tracker=tracker,
        repo=repo,
        poll_interval_seconds=document.positive_int(
            "poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS
        ),
        gate=_gate(document.section("gate")),
        task=_task(document.section("task")),
        worc=_worc(document.section("worc"), clone_root=path.parent.parent),
        write_back=_write_back(document.section("write_back")),
        research=_research(document.section("research")),
    )
