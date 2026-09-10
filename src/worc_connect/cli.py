"""Command-line entry point (the composition root).

The subcommands — ``init``, ``watch [--once] [--dry-run]``, ``status``, ``install-flow`` — arrive
with the connector's feature work. Today the executable reports its version and its usage, which is
enough to verify packaging, the console script and the quality gates before any behaviour exists.
"""

from __future__ import annotations

import argparse

from worc_connect import __version__


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, built once so ``--help`` and tests read the same surface."""
    parser = argparse.ArgumentParser(
        prog="worc-connect",
        description="Tracker connector for wastech-orchestrator: gated items in, worc tasks out.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the process exit code.

    Without a subcommand there is nothing to do yet, so usage is printed and the exit code is 2 —
    the conventional "usage error" — rather than a silent 0 that would look like a successful run.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    del args  # no subcommands yet: parsing only validates the flags
    parser.print_usage()
    return 2
