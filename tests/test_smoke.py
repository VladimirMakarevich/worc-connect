"""Packaging smoke: the package imports, the console entry point runs, the version is reported."""

from __future__ import annotations

import pytest

from worc_connect import __version__
from worc_connect.cli import main


def test_version_is_a_pep440_string() -> None:
    assert __version__
    assert __version__[0].isdigit()


def test_cli_reports_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_without_subcommand_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out
