"""Test support: configuration documents, normalized items, payloads, and the fake ``gh``.

Imported by name rather than relatively: the suite deliberately has no ``__init__.py`` under
``tests/``, so pytest puts this directory on the import path and every module here is a top-level
import for the tests and for ``conftest``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Any

import pytest
import yaml

from worc_connect.config import (
    ConnectorConfig,
    GateConfig,
    TaskConfig,
    TriageConfig,
    WorcConfig,
    WriteBackConfig,
)
from worc_connect.core.items import (
    ItemState,
    PullRequest,
    PullRequestState,
    WorkItem,
    WorkItemState,
)
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerUnavailable

FAKES = Path(__file__).resolve().parent / "fakes"
FAKE_GH_SCRIPT = FAKES / "fake_gh.py"
FAKE_WORC_SCRIPT = FAKES / "fake_worc.py"
FAKE_GIT_SCRIPT = FAKES / "fake_git.py"

ISSUE_FIELDS = "number,title,body,author,labels,updatedAt,url"


def _distribution_is_installed() -> bool:
    """Whether this checkout's own distribution metadata is readable from here."""
    try:
        metadata("worc-connect")
    except PackageNotFoundError:
        return False
    return True


# The entry-point group and the extras are properties of the *installed* distribution, and the CLI
# reaches an adapter only through that group. A dev virtualenv and CI both install the package, so
# they assert all of it; an environment that lints the working tree without installing anything has
# nothing to assert, and saying so is better than mocking the mechanism under test.
requires_installed_distribution = pytest.mark.skipif(
    not _distribution_is_installed(),
    reason='no installed distribution here: run `pip install -e ".[dev]"` to include these',
)


def _worc_is_importable() -> bool:
    """Whether worc itself can be imported here, so its real gate can judge a generated file."""
    return importlib.util.find_spec("wastech_orchestrator") is not None


# worc is a *test* dependency, installed from its repository rather than from an index: the
# connector must never import it at runtime, and the one assertion worth making against the real
# thing is that a file the builder produced passes worc's own validation gate unchanged. CI installs
# it and so does a developer who ran the second install command; the bare environment pre-commit
# builds to lint the tree does not, and saying so beats reimplementing the gate to have something to
# assert against.
requires_worc = pytest.mark.skipif(
    not _worc_is_importable(),
    reason="worc is not installed here: `pip install -r requirements-worc.txt` to include these",
)


def base_config(*, repo: str = "OWNER/REPO") -> dict[str, Any]:
    """A configuration the loader accepts, as a mapping tests edit before writing it out."""
    return {
        "schema_version": 1,
        "tracker": "github",
        "repo": repo,
        "poll_interval_seconds": 300,
        "gate": {"labels": ["worc"], "authors": [], "allow_all": False},
        "task": {"branch_prefix": "worc", "id_prefix": "gh"},
        "worc": {"command": "worc", "repo_path": ".", "tasks_dir": "tasks"},
        "write_back": {"labels_prefix": "worc:", "comment": True, "close_on_merge": True},
        "triage": {"enabled": False, "flow": "issue_triage"},
    }


def write_config(home: ConnectorHome, document: dict[str, Any] | str) -> Path:
    """Write a configuration into ``home`` and return its path."""
    home.path.mkdir(parents=True, exist_ok=True)
    text = document if isinstance(document, str) else yaml.safe_dump(document, sort_keys=False)
    home.config_path.write_text(text, encoding="utf-8", newline="")
    return home.config_path


def work_item(
    identifier: str = "142",
    *,
    title: str = "Signup form accepts foo@ as an email",
    body: str = "The validator lets a bare domain through.",
    author: str = "reporter",
    labels: tuple[str, ...] = ("worc",),
    updated_at: datetime | None = None,
    state: WorkItemState = WorkItemState.OPEN,
) -> WorkItem:
    """A normalized item, with everything a test does not care about already filled in."""
    return WorkItem(
        identifier=identifier,
        title=title,
        body=body,
        author=author,
        labels=labels,
        state=state,
        updated_at=updated_at or datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
        url=f"https://github.com/OWNER/REPO/issues/{identifier}",
    )


def issue_payload(item: WorkItem) -> dict[str, Any]:
    """``item`` in the shape ``gh issue list --json`` returns it in."""
    return {
        "number": int(item.identifier),
        "title": item.title,
        "body": item.body,
        "author": {"login": item.author},
        "labels": [{"name": name} for name in item.labels],
        "updatedAt": item.updated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": item.url,
    }


