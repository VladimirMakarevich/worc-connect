#!/usr/bin/env python
"""A deterministic stand-in for the GitHub CLI, driven by a scenario file.

The suite never launches the real ``gh``: there is no network, no credential and no repository in a
test, and a test that depended on any of them would be a test that fails for reasons the connector
is not responsible for. This script answers each subcommand from a scenario file and records every
argument list it was called with, so a test can assert both what the connector asked for and — just
as important for a dry run — what it never asked for.

The scenario directory arrives in ``WORC_CONNECT_FAKE_GH_HOME``; nothing is read from anywhere else,
so two tests running in parallel cannot see each other's recordings.

Two ways to answer. A scripted ``responses`` entry answers its verb verbatim — the shape for a
failure or a payload a test wants to see exactly. Where a scenario carries ``issues`` instead, the
fake is a small repository: ``issue list`` filters them the way GitHub does — by ``--state``, by the
``updated:>=`` qualifier in ``--search``, oldest update first under ``sort:updated-asc``, capped by
``--limit``, reduced to the ``--json`` fields asked for — ``issue view`` reads one by number, and
``issue edit`` / ``issue comment`` / ``issue close`` change it and move its ``updatedAt`` past every
stamp handed out so far. Without that, a test of the poll window would only be asserting the search
string it sent, never what the tracker made of it.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

CALLS_FILENAME = "calls.jsonl"
ENVIRONMENT_FILENAME = "environment.json"
SCENARIO_FILENAME = "scenario.json"
ISSUES_FILENAME = "issues.json"
HOME_VARIABLE = "WORC_CONNECT_FAKE_GH_HOME"
UPDATED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
UPDATED_QUALIFIER = "updated:>="


def verb_of(argv: list[str]) -> str:
    """The subcommand a call names, as the scenario file keys it (``issue list``, ``pr list``)."""
    return " ".join(argument for argument in argv[:2] if not argument.startswith("-"))


def main(argv: list[str]) -> int:
    """Record the call, then answer it from the scenario; an unscripted verb is a loud failure."""
    home = Path(os.environ[HOME_VARIABLE])
    with (home / CALLS_FILENAME).open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(argv) + "\n")
    # The environment as the connector handed it over, so "nothing is added for the child" is an
    # assertion rather than a claim.
    (home / ENVIRONMENT_FILENAME).write_text(
        json.dumps(dict(os.environ)), encoding="utf-8", newline=""
    )

    scenario = json.loads((home / SCENARIO_FILENAME).read_text(encoding="utf-8"))
    response = scenario.get("responses", {}).get(verb_of(argv))
    if response is None and "issues" in scenario and verb_of(argv).startswith("issue "):
        return Repository(home, scenario["issues"]).answer(argv)
    if response is None:
        sys.stderr.write(f"fake gh: no response scripted for `{verb_of(argv)}`\n")
        return 1

    sys.stdout.write(response.get("stdout", ""))
    sys.stderr.write(response.get("stderr", ""))
    exit_code: int = response.get("exit_code", 0)
    return exit_code


class Repository:
    """The scenario's issues as a tracker holds them, persisted between calls in the fake's home."""

    def __init__(self, home: Path, seeded: list[dict[str, Any]]) -> None:
        self.path = home / ISSUES_FILENAME
        stored = self.path.read_text(encoding="utf-8") if self.path.is_file() else None
        self.issues: list[dict[str, Any]] = json.loads(stored) if stored else seeded

    def answer(self, argv: list[str]) -> int:
        """Answer one ``issue`` subcommand from the stored issues."""
        verb = verb_of(argv)
        if verb == "issue list":
            return _emit([_fields(issue, argv) for issue in self._listed(argv)])
        issue = self._by_number(argv[2])
        if issue is None:
            sys.stderr.write(f"GraphQL: Could not resolve to an issue numbered {argv[2]}\n")
            return 1
        if verb == "issue view":
            return _emit(_fields(issue, argv))
        if verb == "issue edit":
            removed = set(_values(argv, "--remove-label"))
            kept = [label for label in issue["labels"] if label["name"] not in removed]
            issue["labels"] = kept + [{"name": name} for name in _values(argv, "--add-label")]
        elif verb == "issue close":
            issue["state"] = "CLOSED"
        elif verb != "issue comment":
            sys.stderr.write(f"fake gh: no response scripted for `{verb}`\n")
            return 1
        self._touch(issue)
        return 0

    def _listed(self, argv: list[str]) -> list[dict[str, Any]]:
        state = (_value(argv, "--state") or "open").upper()
        search = (_value(argv, "--search") or "").split()
        floors = [
            _stamp(term.removeprefix(UPDATED_QUALIFIER))
            for term in search
            if term.startswith(UPDATED_QUALIFIER)
        ]
        listed = [
            issue
            for issue in self.issues
            if state in ("ALL", issue.get("state", "OPEN"))
            and all(_stamp(issue["updatedAt"]) >= floor for floor in floors)
        ]
        if "sort:updated-asc" in search:
            listed.sort(key=lambda issue: _stamp(issue["updatedAt"]))
        return listed[: int(_value(argv, "--limit") or 30)]

    def _by_number(self, number: str) -> dict[str, Any] | None:
        return next((issue for issue in self.issues if str(issue["number"]) == number), None)

    def _touch(self, issue: dict[str, Any]) -> None:
        latest = max(_stamp(entry["updatedAt"]) for entry in self.issues)
        issue["updatedAt"] = (latest + timedelta(seconds=1)).strftime(UPDATED_FORMAT)
        self.path.write_text(json.dumps(self.issues), encoding="utf-8", newline="")


def _value(argv: list[str], flag: str) -> str | None:
    values = _values(argv, flag)
    return values[-1] if values else None


def _values(argv: list[str], flag: str) -> list[str]:
    return [argv[index + 1] for index, argument in enumerate(argv[:-1]) if argument == flag]


def _fields(issue: dict[str, Any], argv: list[str]) -> dict[str, Any]:
    wanted = (_value(argv, "--json") or "").split(",")
    return {name: issue.get(name, "OPEN" if name == "state" else None) for name in wanted if name}


def _stamp(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _emit(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
