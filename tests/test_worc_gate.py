"""The generated task file, judged by worc's own validation gate rather than by our reading of it.

The connector has to produce a file worc accepts unchanged, and worc rejects rather than repairs:
a title carrying an argv-shaped token, a body over one of three size limits, or a front-matter key
worc does not allow all end the same way — the task is quarantined inside worc's private home,
where the connector is not allowed to look, so from outside it simply vanishes.

That is too important to check against a second, local copy of worc's rules, which could drift from
the real ones without anything failing. These tests run the real gate.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest

from support import (
    connector_config,
    requires_worc,
    requires_worc_references,
    task_config,
    work_item,
)
from worc_connect.core import builder
from worc_connect.core.sanitize import sanitize_title

pytestmark = requires_worc

# A title made of every token worc's front-matter scan refuses, led by the one that makes a value
# look like a command-line flag.
HOSTILE_TITLE = "-rm -rf /; echo | $(whoami)"

# Leading runs of dashes broken by whitespace, and the flag shapes worc's forbidden-argument check
# names. Each is refused by worc's scanner as written, and each has to leave the sanitizer as a
# value that same scanner accepts.
DASH_RUN_TITLES = (
    "- -foo",
    "-- --yolo",
    "- - -x",
    "- - -",
    "--dangerously-skip-permissions",
    "--sandbox=danger-full-access",
    "-s danger-full-access",
)


@pytest.fixture
def gate() -> object:
    """worc's validation gate, configured exactly as a fresh `worc install` configures it."""
    from wastech_orchestrator.config.loader import loads_config
    from wastech_orchestrator.task.validation_gate import ValidationGate

    text = (
        resources.files("wastech_orchestrator")
        .joinpath("packaged", "config.example.yaml")
        .read_text(encoding="utf-8")
    )
    return ValidationGate(
        loads_config(text).config,
        store_has_task_id=lambda _id: False,
        ledger_has_task_id=lambda _id: False,
    )


def judge(gate: object, content: str) -> object:
    """Run the gate over ``content`` as though it had just been promoted."""
    from wastech_orchestrator.task.parser import ParsedSource

    source = ParsedSource(path="task.md", suffix=".md", raw_bytes=content.encode("utf-8"))
    return gate.validate(source)  # type: ignore[attr-defined]


def test_a_hostile_item_still_produces_a_task_worc_accepts(gate: object, clone: Path) -> None:
    # 300 000 bytes with one 10 000-byte line: over the byte limit, over the per-line limit, and
    # carrying every token the injection scan refuses in its title.
    item = work_item(
        title=HOSTILE_TITLE,
        body="\n".join(["x" * 10_000, *["padding line"] * 20_000]),
    )
    config = connector_config(
        clone,
        task=task_config(
            task_type="implementation", queue="default", priority="mid", commit_type="feat"
        ),
    )

    result = judge(gate, builder.build(item, config, seq=1).content)

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]


def test_a_title_that_sanitizes_to_nothing_still_passes(gate: object, clone: Path) -> None:
    item = work_item(title=";;; ``` |||")

    result = judge(gate, builder.build(item, connector_config(clone), seq=1).content)

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]
    assert result.normalized.title == "Issue #142"  # type: ignore[attr-defined]


@pytest.mark.parametrize("raw", DASH_RUN_TITLES)
def test_a_title_whose_dash_run_is_broken_by_whitespace_still_passes(
    gate: object, clone: Path, raw: str
) -> None:
    draft = builder.build(work_item(title=raw), connector_config(clone), seq=1)

    result = judge(gate, draft.content)

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]


@pytest.mark.parametrize("raw", DASH_RUN_TITLES)
def test_a_sanitized_title_passes_worcs_own_scanner(raw: str) -> None:
    # Judged by the scanner itself rather than by our reading of it: the raw title is one it
    # refuses, and the sanitized one has to be one it accepts.
    from wastech_orchestrator.security.injection import scan_value

    assert scan_value("title", raw) is not None
    assert scan_value("title", sanitize_title(raw, fallback="Issue #142")) is None


