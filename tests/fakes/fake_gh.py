#!/usr/bin/env python
"""A deterministic stand-in for the GitHub CLI, driven by a scenario file.

The suite never launches the real ``gh``: there is no network, no credential and no repository in a
test, and a test that depended on any of them would be a test that fails for reasons the connector
is not responsible for. This script answers each subcommand from a scenario file and records every
argument list it was called with, so a test can assert both what the connector asked for and — just
as important for a dry run — what it never asked for.

The scenario directory arrives in ``WORC_CONNECT_FAKE_GH_HOME``; nothing is read from anywhere else,
so two tests running in parallel cannot see each other's recordings.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CALLS_FILENAME = "calls.jsonl"
SCENARIO_FILENAME = "scenario.json"
HOME_VARIABLE = "WORC_CONNECT_FAKE_GH_HOME"


def verb_of(argv: list[str]) -> str:
    """The subcommand a call names, as the scenario file keys it (``issue list``, ``pr list``)."""
    return " ".join(argument for argument in argv[:2] if not argument.startswith("-"))


def main(argv: list[str]) -> int:
    """Record the call, then answer it from the scenario; an unscripted verb is a loud failure."""
    home = Path(os.environ[HOME_VARIABLE])
    with (home / CALLS_FILENAME).open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(argv) + "\n")

    scenario = json.loads((home / SCENARIO_FILENAME).read_text(encoding="utf-8"))
    response = scenario.get("responses", {}).get(verb_of(argv))
    if response is None:
        sys.stderr.write(f"fake gh: no response scripted for `{verb_of(argv)}`\n")
        return 1

    sys.stdout.write(response.get("stdout", ""))
    sys.stderr.write(response.get("stderr", ""))
    exit_code: int = response.get("exit_code", 0)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
