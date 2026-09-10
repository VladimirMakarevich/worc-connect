#!/usr/bin/env python3
"""Run import-linter against the working tree's ``src/`` without installing the package.

import-linter locates the root package through the interpreter's import path, so out of the box it
lints whatever copy of ``worc_connect`` is importable. In an environment that pre-commit builds and
owns the package is not installed at all — and installing it there would lint a stale snapshot taken
when the environment was created, not the tree being committed. Putting ``src/`` first on the path
makes the contracts read the files in this checkout, which is the only copy that matters.

Exit code is import-linter's own: ``0`` kept, ``1`` broken, ``2`` a configuration problem.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def main() -> int:
    """Insert ``src/`` ahead of everything else, then hand over to import-linter's CLI."""
    sys.path.insert(0, str(SRC))
    from importlinter.cli import lint_imports  # imported late: the path must be set first

    result: int = lint_imports()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