@dataclass
class FakeGh:
    """A handle on the fake ``gh``: what it will answer, and what it was asked."""

    home: Path
    bin_dir: Path
    _responses: dict[str, dict[str, Any]] = field(default_factory=dict)

    def respond(
        self,
        verb: str,
        *,
        payload: Any = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> None:
        """Script the answer to one subcommand (``issue list``, ``pr list``, …)."""
        body = json.dumps(payload) if payload is not None else stdout
        self._responses[verb] = {"stdout": body, "stderr": stderr, "exit_code": exit_code}
        self._flush()

    @property
    def calls(self) -> list[list[str]]:
        """Every argument list the connector launched, in order."""
        path = self.home / "calls.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def calls_for(self, verb: str) -> list[list[str]]:
        """The recorded calls whose subcommand is ``verb``."""
        return [call for call in self.calls if _verb_of(call) == verb]

    @property
    def environment(self) -> dict[str, str]:
        """The environment the last call was given."""
        return _recorded_environment(self.home)

    def _flush(self) -> None:
        (self.home / "scenario.json").write_text(
            json.dumps({"responses": self._responses}), encoding="utf-8", newline=""
        )


def _recorded_environment(home: Path) -> dict[str, str]:
    """The environment a fake executable recorded on its last call."""
    path = home / "environment.json"
    if not path.is_file():
        return {}
    loaded: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _verb_of(argv: list[str]) -> str:
    return " ".join(argument for argument in argv[:2] if not argument.startswith("-"))


@dataclass
class FakeWorc:
    """A handle on the fake worc: what it will answer, and what it was asked."""

    home: Path
    bin_dir: Path
    _scenario: dict[str, Any] = field(default_factory=lambda: {"tasks_dir": "tasks"})

    def configure(self, **scenario: Any) -> None:
        """Set or replace scenario values (``tasks_dir``, ``entries``, ``promote``, ``list``)."""
        self._scenario.update(scenario)
        (self.home / "scenario.json").write_text(
            json.dumps(self._scenario), encoding="utf-8", newline=""
        )

    def entries(self, **statuses: str) -> None:
        """Script ``worc list --format json`` as one entry per ``task_id=status`` pair."""
        self.configure(
            entries=[
                {"task_id": task_id, "status": status, "title": None, "branch": None}
                for task_id, status in statuses.items()
            ]
        )

    @property
    def calls(self) -> list[list[str]]:
        """Every argument list the connector launched worc with, in order."""
        path = self.home / "calls.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    @property
    def environment(self) -> dict[str, str]:
        """The environment the last call was given."""
        return _recorded_environment(self.home)


@dataclass
class FakeGit:
    """A handle on the recording ``git``: what, if anything, the connector tried to run."""

    home: Path

    @property
    def calls(self) -> list[list[str]]:
        """Every argument list git was launched with — which must always be none of them."""
        path = self.home / "calls.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def pull_request(
    number: int = 201,
    *,
    state: PullRequestState = PullRequestState.OPEN,
    merged_at: datetime | None = None,
) -> PullRequest:
    """A pull request as the core sees it, with everything a test does not care about filled in."""
    return PullRequest(
        number=number,
        url=f"https://github.com/OWNER/REPO/pull/{number}",
        state=state,
        merged_at=merged_at,
    )


def install_launcher(bin_dir: Path, name: str, script: Path) -> None:
    """Put a runnable executable called ``name`` on disk that runs ``script``.

    The connector resolves its tools with ``shutil.which``, so a fake has to be a real executable of
    the real name — which is a ``.cmd`` shim on Windows and a shebang script elsewhere. Both are
    generated here rather than in a test, so no test carries a platform assumption.
    """
    if os.name == "nt":
        launcher = bin_dir / f"{name}.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8", newline="")
        return
    launcher = bin_dir / name
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8", newline=""
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def task_config(
    *,
    task_type: str | None = None,
    queue: str | None = None,
    priority: str | None = None,
    commit_type: str | None = None,
    commit_type_by_label: dict[str, str] | None = None,
    branch_prefix: str = "worc",
    id_prefix: str = "gh",
    auto_merge: bool | None = None,
) -> TaskConfig:
    """The dispatch fields of a generated task, with nothing emitted unless a test asks for it."""
    return TaskConfig(
        task_type=task_type,
        queue=queue,
        priority=priority,
        commit_type=commit_type,
        commit_type_by_label=commit_type_by_label or {},
        branch_prefix=branch_prefix,
        id_prefix=id_prefix,
        auto_merge=auto_merge,
    )


