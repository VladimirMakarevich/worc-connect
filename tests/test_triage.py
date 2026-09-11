"""The one place a model's output is read, and the deterministic task it can turn into.

Everything here is about a boundary rather than about a feature: the report is written by an agent
that read a stranger's issue, so the parser is the whole of the connector's trust in it. It fails
closed on shape — no block, a broken block, a verdict outside the vocabulary all yield nothing —
and it never lets the report decide what a task looks like. The builder does that, from the fields
the parser proved were there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from support import connector_config, report_text, work_item
from worc_connect.core import builder, triage
from worc_connect.core.triage import Verdict
from worc_connect.home import ConnectorHome

ACTIONABLE = """\
The validator accepts a bare domain because it never checks for a dot.

```worc-connect-triage
verdict: actionable
reason: The address validator accepts a bare domain; it should require a dot-separated host.
acceptance_criteria:
  - A signup with `foo@` is rejected.
  - A signup with `foo@example.test` is accepted.
failing_test:
  path: tests/test_signup.py
  body: |
    def test_bare_domain_is_rejected():
        assert not validate("foo@")
```
"""


def front_matter(content: str) -> dict[str, object]:
    """The generated task's front matter, parsed back the way worc's own gate parses it."""
    block = content.split("---\n", 2)[1]
    loaded = yaml.safe_load(block)
    assert isinstance(loaded, dict)
    return loaded


def body_of(content: str) -> str:
    """The generated task's body, everything after the front matter."""
    return content.split("---\n", 2)[2]


# --- the parser -------------------------------------------------------------------------------


def test_a_full_report_is_read_field_by_field() -> None:
    report = triage.parse(ACTIONABLE)

    assert report is not None
    assert report.verdict is Verdict.ACTIONABLE
    assert report.reason.startswith("The address validator")
    assert report.acceptance_criteria == (
        "A signup with `foo@` is rejected.",
        "A signup with `foo@example.test` is accepted.",
    )
    assert report.failing_test is not None
    assert report.failing_test.path == "tests/test_signup.py"
    assert 'validate("foo@")' in report.failing_test.body


@pytest.mark.parametrize(
    "verdict",
    [Verdict.ACTIONABLE, Verdict.NEEDS_INFO, Verdict.DUPLICATE, Verdict.DECLINED],
)
def test_every_verdict_of_the_vocabulary_is_read(verdict: Verdict) -> None:
    report = triage.parse(report_text(verdict, reason="because"))

    assert report is not None
    assert report.verdict is verdict


def test_a_duplicate_carries_what_it_duplicates() -> None:
    report = triage.parse(report_text(Verdict.DUPLICATE, reason="already fixed", duplicate_of="#7"))

    assert report is not None
    assert report.duplicate_of == "#7"


@pytest.mark.parametrize(
    "text",
    [
        "a report with prose and no block at all",
        "```worc-connect-triage\nverdict: actionable\n",  # the fence was never closed
        "```worc-connect-triage\n: not: yaml: at all\n```",
        "```worc-connect-triage\njust a string, not a mapping\n```",
        "```worc-connect-triage\nverdict: probably-fine\n```",
        "```worc-connect-triage\nreason: no verdict here\n```",
        "```yaml\nverdict: actionable\n```",  # a block that is not the one the flow asks for
    ],
)
def test_a_report_this_build_cannot_act_on_yields_nothing(text: str) -> None:
    assert triage.parse(text) is None


def test_a_thin_report_is_still_a_report() -> None:
    # A verdict with nothing else is valid: an item that needs no criteria and no reproduction still
    # deserves a task, and the builder emits only the sections the report actually filled in.
    report = triage.parse("```worc-connect-triage\nverdict: actionable\n```")

    assert report is not None
    assert (report.reason, report.acceptance_criteria, report.failing_test) == ("", (), None)


def test_a_reproduction_without_a_body_is_not_half_a_reproduction() -> None:
    report = triage.parse(
        "```worc-connect-triage\nverdict: actionable\nfailing_test:\n  path: tests/x.py\n```"
    )

    assert report is not None
    assert report.failing_test is None


