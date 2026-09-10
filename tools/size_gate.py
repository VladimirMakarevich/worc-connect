#!/usr/bin/env python3
"""Fail when a source file outgrows its line budget.

ruff bounds a *function* (statements, branches, complexity) but has no rule for the size of a
*module*, and a module is what a reader or an agent has to load whole: a 5,000-line file is a cost
on every task that touches it, and it grows one reasonable-looking addition at a time. This gate
makes the budget explicit — per glob, in ``pyproject.toml`` under ``[tool.size_gate.max_lines]`` —
so a module that crosses it fails the commit that crossed it, with the only correct fix a split.

Physical lines are counted (docstrings and comments included) because that is what the reader pays
for. A budget is a ratchet: lower it as the code allows, never raise it to silence a finding.

Exit codes: ``0`` clean, ``1`` findings, ``2`` operational failure (unreadable configuration).
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

# Applied when pyproject.toml declares no [tool.size_gate.max_lines] table, so the gate is never
# silently inert: a repository that forgets to configure it still gets a strict default.
DEFAULT_BUDGETS: dict[str, int] = {"src/**/*.py": 500, "tests/**/*.py": 800}


def load_budgets(root: Path) -> dict[str, int]:
    """The glob → line-budget table from ``pyproject.toml``, or the defaults when absent."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return dict(DEFAULT_BUDGETS)
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    table = data.get("tool", {}).get("size_gate", {}).get("max_lines")
    if table is None:
        return dict(DEFAULT_BUDGETS)
    budgets: dict[str, int] = {}
    for pattern, limit in table.items():
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError(
                f"[tool.size_gate.max_lines] {pattern!r}: budget must be a positive int"
            )
        budgets[str(pattern)] = limit
    return budgets


def count_lines(path: Path) -> int:
    """Physical lines in *path*, read as UTF-8 with undecodable bytes replaced (a count, not a parse)."""  # noqa: E501
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def findings(root: Path, budgets: dict[str, int]) -> list[str]:
    """One ``path:1: …`` line per file over budget, sorted; paths are POSIX so output matches on
    every OS.

    A file matched by several globs is held to the smallest budget among them: the narrower pattern
    is the deliberate one, and a looser match must not widen it.
    """
    limit_for: dict[Path, int] = {}
    for pattern, limit in budgets.items():
        for path in root.glob(pattern):
            if path.is_file():
                limit_for[path] = min(limit, limit_for.get(path, limit))
    out: list[str] = []
    for path in sorted(limit_for):
        lines = count_lines(path)
        limit = limit_for[path]
        if lines > limit:
            rel = path.relative_to(root).as_posix()
            out.append(
                f"{rel}:1: file has {lines} lines, budget {limit} — split the module rather than "
                "raising the budget"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    """Run the gate over *root* (the repository by default) and report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root holding pyproject.toml (default: this checkout)",
    )
    args = parser.parse_args(argv)
    try:
        budgets = load_budgets(args.root)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        sys.stderr.write(f"size-gate: cannot read budgets: {exc}\n")
        return 2
    problems = findings(args.root, budgets)
    for line in problems:
        sys.stdout.write(line + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
