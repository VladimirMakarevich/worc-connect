"""Shared fixtures: a clone, the connector's home in it, and fake ``gh`` / ``worc`` on ``PATH``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support import (
    FAKE_GH_SCRIPT,
    FAKE_GIT_SCRIPT,
    FAKE_WORC_SCRIPT,
    FakeGh,
    FakeGit,
    FakeWorc,
    install_launcher,
)
from worc_connect.home import ConnectorHome


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A directory standing in for the operator's clone."""
    path = tmp_path / "clone"
    path.mkdir()
    return path


@pytest.fixture
def home(clone: Path) -> ConnectorHome:
    """The connector's home inside the clone; nothing is created until a test asks for it."""
    return ConnectorHome.beside(clone)


def _launcher_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The directory the fakes are installed in, first on ``PATH`` for the duration of a test.

    Shared by both fakes so a test can use them together: the connector resolves ``gh`` and
    ``worc`` with ``shutil.which``, and the real ones must never win — on a developer's machine
    both are usually installed.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    """A fake ``gh`` first on ``PATH``, answering nothing until a test scripts a response."""
    scenario_home = tmp_path / "fake-gh"
    scenario_home.mkdir()
    bin_dir = _launcher_dir(tmp_path, monkeypatch)
    install_launcher(bin_dir, "gh", FAKE_GH_SCRIPT)
    monkeypatch.setenv("WORC_CONNECT_FAKE_GH_HOME", str(scenario_home))
    handle = FakeGh(home=scenario_home, bin_dir=bin_dir)
    handle.respond("__none__")  # writes an empty scenario file so an unscripted call fails cleanly
    return handle


@pytest.fixture
def fake_worc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeWorc:
    """A fake ``worc`` first on ``PATH``, with an empty listing until a test scripts one."""
    scenario_home = tmp_path / "fake-worc"
    scenario_home.mkdir()
    bin_dir = _launcher_dir(tmp_path, monkeypatch)
    install_launcher(bin_dir, "worc", FAKE_WORC_SCRIPT)
    monkeypatch.setenv("WORC_CONNECT_FAKE_WORC_HOME", str(scenario_home))
    handle = FakeWorc(home=scenario_home, bin_dir=bin_dir)
    handle.configure()
    return handle


@pytest.fixture
def refuse_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    """A recording ``git`` first on ``PATH``, so "the connector ran none" is an assertion."""
    scenario_home = tmp_path / "fake-git"
    scenario_home.mkdir()
    bin_dir = _launcher_dir(tmp_path, monkeypatch)
    install_launcher(bin_dir, "git", FAKE_GIT_SCRIPT)
    monkeypatch.setenv("WORC_CONNECT_FAKE_GIT_HOME", str(scenario_home))
    return FakeGit(home=scenario_home)
