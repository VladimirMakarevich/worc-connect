"""Shared fixtures: a clone, the connector's home in it, and the fake ``gh`` first on ``PATH``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support import FAKE_GH_SCRIPT, FakeGh, install_launcher
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


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    """A fake ``gh`` first on ``PATH``, answering nothing until a test scripts a response."""
    scenario_home = tmp_path / "fake-gh"
    scenario_home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_launcher(bin_dir, "gh", FAKE_GH_SCRIPT)
    monkeypatch.setenv("WORC_CONNECT_FAKE_GH_HOME", str(scenario_home))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    handle = FakeGh(home=scenario_home, bin_dir=bin_dir)
    handle.respond("__none__")  # writes an empty scenario file so an unscripted call fails cleanly
    return handle