def test_every_substring_worcs_scanner_refuses_is_removed_from_a_title() -> None:
    # The list is worc's, read from worc, so a token added there fails here instead of quarantining
    # a task on a real run.
    from wastech_orchestrator.security.injection import INJECTION_SUBSTRINGS, scan_value

    for token in INJECTION_SUBSTRINGS:
        sanitized = sanitize_title(f"a{token}b", fallback="Issue #142")
        assert token not in sanitized
        assert scan_value("title", sanitized) is None


def test_a_re_triggered_task_continuing_a_branch_passes(gate: object, clone: Path) -> None:
    draft = builder.build(
        work_item(), connector_config(clone), seq=2, branch_ref="worc/gh-142-first-try"
    )

    result = judge(gate, draft.content)

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]
    assert draft.task_id == "gh-142.2"


@requires_worc_references
def test_the_closing_line_the_adapter_authors_passes_the_gate(gate: object, clone: Path) -> None:
    draft = builder.build(work_item(), connector_config(clone), seq=1, references=("Fixes #142",))

    result = judge(gate, draft.content)

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]
    # worc parses the key back to the exact line the adapter wrote; it is what it appends to the
    # pull-request body, so a value the gate reshaped would close a different issue or none.
    assert result.normalized.references == ("Fixes #142",)  # type: ignore[attr-defined]


@requires_worc_references
def test_a_hostile_item_with_a_closing_line_still_passes_the_gate(
    gate: object, clone: Path
) -> None:
    # The same item AC-4 builds from, now carrying the contract key: the front-matter injection
    # scan recurses into lists, so the reference is scanned exactly like the title is.
    item = work_item(
        title=HOSTILE_TITLE,
        body="\n".join(["x" * 10_000, *["padding line"] * 20_000]),
    )
    config = connector_config(
        clone,
        task=task_config(
            task_type="implementation", queue="default", priority="mid", commit_type="feat"
        ),
    )

    result = judge(gate, builder.build(item, config, seq=1, references=("Fixes #142",)).content)

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]


@requires_worc_references
def test_the_reference_the_github_adapter_authors_is_the_one_the_gate_accepts(
    gate: object, clone: Path
) -> None:
    # The adapter, not a literal, authors the line — a keyword this repository invented would pass
    # the gate just as happily and close nothing.
    from worc_connect.trackers.github import build_adapter

    line = build_adapter(repo="OWNER/REPO", labels_prefix="worc:").closing_reference(work_item())
    assert line is not None

    result = judge(
        gate, builder.build(work_item(), connector_config(clone), seq=1, references=(line,)).content
    )

    assert result.passed is True, f"{result.reason}: {result.detail}"  # type: ignore[attr-defined]
    assert result.normalized.references == ("Fixes #142",)  # type: ignore[attr-defined]


def test_the_builders_key_set_is_a_strict_subset_of_the_keys_worc_allows() -> None:
    from wastech_orchestrator.task.model import ALLOWED_TASK_KEYS

    assert builder.ALLOWED_KEYS < ALLOWED_TASK_KEYS


def test_the_builder_can_never_emit_a_key_that_changes_how_a_task_runs() -> None:
    forbidden = {"nodes", "subtasks", "decomposition", "trust_level", "prompt_audit", "publish"}

    assert not builder.ALLOWED_KEYS & forbidden


def test_the_branch_the_connector_names_is_the_one_worc_will_use(clone: Path) -> None:
    from wastech_orchestrator.task.model import BRANCH_NAME_MAX_LEN, is_valid_branch_name

    draft = builder.build(work_item(title="a " * 200), connector_config(clone), seq=1)

    # Over worc's soft cap it would discard the name and generate its own, which the connector
    # could then not find the pull request by.
    assert len(draft.branch) <= BRANCH_NAME_MAX_LEN
    assert is_valid_branch_name(draft.branch)
