"""The triage report: where it lands, what it must say, and what the connector takes from it.

This module is the whole of the connector's trust in a model. A triage task runs inside worc's
sandbox and leaves a Markdown report in a directory the connector owns; everything the connector
then does is decided **here and by the builder**, from a block whose shape is fixed and whose
verdict comes from a closed vocabulary. The agent proposes; nothing it wrote becomes a task, a
label, or a state without passing through this parser first.

Two properties are deliberate.

**It fails closed on shape, not on content.** A report with no verdict block, an unparseable block,
or a verdict outside the vocabulary yields nothing at all — the attempt becomes a visible failure on
the item rather than a task built from a guess. The prose *inside* the block is not judged: a report
that says something surprising is the operator's to read, and a parser that second-guessed it would
be making the editorial decision the operator asked an agent for.

**The report is read from the connector's own home and nowhere else.** The flow declares that
directory as its ``report_dir``, so the file lands under ``.worc-connect/triage/<task_id>/``; there
is no fallback path, because a fallback is how a connector ends up reading worc's private home.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import yaml

# The file worc's private report policy requires a flow to produce, under the per-task directory it
# resolves from the flow's `report_dir`.
REPORT_FILENAME: Final = "report.md"

# The fenced block the report node is instructed to emit, tagged with a language nobody renders so
# it can never be confused with example YAML in the report's own prose. The connector reads the
# first one; a report that carries two is an authoring mistake, and taking the first is the
# deterministic answer rather than a merge of both.
_BLOCK_TAG: Final = "worc-connect-triage"
_BLOCK_PATTERN: Final = re.compile(
    rf"^[ \t]*```{_BLOCK_TAG}[ \t]*\n(?P<body>.*?)^[ \t]*```", re.DOTALL | re.MULTILINE
)


class Verdict(StrEnum):
    """What the triage task concluded about the item — the whole vocabulary, and closed.

    ``ACTIONABLE`` is the only one that produces work: the deterministic builder turns the report
    into an implementation task. The other three end the attempt on the item, which is the point of
    triage — an item nobody can act on should cost one analysis rather than an implementation run.
    """

    ACTIONABLE = "actionable"
    NEEDS_INFO = "needs-info"
    DUPLICATE = "duplicate"
    DECLINED = "declined"


@dataclass(frozen=True)
class FailingTest:
    """A reproduction the triage run arrived at, as text rather than as a file in the tree.

    The reproduction node writes only inside the report directory, so the test cannot be left
    behind in the clone and no branch is pushed. Carrying it as text is what lets the builder —
    not the agent — decide whether it reaches the implementation task and under which heading.
    """

    path: str | None
    body: str


@dataclass(frozen=True)
class Report:
    """One triage report, reduced to the fields the connector acts on.

    Everything but ``verdict`` is optional, because a report is allowed to be thin: an item that
    needs no acceptance criteria and no reproduction still deserves an implementation task, and the
    builder emits only the sections the report actually filled in.
    """

    verdict: Verdict
    reason: str
    duplicate_of: str | None = None
    acceptance_criteria: tuple[str, ...] = ()
    failing_test: FailingTest | None = None


def report_path(home_path: Path, task_id: str) -> Path:
    """Where the triage task ``task_id`` leaves its report, under the connector's own home.

    One spelling, used by the reader and by the flow's declared ``report_dir`` alike: the two have
    to name the same directory, and a second spelling is how they would come to disagree.
    """
    return home_path / task_id / REPORT_FILENAME


def mark_triage_task(home_path: Path, task_id: str) -> None:
    """Create the per-task directory under the connector's triage home before the task is staged.

    The directory is the report's declared destination, so the flow writes into it either way.
    Creating it first makes its existence the one signal — surviving a deleted cache — that a task
    id is a triage task's, from the tick the task is staged rather than from the moment a report
    lands; a row rebuilt while the task still runs would otherwise be followed as an implementation
    task and its report never read.
    """
    report_path(home_path, task_id).parent.mkdir(parents=True, exist_ok=True)


def is_triage_task(home_path: Path, task_id: str) -> bool:
    """Whether ``task_id`` is a triage task: it owns a directory under the triage home."""
    return report_path(home_path, task_id).parent.is_dir()


def read(home_path: Path, task_id: str) -> Report | None:
    """The report for ``task_id``, or ``None`` when there is none this parser can act on.

    ``None`` covers every way a report can be unusable — absent, unreadable, carrying no verdict
    block, carrying one that is not a mapping, naming a verdict outside the vocabulary. They are one
    answer because the connector's reaction is one: report the attempt as failed on the item and
    build nothing. Distinguishing them would offer the operator a difference they cannot act on
    differently, and the report itself is on their host to read.
    """
    path = report_path(home_path, task_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse(text)


def parse(text: str) -> Report | None:
    """The verdict block inside a report's text, or ``None`` when it carries none usable."""
    found = _BLOCK_PATTERN.search(text)
    if found is None:
        return None
    try:
        block = yaml.safe_load(found.group("body"))
    except yaml.YAMLError:
        return None
    if not isinstance(block, dict):
        return None
    verdict = _verdict(block.get("verdict"))
    if verdict is None:
        return None
    return Report(
        verdict=verdict,
        reason=_line(block.get("reason")) or "",
        duplicate_of=_line(block.get("duplicate_of")),
        acceptance_criteria=_lines(block.get("acceptance_criteria")),
        failing_test=_failing_test(block.get("failing_test")),
    )


def _verdict(value: Any) -> Verdict | None:
    """The verdict named by ``value``, or ``None`` for anything outside the vocabulary."""
    if not isinstance(value, str):
        return None
    try:
        return Verdict(value.strip())
    except ValueError:
        return None


def _line(value: Any) -> str | None:
    """A block value as one non-blank string, or ``None`` where the report left it out."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _lines(value: Any) -> tuple[str, ...]:
    """A block value as a list of non-blank strings; anything else reads as none given."""
    if not isinstance(value, list):
        return ()
    return tuple(entry.strip() for entry in value if isinstance(entry, str) and entry.strip())


def _failing_test(value: Any) -> FailingTest | None:
    """The reproduction the report carries, or ``None`` when it carries none with a body.

    A suggested path without a body is not half a reproduction, it is none: the body is the thing
    an implementation task can act on, and the path is only a hint about where it belongs.
    """
    if not isinstance(value, dict):
        return None
    body = value.get("body")
    if not isinstance(body, str) or not body.strip():
        return None
    return FailingTest(path=_line(value.get("path")), body=body)
