"""The file-size gate: budgets come from pyproject, the smallest matching budget wins."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[1] / "tools" / "size_gate.py"


def _load_gate():  # type: ignore[no-untyped-def]  # a script, not a package: loaded by path
    spec = importlib.util.spec_from_file_location("size_gate", _GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["size_gate"] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * lines, encoding="utf-8", newline="")


def test_defaults_apply_without_a_table(tmp_path: Path) -> None:
    gate = _load_gate()
    _write(tmp_path / "src" / "pkg" / "big.py", 501)
    _write(tmp_path / "src" / "pkg" / "ok.py", 500)
    out = gate.findings(tmp_path, gate.load_budgets(tmp_path))
    assert len(out) == 1
    assert out[0].startswith("src/pkg/big.py:1: file has 501 lines, budget 500")


def test_pyproject_budgets_and_smallest_match_wins(tmp_path: Path) -> None:
    gate = _load_gate()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.size_gate.max_lines]\n"src/**/*.py" = 100\n"src/pkg/cli.py" = 40\n',
        encoding="utf-8",
    )
    _write(tmp_path / "src" / "pkg" / "cli.py", 50)  # under the loose glob, over the narrow one
    _write(tmp_path / "src" / "pkg" / "core.py", 100)
    out = gate.findings(tmp_path, gate.load_budgets(tmp_path))
    assert out == [
        (
            "src/pkg/cli.py:1: file has 50 lines, budget 40 — split the module rather than "
            "raising the budget"
        )
    ]


def test_bad_budget_is_an_operational_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gate = _load_gate()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.size_gate.max_lines]\n"src/**/*.py" = 0\n', encoding="utf-8"
    )
    assert gate.main(["--root", str(tmp_path)]) == 2
    assert "budget must be a positive int" in capsys.readouterr().err


def test_this_repository_is_within_budget() -> None:
    gate = _load_gate()
    root = _GATE.parents[1]
    assert gate.findings(root, gate.load_budgets(root)) == []
