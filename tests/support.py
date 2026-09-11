"""Test support: configuration documents, normalized items, payloads, and the fake ``gh``.

Imported by name rather than relatively: the suite deliberately has no ``__init__.py`` under
``tests/``, so pytest puts this directory on the import path and every module here is a top-level
import for the tests and for ``conftest``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
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
from worc_connect.core.items import PullRequest, WorkItem, WorkItemState
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerUnavailable

FAKES = Path(__file__).resolve().parent / "fakes"
FAKE_GH_SCRIPT = FAKES / "fake_gh.py"

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


def base_config(*, repo: str = "OWNER/REPO") -> dict[str, Any]:
    """A configuration the loader accepts, as a mapping tests edit before writing it out."""
    return {
        "schema_version": 1,
        "tracker": "github",
        "repo": repo,
        "poll_interval_seconds": 300,
        "gate": {"labels": ["worc"], "authors": [], "allow_all": False},
        "task": {"branch_prefix": "worc", "id_prefix": "gh"},
        "worc": {"command": "worc", "repo_path": "."},
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

    def _flush(self) -> None:
        (self.home / "scenario.json").write_text(
            json.dumps({"responses": self._responses}), encoding="utf-8", newline=""
        )


def _verb_of(argv: list[str]) -> str:
    return " ".join(argument for argument in argv[:2] if not argument.startswith("-"))


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


def connector_config(
    clone: Path,
    *,
    tracker: str = "github",
    repo: str = "OWNER/REPO",
    poll_interval_seconds: int = 300,
    labels: tuple[str, ...] = ("worc",),
    authors: tuple[str, ...] = (),
    allow_all: bool = False,
) -> ConnectorConfig:
    """A validated configuration built directly, for tests that are not about the loader."""
    return ConnectorConfig(
        schema_version=1,
        tracker=tracker,
        repo=repo,
        poll_interval_seconds=poll_interval_seconds,
        gate=GateConfig(labels=labels, authors=authors, allow_all=allow_all),
        task=TaskConfig(
            task_type=None,
            queue=None,
            priority=None,
            commit_type=None,
            commit_type_by_label={},
            branch_prefix="worc",
            id_prefix="gh",
            auto_merge=None,
        ),
        worc=WorcConfig(command="worc", repo_path=clone),
        write_back=WriteBackConfig(labels_prefix="worc:", comment=True, close_on_merge=True),
        triage=TriageConfig(enabled=False, flow="issue_triage"),
    )


@dataclass
class StubAdapter:
    """A second tracker adapter, in the tests only, that no GitHub code path runs through.

    Its existence is the proof the core asked for: a full tick drives this object through the same
    protocol the real adapter implements, so anything the loop learned about GitHub specifically
    would show up here as a missing method rather than as a passing test.
    """

    items: list[WorkItem] = field(default_factory=list)
    failures: list[Exception] = field(default_factory=list)
    pull_request: PullRequest | None = None
    listed_since: list[datetime | None] = field(default_factory=list)

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
        return self.pull_request