def connector_config(
    clone: Path,
    *,
    tracker: str = "github",
    repo: str = "OWNER/REPO",
    poll_interval_seconds: int = 300,
    labels: tuple[str, ...] = ("worc",),
    authors: tuple[str, ...] = (),
    allow_all: bool = False,
    task: TaskConfig | None = None,
    labels_prefix: str = "worc:",
    comment: bool = True,
    close_on_merge: bool = True,
    tasks_dir: str = "tasks",
    max_task_bytes: int = 262_144,
    max_task_lines: int = 5_000,
    max_line_bytes: int = 8_192,
) -> ConnectorConfig:
    """A validated configuration built directly, for tests that are not about the loader."""
    return ConnectorConfig(
        schema_version=1,
        tracker=tracker,
        repo=repo,
        poll_interval_seconds=poll_interval_seconds,
        gate=GateConfig(labels=labels, authors=authors, allow_all=allow_all),
        task=task or task_config(),
        worc=WorcConfig(
            command="worc",
            repo_path=clone,
            tasks_dir=tasks_dir,
            max_task_bytes=max_task_bytes,
            max_task_lines=max_task_lines,
            max_line_bytes=max_line_bytes,
        ),
        write_back=WriteBackConfig(
            labels_prefix=labels_prefix, comment=comment, close_on_merge=close_on_merge
        ),
        triage=TriageConfig(enabled=False, flow="issue_triage"),
    )


@dataclass
class StubAdapter:
    """A second tracker adapter, in the tests only, that no GitHub code path runs through.

    Its existence is the proof the core asked for: a full tick drives this object through the same
    protocol the real adapter implements, so anything the loop learned about GitHub specifically
    would show up here as a missing method rather than as a passing test.

    The write side records instead of sending, and reads the current state back off the item's own
    labels exactly as a real adapter does — so "re-applying a state is a no-op" is asserted against
    the same mechanism the product uses, not against a convenient shortcut.
    """

    items: list[WorkItem] = field(default_factory=list)
    failures: list[Exception] = field(default_factory=list)
    pull_request: PullRequest | None = None
    pull_requests: dict[int, PullRequest] = field(default_factory=dict)
    labels_prefix: str = "worc:"
    listed_since: list[datetime | None] = field(default_factory=list)
    states: list[tuple[str, str, str | None]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)
    closed: list[tuple[str, str]] = field(default_factory=list)
    ensured: list[tuple[ItemState, ...]] = field(default_factory=list)
    found_by_branch: list[str] = field(default_factory=list)
    read_by_number: list[int] = field(default_factory=list)

    def list_items(self, since: datetime | None) -> list[WorkItem]:
        self.listed_since.append(since)
        if self.failures:
            raise self.failures.pop(0)
        return list(self.items)

    def get_item(self, identifier: str) -> WorkItem:
        for item in self.items:
            if item.identifier == identifier:
                return item
        raise TrackerUnavailable(f"no item {identifier}")

    def find_pull_request(self, branch: str) -> PullRequest | None:
        self.found_by_branch.append(branch)
        return self.pull_request

    def get_pull_request(self, number: int) -> PullRequest:
        self.read_by_number.append(number)
        found = self.pull_requests.get(number) or self.pull_request
        if found is None:
            raise TrackerUnavailable(f"no pull request {number}")
        return found

    def current_state(self, item: WorkItem) -> ItemState | None:
        for label in item.labels:
            if label.startswith(self.labels_prefix):
                name = label[len(self.labels_prefix) :]
                if name in {str(state) for state in ItemState}:
                    return ItemState(name)
        return None

    def set_state(self, identifier: str, state: ItemState, *, previous: ItemState | None) -> None:
        self.states.append((identifier, str(state), None if previous is None else str(previous)))
        self._relabel(identifier, state)

    def comment(self, identifier: str, body_path: Path) -> None:
        self.comments.append((identifier, body_path.read_text(encoding="utf-8")))

    def close(self, identifier: str, message: str) -> None:
        self.closed.append((identifier, message))
        # A closed item leaves the open listing, exactly as it does on a real tracker — which is
        # what keeps a merged, closed item from being re-triggered on the very next tick.
        self.items = [item for item in self.items if item.identifier != identifier]

    def ensure_labels(self, states: tuple[ItemState, ...]) -> None:
        self.ensured.append(states)

    def _relabel(self, identifier: str, state: ItemState) -> None:
        """Put the new state on the stub's own copy of the item, as the tracker would."""
        for index, item in enumerate(self.items):
            if item.identifier == identifier:
                kept = tuple(
                    label for label in item.labels if not label.startswith(self.labels_prefix)
                )
                self.items[index] = replace(item, labels=(*kept, f"{self.labels_prefix}{state}"))
