#!/usr/bin/env python
"""Stop-hook docs-sync gate for worc-connect.

When a turn ends with code changed under ``src/`` but no documentation change, this blocks the stop
once and reminds Claude to sync the docs (or to state that the change has no doc impact). It is the
deterministic backstop for the "keep docs in sync in the same change" rule in AGENTS.md.

A loop guard (``stop_hook_active``) surfaces the reminder at most once per stop-cycle — it never
spins. Cross-platform and side-effect-free: it only reads ``git status`` (fixed argv, no shell) and
prints a decision. Tests-only / config-only / doc-only change sets never trigger it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# What counts as "documentation" for the purpose of this gate (git uses forward slashes).
_DOC_FILES = ("README.md", "AGENTS.md")
_DOC_PREFIXES = (".agents/", "docs/", "src/worc_connect/packaged/")


def _changed_paths() -> list[str]:
    """Repo-relative paths with any working-tree change (staged, unstaged, or untracked)."""
    proc = subprocess.run(  # fixed argv, no shell, no user input
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) <= 3:
            continue
        entry = line[3:]  # drop the two-char XY status + the separating space
        if " -> " in entry:  # a rename is reported as "old -> new"; the new path is what changed
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip().strip('"'))
    return paths


def _should_block(paths: list[str]) -> bool:
    """True when code under ``src/`` changed but no documentation did (pure, testable)."""
    code_changed = any(p.startswith("src/") and not p.startswith(_DOC_PREFIXES) for p in paths)
    docs_changed = any(p.startswith(_DOC_PREFIXES) or p in _DOC_FILES for p in paths)
    return code_changed and not docs_changed


_REASON = (
    "You changed code under src/ this session but touched no documentation. If this affects "
    "behavior, the CLI, the configuration file, or the contract with worc, update the relevant "
    "docs in the same change: README.md, .agents/rules/, docs/backlog/, and any operator-facing "
    "file under src/worc_connect/packaged/. If the change has no documentation impact, say so "
    "explicitly and finish."
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    # Surface the reminder at most once per stop-cycle, so we never loop.
    if payload.get("stop_hook_active"):
        return 0

    if _should_block(_changed_paths()):
        print(json.dumps({"decision": "block", "reason": _REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
