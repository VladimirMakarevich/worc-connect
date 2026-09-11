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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

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
DEFAULT_TRIAGE_FLOW: Final = "issue_triage"

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


class ConfigError(Exception):
    """A configuration the connector refuses to run with; the message names the offending key."""


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
    """How to reach worc: the launcher name to resolve and the clone to run it against."""

    command: str
    repo_path: Path


@dataclass(frozen=True)
class WriteBackConfig:
    """What the connector writes back onto the item: state labels, comments, the close on merge."""

    labels_prefix: str
    comment: bool
    close_on_merge: bool


@dataclass(frozen=True)
class TriageConfig:
    """The optional triage path: off unless the operator turned it on, and then which flow."""

    enabled: bool
    flow: str


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
    triage: TriageConfig


class _Section:
    """A typed, key-naming reader over one mapping of the configuration file.

    Every accessor raises :class:`ConfigError` naming the dotted path of the offending key, which
    is the whole reason the reads go through an object rather than ``dict.get``: an operator who
    mistypes a key learns which one, and the process stops instead of running with a default nobody
    chose.
    """

    def __init__(self, data: Any, prefix: str = "") -> None:
        if not isinstance(data, dict):
            raise ConfigError(f"{self._describe(prefix)} must be a mapping")
        self._data: dict[str, Any] = data
        self._prefix = prefix

    @staticmethod
    def _describe(prefix: str) -> str:
        """How this section is named in an error message."""
        return f"`{prefix}`" if prefix else "the configuration file"

    def _key(self, name: str) -> str:
        """The dotted path of ``name`` as the operator wrote it in the file."""
        return f"{self._prefix}.{name}" if self._prefix else name

    def reject_unknown(self, *known: str) -> None:
        """Refuse any key this build does not declare, so a typo cannot pass as a default."""
        for name in sorted(self._data):
            if name not in known:
                raise ConfigError(f"unknown configuration key `{self._key(name)}`")

    def section(self, name: str) -> _Section:
        """A reader over the nested mapping at ``name``; an absent section reads as empty."""
        return _Section(self._data.get(name, {}), self._key(name))

    def string(self, name: str, default: str | None = None) -> str:
        """A non-blank string; ``default`` applies when absent, otherwise the key is required."""
        value = self.optional_string(name)
        if value is not None:
            return value
        if default is None:
            raise ConfigError(f"`{self._key(name)}` is required")
        return default

    def optional_string(self, name: str) -> str | None:
        """A non-blank string, or ``None`` when the key is absent — which means "do not emit it"."""
        if name not in self._data:
            return None
        value = self._data[name]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"`{self._key(name)}` must be a non-empty string")
        return value.strip()

    def flag(self, name: str, default: bool) -> bool:
        """A boolean; a value that is not a real boolean is an error, never a truthiness test."""
        value = self.optional_flag(name)
        return default if value is None else value

    def optional_flag(self, name: str) -> bool | None:
        """A boolean, or ``None`` when the key is absent."""
        if name not in self._data:
            return None
        value = self._data[name]
        if not isinstance(value, bool):
            raise ConfigError(f"`{self._key(name)}` must be true or false")
        return value

    def positive_int(self, name: str, default: int) -> int:
        """An integer above zero; a boolean is rejected even though Python counts it as an int."""
        if name not in self._data:
            return default
        value = self._data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"`{self._key(name)}` must be a positive integer")
        return value

    def string_list(self, name: str) -> tuple[str, ...]:
        """A list of non-blank strings; an absent key reads as the empty list."""
        if name not in self._data:
            return ()
        value = self._data[name]
        if not isinstance(value, list):
            raise ConfigError(f"`{self._key(name)}` must be a list of strings")
        entries: list[str] = []
        for entry in value:
            if not isinstance(entry, str) or not entry.strip():
                raise ConfigError(f"`{self._key(name)}` must contain non-empty strings only")
            entries.append(entry.strip())
        return tuple(entries)

    def string_map(self, name: str) -> dict[str, str]:
        """A mapping of non-blank strings to non-blank strings; an absent key reads as empty."""
        if name not in self._data:
            return {}
        value = self._data[name]
        if not isinstance(value, dict):
            raise ConfigError(f"`{self._key(name)}` must be a mapping of strings to strings")
        mapping: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise ConfigError(f"`{self._key(name)}` must map strings to strings")
            if not raw_key.strip() or not raw_value.strip():
                raise ConfigError(f"`{self._key(name)}` must not contain empty keys or values")
            mapping[raw_key.strip()] = raw_value.strip()
        return mapping


def _gate(section: _Section) -> GateConfig:
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


def _task(section: _Section) -> TaskConfig:
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


def _worc(section: _Section, *, clone_root: Path) -> WorcConfig:
    """How to reach worc, with ``repo_path`` resolved and proven to be a directory."""
    section.reject_unknown("command", "repo_path")
    raw_path = section.string("repo_path", ".")
    # Relative to the directory that holds the connector's home, so the documented `.` means "the
    # clone this home sits in" however the operator's shell happened to be positioned.
    repo_path = Path(raw_path) if Path(raw_path).is_absolute() else clone_root / raw_path
    if not repo_path.is_dir():
        raise ConfigError(f"`worc.repo_path` is not a directory: {repo_path.as_posix()}")
    return WorcConfig(
        command=section.string("command", DEFAULT_WORC_COMMAND),
        repo_path=repo_path.resolve(),
    )


def _write_back(section: _Section) -> WriteBackConfig:
    """What the connector is allowed to write back onto an item."""
    section.reject_unknown("labels_prefix", "comment", "close_on_merge")
    return WriteBackConfig(
        labels_prefix=section.string("labels_prefix", DEFAULT_LABELS_PREFIX),
        comment=section.flag("comment", default=True),
        close_on_merge=section.flag("close_on_merge", default=True),
    )


def _triage(section: _Section) -> TriageConfig:
    """The optional triage path, off unless the operator states otherwise."""
    section.reject_unknown("enabled", "flow")
    return TriageConfig(
        enabled=section.flag("enabled", default=False),
        flow=section.string("flow", DEFAULT_TRIAGE_FLOW),
    )


def _document(path: Path) -> _Section:
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
    return _Section(loaded)


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
        "triage",
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
        triage=_triage(document.section("triage")),
    )
