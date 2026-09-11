"""The connector holds nothing worth stealing, and hands nothing extra to the tools it launches.

Authentication belongs to the operator: ``gh auth login`` for the tracker, and whatever worc was
installed with for worc. The connector has no token to store, log, leak or forward, and these tests
hold every surface where one could appear to that: its configuration, its state schema, and the
environment its child processes are given.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import fields
from pathlib import Path

import pytest

from support import FakeGh, FakeWorc, StubAdapter, connector_config, issue_payload, work_item
from worc_connect.config import ConnectorConfig, GateConfig, TaskConfig, WorcConfig, WriteBackConfig
from worc_connect.core.loop import Watcher
from worc_connect.core.state import StateStore
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome
from worc_connect.trackers.github.adapter import GitHubAdapter
from worc_connect.trackers.github.gh import GhCommand

SECRET_WORDS = ("token", "secret", "password", "credential", "api_key", "access_key")

# A launcher on POSIX is a shell shim, and `sh` maintains these itself. They are the shell's
# bookkeeping, not something the connector put there — which is exactly what the assertion is about.
SHELL_BOOKKEEPING = frozenset({"PWD", "OLDPWD", "SHLVL", "_"})


@pytest.mark.parametrize(
    "section", [ConnectorConfig, GateConfig, TaskConfig, WorcConfig, WriteBackConfig]
)
def test_no_configuration_key_could_hold_a_credential(section: type) -> None:
    names = [field.name.casefold() for field in fields(section)]

    assert not [name for name in names if any(word in name for word in SECRET_WORDS)]


def test_no_state_column_could_hold_a_credential(home: ConnectorHome) -> None:
    store = StateStore(home.state_path)
    try:
        with sqlite3.connect(home.state_path) as connection:
            columns = [row[1].casefold() for row in connection.execute("PRAGMA table_info(items)")]
    finally:
        store.close()

    assert columns
    assert not [name for name in columns if any(word in name for word in SECRET_WORDS)]


def added_by(child: dict[str, str]) -> dict[str, str]:
    """What a child process was given that the connector's own environment does not have."""
    parent = dict(os.environ)
    return {
        key: value
        for key, value in child.items()
        if key not in SHELL_BOOKKEEPING and parent.get(key) != value
    }


@pytest.mark.slow
def test_gh_is_given_the_connectors_own_environment_and_nothing_more(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", payload=[issue_payload(work_item())])

    GitHubAdapter(command=GhCommand(repo="OWNER/REPO"), labels_prefix="worc:").list_items(None)

    assert fake_gh.environment  # the recording happened at all
    assert added_by(fake_gh.environment) == {}


@pytest.mark.slow
def test_worc_is_given_the_connectors_own_environment_and_nothing_more(
    home: ConnectorHome, fake_worc: FakeWorc
) -> None:
    WorcCommand(command="worc", repo_path=home.clone_path).list_tasks()

    assert fake_worc.environment
    assert added_by(fake_worc.environment) == {}


@pytest.mark.slow
def test_a_full_tick_writes_no_secret_anywhere_it_owns(
    home: ConnectorHome, fake_worc: FakeWorc, tmp_path: Path
) -> None:
    store = StateStore(home.state_path)
    try:
        Watcher(
            config=connector_config(home.clone_path),
            adapter=StubAdapter(items=[work_item()]),
            store=store,
            home=home,
            worc=WorcCommand(command="worc", repo_path=home.clone_path),
        ).tick(dry_run=False)
    finally:
        store.close()

    written = b"".join(path.read_bytes() for path in home.path.rglob("*") if path.is_file()).lower()
    for word in (b"authorization", b"bearer ", b"ghp_", b"github_pat_"):
        assert word not in written
