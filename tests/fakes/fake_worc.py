#!/usr/bin/env python
"""A deterministic stand-in for worc, driven by a scenario file.

The suite never launches the real worc: a test has no clone worc would accept, no state database
and no daemon, and a test that depended on any of them would fail for reasons the connector is not
responsible for. This script implements the two surfaces the connector is allowed to use — the
``promote`` that moves a staged file and refuses to overwrite a queued one, and the JSON listing —
and records every argument list it was called with, so a test can assert both what the connector
asked worc for and, just as important, what it never asked.

``promote``'s messages and exit codes are worc's own, because the connector classifies its outcome
from them: exit 1 with an "already in pending" line is a success, exit 1 with anything else is a
retry.

The scenario directory arrives in ``WORC_CONNECT_FAKE_WORC_HOME``; the clone is the working
directory the connector launched worc in, exactly as the real worc discovers it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CALLS_FILENAME = "calls.jsonl"
ENVIRONMENT_FILENAME = "environment.json"
SCENARIO_FILENAME = "scenario.json"
HOME_VARIABLE = "WORC_CONNECT_FAKE_WORC_HOME"


def promote(argv: list[str], scenario: dict[str, object]) -> int:
    """Move one staged task file into the queue, or report why it stayed where it was."""
    scripted = scenario.get("promote")
    if isinstance(scripted, dict):  # a failure a test wants to see verbatim
        sys.stdout.write(str(scripted.get("stdout", "")))
        return int(scripted.get("exit_code", 1))

    target = next((argument for argument in argv[1:] if not argument.startswith("-")), "")
    tasks_dir = Path.cwd() / str(scenario.get("tasks_dir", "tasks"))
    source = tasks_dir / "preparing" / f"{target}.md"
    destination = tasks_dir / "pending" / f"{target}.md"
    if not source.is_file():
        sys.stdout.write(f"promote: {target!r} is not a staged file in preparing/\n")
        return 1
    if destination.exists():
        sys.stdout.write(
            f"promote: {destination.name} already in pending — not overwriting a queued task\n"
        )
        return 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    sys.stdout.write(f"promote: {destination.name} -> pending\n")
    return 0


def listing(scenario: dict[str, object]) -> int:
    """Answer ``list --format json`` from the scenario's entries."""
    scripted = scenario.get("list")
    if isinstance(scripted, dict):
        sys.stdout.write(str(scripted.get("stdout", "")))
        return int(scripted.get("exit_code", 0))
    sys.stdout.write(json.dumps(scenario.get("entries", [])))
    return 0


def main(argv: list[str]) -> int:
    """Record the call, then answer it; a verb the connector may not use is a loud failure."""
    home = Path(os.environ[HOME_VARIABLE])
    with (home / CALLS_FILENAME).open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(argv) + "\n")
    # The environment as the connector handed it over, so "nothing is added for the child" is an
    # assertion rather than a claim.
    (home / ENVIRONMENT_FILENAME).write_text(
        json.dumps(dict(os.environ)), encoding="utf-8", newline=""
    )

    scenario = json.loads((home / SCENARIO_FILENAME).read_text(encoding="utf-8"))
    verb = argv[0] if argv else ""
    if verb == "promote":
        return promote(argv, scenario)
    if verb == "list":
        return listing(scenario)
    sys.stderr.write(f"fake worc: the connector may not run `worc {verb}`\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
