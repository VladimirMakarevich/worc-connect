"""The task builder: the names it allocates, the text it sanitizes, the keys it emits.

Everything here is pure — no clone, no process, no tracker — because the builder is the one place
where a stranger's text is turned into something worc will accept, and that transformation has to
be provable on its own. Whether the resulting file actually passes worc's gate is asserted against
the real gate in ``test_worc_gate.py``; these tests pin the rules that get it there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from support import connector_config, task_config, work_item
from worc_connect.core import builder
from worc_connect.core.naming import BRANCH_MAX_LEN, allocate_branch, allocate_task_id, slugify
from worc_connect.core.sanitize import TITLE_MAX_LEN, sanitize_title, truncate_body


def front_matter(content: str) -> dict[str, object]:
    """The parsed front matter of a rendered task file."""
    _, _, rest = content.partition("---\n")
    block, _, _ = rest.partition("---\n")
    loaded = yaml.safe_load(block)
    assert isinstance(loaded, dict)
    return loaded


def body_of(content: str) -> str:
    """Everything after the front matter of a rendered task file."""
    _, _, rest = content.partition("---\n")
    _, _, body = rest.partition("---\n")
    return body


# --- the title sanitizer ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["a; rm -rf /", "a `whoami`", "a | b", "a $(whoami)", "a\nb", "a\rb", "-leading", "--flag"],
)
def test_a_title_carries_no_token_worcs_injection_scan_refuses(raw: str) -> None:
    sanitized = sanitize_title(raw, fallback="Issue #1")

    assert not sanitized.startswith("-")
    assert not any(token in sanitized for token in (";", "`", "|", "$(", "\n", "\r"))


# A leading run of dashes broken by whitespace, and the flag shapes worc's forbidden-argument check
# names. Stripping one dash and the whitespace after it exposes the next one — `- -foo` becomes
# `-foo` — which worc refuses for exactly the same reason.
DASH_RUN_TITLES = (
    "- -foo",
    "-- --yolo",
    "- - -x",
    "- \t- -x",
    "- - -",
    "--dangerously-skip-permissions",
    "--sandbox=danger-full-access",
    "-s danger-full-access",
)


@pytest.mark.parametrize("raw", DASH_RUN_TITLES)
def test_a_leading_run_of_dashes_is_stripped_whole_however_it_is_broken_up(raw: str) -> None:
    sanitized = sanitize_title(raw, fallback="Issue #1")

    assert not sanitized.startswith("-")
    assert sanitized == sanitized.strip()


def test_stripping_the_dash_run_keeps_the_words_behind_it() -> None:
    assert sanitize_title("- -foo bar", fallback="Issue #1") == "foo bar"
    assert sanitize_title("- - -", fallback="Issue #1") == "Issue #1"


def test_a_title_that_sanitizes_to_nothing_falls_back_to_the_item_number() -> None:
    assert sanitize_title(";;; ``` ---", fallback="Issue #142") == "Issue #142"
    assert sanitize_title("", fallback="Issue #142") == "Issue #142"


def test_a_title_has_its_whitespace_collapsed_and_its_control_characters_dropped() -> None:
    assert sanitize_title("  a \t\t b \x00\x07 c  ", fallback="-") == "a b c"


def test_a_title_is_capped_and_the_cap_never_leaves_trailing_space() -> None:
    sanitized = sanitize_title("word " * 200, fallback="-")

    assert len(sanitized) <= TITLE_MAX_LEN
    assert sanitized == sanitized.strip()


# --- ids and branches ------------------------------------------------------------------------


def test_the_first_attempt_has_no_suffix_and_a_re_trigger_takes_the_next_one() -> None:
    assert allocate_task_id("gh", "142", 1) == "gh-142"
    assert allocate_task_id("gh", "142", 2) == "gh-142.2"
    assert allocate_task_id("gh", "142", 11) == "gh-142.11"


def test_a_branch_fits_worcs_budget_however_long_the_title_is() -> None:
    branch = allocate_branch("worc", "gh-142", "a " * 200)

    assert len(branch) <= BRANCH_MAX_LEN
    assert branch.startswith("worc/gh-142")
    assert not branch.endswith(("-", "."))


def test_a_branch_survives_a_title_that_slugifies_to_nothing() -> None:
    assert allocate_branch("worc", "gh-142", "???") == "worc/gh-142"
    assert slugify("???") == ""


def test_a_branch_can_never_collide_with_a_base_branch() -> None:
    branch = allocate_branch("worc", "gh-142", "main")

    assert "/" in branch
    assert branch not in {"main", "master", "dev"}


@pytest.mark.parametrize("title", ["Signup form accepts foo@", "Ünïcödé", "a/b:c?d*e[f]g~h^i"])
def test_a_branch_is_a_ref_name_git_accepts(title: str) -> None:
    branch = allocate_branch("worc", "gh-142", title)

    assert not any(character in branch for character in " ~^:?*[\\")
    assert ".." not in branch and "//" not in branch and "@{" not in branch
    for component in branch.split("/"):
        assert component and not component.startswith((".", "-"))
        assert not component.endswith((".", ".lock"))


# --- body truncation -------------------------------------------------------------------------

URL = "https://github.com/OWNER/REPO/issues/142"


def test_a_body_inside_every_limit_is_returned_byte_for_byte() -> None:
    body = "first line\nsecond line"

    assert truncate_body(body, max_bytes=1000, max_lines=10, max_line_bytes=100, url=URL) == body


def test_an_over_long_line_is_cut_with_a_marker_that_names_the_item() -> None:
    result = truncate_body("x" * 500, max_bytes=10_000, max_lines=10, max_line_bytes=200, url=URL)

    assert len(result.encode("utf-8")) <= 200
    assert URL in result


def test_too_many_lines_are_cut_with_a_marker_on_the_last_one() -> None:
    result = truncate_body(
        "line\n" * 50, max_bytes=10_000, max_lines=10, max_line_bytes=100, url=URL
    )

    assert len(result.splitlines()) <= 10
    assert result.splitlines()[-1].endswith(URL)


def test_a_body_over_the_byte_limit_is_cut_and_still_decodes() -> None:
    result = truncate_body(
        "é" * 5_000, max_bytes=400, max_lines=100, max_line_bytes=10_000, url=URL
    )

    assert len(result.encode("utf-8")) <= 400
    assert URL in result
    assert result == result.encode("utf-8").decode("utf-8")  # never split a multi-byte character


# --- the rendered task file ------------------------------------------------------------------


def test_only_the_configured_dispatch_keys_are_emitted(clone: Path) -> None:
    config = connector_config(clone, task=task_config(queue="default"))

    draft = builder.build(work_item(), config, seq=1)

    assert set(front_matter(draft.content)) == {"id", "title", "branch_name", "queue"}


def test_every_configured_dispatch_key_reaches_the_front_matter(clone: Path) -> None:
    config = connector_config(
        clone,
        task=task_config(
            task_type="implementation",
            queue="default",
            priority="mid",
            commit_type="feat",
            auto_merge=False,
        ),
    )

    fields = front_matter(builder.build(work_item(), config, seq=1).content)

    assert fields["task_type"] == "implementation"
    assert fields["queue"] == "default"
    assert fields["priority"] == "mid"
    assert fields["commit_type"] == "feat"
    assert fields["auto_merge"] is False


def test_auto_merge_is_absent_until_the_operator_sets_it(clone: Path) -> None:
    config = connector_config(clone, task=task_config(commit_type="feat"))

    assert "auto_merge" not in front_matter(builder.build(work_item(), config, seq=1).content)


def test_a_label_can_override_the_commit_type(clone: Path) -> None:
    config = connector_config(
        clone,
        task=task_config(commit_type="feat", commit_type_by_label={"bug": "fix"}),
    )

    fields = front_matter(builder.build(work_item(labels=("worc", "Bug")), config, seq=1).content)

    assert fields["commit_type"] == "fix"


def test_an_unmapped_label_leaves_the_configured_commit_type(clone: Path) -> None:
    config = connector_config(
        clone, task=task_config(commit_type="feat", commit_type_by_label={"bug": "fix"})
    )

    fields = front_matter(builder.build(work_item(labels=("worc",)), config, seq=1).content)

    assert fields["commit_type"] == "feat"


def test_a_follow_up_continues_the_previous_branch_instead_of_naming_a_new_one(clone: Path) -> None:
    config = connector_config(clone)

    draft = builder.build(work_item(), config, seq=2, branch_ref="worc/gh-142-first-try")

    fields = front_matter(draft.content)
    assert fields["branch_mode"] == "existing"
    assert fields["branch_ref"] == "worc/gh-142-first-try"
    assert "branch_name" not in fields
    assert draft.branch == "worc/gh-142-first-try"


def test_a_closing_line_is_emitted_as_the_references_key(clone: Path) -> None:
    draft = builder.build(work_item(), connector_config(clone), seq=1, references=("Fixes #142",))

    assert front_matter(draft.content)["references"] == ["Fixes #142"]


def test_no_closing_line_means_no_references_key_at_all(clone: Path) -> None:
    # Not an empty list: worc's gate refuses `references: []` outright, and a key an older worc
    # does not know is a hard reject whatever its value.
    draft = builder.build(work_item(), connector_config(clone), seq=1)

    assert "references" not in front_matter(draft.content)


def test_the_closing_line_is_the_callers_and_is_never_derived_from_the_item(clone: Path) -> None:
    # The keyword belongs to a tracker adapter, so the builder renders whatever it is handed and
    # invents nothing — a core that guessed `Fixes #<n>` would have learned one tracker's syntax.
    draft = builder.build(work_item(), connector_config(clone), seq=1, references=("AB#142",))

    assert front_matter(draft.content)["references"] == ["AB#142"]


def test_the_body_opens_with_provenance_and_then_the_item_text_verbatim(clone: Path) -> None:
    item = work_item(body="The validator lets a bare domain through.\n\n```sh\nrm -rf /\n```")

    draft = builder.build(item, connector_config(clone), seq=1)

    body = body_of(draft.content)
    assert "## Description" in body
    assert f"Source: github item #142 by @{item.author} — {item.url}" in body
    assert item.body in body  # verbatim, shell snippet included — worc scans front matter only
    assert "## Acceptance criteria" not in body


def test_an_item_without_an_author_still_gets_a_provenance_line(clone: Path) -> None:
    draft = builder.build(work_item(author=""), connector_config(clone), seq=1)

    assert "Source: github item #142 — https://" in body_of(draft.content)


def test_the_whole_file_fits_worcs_limits_not_just_the_item_text(clone: Path) -> None:
    config = connector_config(clone, max_task_bytes=400, task=task_config(queue="default"))

    draft = builder.build(work_item(body="x" * 10_000), config, seq=1)

    assert len(draft.content.encode("utf-8")) <= 400