def test_the_first_block_decides_when_a_report_carries_two() -> None:
    doubled = report_text(Verdict.DECLINED, reason="first") + report_text(
        Verdict.ACTIONABLE, reason="second"
    )

    report = triage.parse(doubled)

    assert report is not None
    assert report.verdict is Verdict.DECLINED


# --- reading it off disk ----------------------------------------------------------------------


def test_the_report_is_read_from_the_directory_the_flow_declares(clone: Path) -> None:
    home = ConnectorHome.beside(clone)
    path = triage.report_path(home.triage_path, "gh-142")
    path.parent.mkdir(parents=True)
    path.write_text(ACTIONABLE, encoding="utf-8", newline="")

    report = triage.read(home.triage_path, "gh-142")

    assert report is not None and report.verdict is Verdict.ACTIONABLE
    assert path.as_posix().endswith(".worc-connect/triage/gh-142/report.md")


def test_a_report_that_is_not_there_reads_as_none(clone: Path) -> None:
    # There is deliberately no second place to look: a fallback is how a connector ends up reading
    # worc's private home.
    assert triage.read(ConnectorHome.beside(clone).triage_path, "gh-142") is None


# --- the tasks the builder makes of it --------------------------------------------------------


def test_the_triage_task_names_the_flow_and_jumps_the_queue(clone: Path) -> None:
    config = connector_config(clone, research_flow="issue_triage")

    draft = builder.build_research(work_item(), config, seq=1)

    fields = front_matter(draft.content)
    assert fields["task_type"] == "issue_triage"
    assert fields["priority"] == "high"
    assert draft.task_id == "gh-142"


def test_the_triage_task_carries_no_closing_reference(clone: Path) -> None:
    # It publishes nothing, so a pull request it will never open cannot close anything.
    draft = builder.build_research(work_item(), connector_config(clone), seq=1)

    assert "references" not in front_matter(draft.content)


def test_the_triage_task_carries_the_items_own_text(clone: Path) -> None:
    item = work_item(body="the reporter's own words")

    draft = builder.build_research(item, connector_config(clone), seq=1)

    assert "the reporter's own words" in body_of(draft.content)


def test_an_actionable_report_becomes_a_task_shaped_by_the_builder(clone: Path) -> None:
    report = triage.parse(ACTIONABLE)
    assert report is not None

    draft = builder.build_from_report(work_item(), connector_config(clone), report, seq=2)

    body = body_of(draft.content)
    assert report.reason in body
    assert "## Acceptance criteria" in body
    assert "- A signup with `foo@` is rejected." in body
    assert "## Failing test" in body
    assert "Suggested path: `tests/test_signup.py`" in body
    assert 'assert not validate("foo@")' in body


def test_a_report_with_no_criteria_gets_no_criteria_section(clone: Path) -> None:
    report = triage.parse(report_text(Verdict.ACTIONABLE, reason="just do it"))
    assert report is not None

    draft = builder.build_from_report(work_item(), connector_config(clone), report, seq=2)

    body = body_of(draft.content)
    assert "just do it" in body
    assert "## Acceptance criteria" not in body
    assert "## Failing test" not in body


def test_the_implementation_task_from_a_report_is_dispatched_like_any_other(clone: Path) -> None:
    # The convergence that matters: what an implementation task looks like must not depend on
    # whether an agent analysed the item first.
    report = triage.parse(ACTIONABLE)
    assert report is not None
    config = connector_config(clone)

    from_report = builder.build_from_report(
        work_item(), config, report, seq=2, references=("Fixes #142",)
    )
    direct = builder.build(work_item(), config, seq=2, references=("Fixes #142",))

    assert set(front_matter(from_report.content)) == set(front_matter(direct.content))
    assert from_report.task_id == direct.task_id == "gh-142.2"


def test_the_provenance_line_survives_the_report(clone: Path) -> None:
    report = triage.parse(ACTIONABLE)
    assert report is not None

    draft = builder.build_from_report(work_item(), connector_config(clone), report, seq=2)

    assert "Source: github item #142 by @reporter" in body_of(draft.content)
