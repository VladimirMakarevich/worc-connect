"""The connector's home: the configuration it seeds and how it stays out of git.

The gitignore probe is textual because the connector never runs ``git``, so the cases worth pinning
down are the ones a text append gets wrong: a file with no trailing newline, a line already there,
and a file that does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worc_connect.home import (
    GITIGNORE_LINE,
    ConnectorHome,
    ensure_gitignore_entry,
    render_config,
    unignored_home,
)


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone with no ``.gitignore`` at all — the starting point these probes are about."""
    path = tmp_path / "clone"
    path.mkdir()
    return path


def test_the_line_is_added_to_a_clone_with_no_gitignore(clone: Path) -> None:
    assert ensure_gitignore_entry(ConnectorHome.beside(clone)) is True

    lines = (clone / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[-1] == GITIGNORE_LINE
    assert lines[-2].startswith("#")


def test_a_file_without_a_trailing_newline_does_not_lose_its_last_line(clone: Path) -> None:
    (clone / ".gitignore").write_text("build/", encoding="utf-8", newline="")

    ensure_gitignore_entry(ConnectorHome.beside(clone))

    lines = (clone / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "build/"
    assert GITIGNORE_LINE in lines


def test_a_line_already_present_is_not_added_again(clone: Path) -> None:
    (clone / ".gitignore").write_text(f"{GITIGNORE_LINE}\n", encoding="utf-8", newline="")

    assert ensure_gitignore_entry(ConnectorHome.beside(clone)) is False
    assert (clone / ".gitignore").read_text(encoding="utf-8") == f"{GITIGNORE_LINE}\n"


def test_a_line_the_operator_indented_still_counts_as_present(clone: Path) -> None:
    (clone / ".gitignore").write_text(f"  {GITIGNORE_LINE}  \n", encoding="utf-8", newline="")

    assert ensure_gitignore_entry(ConnectorHome.beside(clone)) is False


def test_the_seeded_configuration_leaves_auto_merge_unset(clone: Path) -> None:
    text = render_config(tracker="github", repo="OWNER/REPO")

    assert "\nauto_merge:" not in text
    assert "# auto_merge: false" in text
    assert "prompt" in text  # the caveat is written where the operator reads it


def test_the_home_names_every_file_the_connector_owns(clone: Path) -> None:
    home = ConnectorHome.beside(clone)

    owned = {
        home.config_path,
        home.state_path,
        home.log_path,
        home.pid_path,
        home.stop_path,
        home.triage_path,
    }

    assert all(path.parent == home.path for path in owned)
    assert len(owned) == 6


def test_a_home_under_another_name_gets_its_own_line(clone: Path) -> None:
    home = ConnectorHome(clone / "connector")

    assert ensure_gitignore_entry(home) is True

    assert "connector/" in (clone / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert unignored_home(home, clone) is None


# --- the probe the watch loop runs before every tick --------------------------------------------


@pytest.mark.parametrize(
    "line",
    [".worc-connect/", ".worc-connect", "/.worc-connect/", "/.worc-connect", "**/.worc-connect/"],
)
def test_every_exact_spelling_of_the_home_counts_as_ignored(clone: Path, line: str) -> None:
    (clone / ".gitignore").write_text(f"build/\n  {line}  \n", encoding="utf-8", newline="")

    assert unignored_home(ConnectorHome.beside(clone), clone) is None


def test_a_clone_with_no_gitignore_does_not_ignore_the_home(clone: Path) -> None:
    problem = unignored_home(ConnectorHome.beside(clone), clone)

    assert problem is not None
    assert "`.worc-connect/`" in problem
    assert "commit" in problem


def test_a_line_reverted_out_of_the_gitignore_is_noticed(clone: Path) -> None:
    # The first real run: `init`'s uncommitted line was reverted by a worc task as unrelated.
    (clone / ".gitignore").write_text("build/\n", encoding="utf-8", newline="")

    assert unignored_home(ConnectorHome.beside(clone), clone) is not None


def test_a_later_negation_un_ignores_the_home(clone: Path) -> None:
    (clone / ".gitignore").write_text(
        ".worc-connect/\n!.worc-connect/\n", encoding="utf-8", newline=""
    )

    assert unignored_home(ConnectorHome.beside(clone), clone) is not None


@pytest.mark.parametrize("line", ["*", ".worc-*/", "#.worc-connect/", ".worc-connect/state.db"])
def test_a_pattern_the_probe_cannot_read_exactly_fails_closed(clone: Path, line: str) -> None:
    (clone / ".gitignore").write_text(f"{line}\n", encoding="utf-8", newline="")

    assert unignored_home(ConnectorHome.beside(clone), clone) is not None


def test_a_home_in_a_subdirectory_is_matched_by_its_path_or_its_name(clone: Path) -> None:
    home = ConnectorHome(clone / "ops" / ".worc-connect")
    gitignore = clone / ".gitignore"

    for line in ("ops/.worc-connect/", "/ops/.worc-connect", ".worc-connect/", "**/.worc-connect"):
        gitignore.write_text(f"{line}\n", encoding="utf-8", newline="")
        assert unignored_home(home, clone) is None, line
    gitignore.write_text("other/.worc-connect/\n", encoding="utf-8", newline="")
    assert unignored_home(home, clone) is not None


def test_a_home_outside_the_clone_needs_no_line(tmp_path: Path, clone: Path) -> None:
    assert unignored_home(ConnectorHome(tmp_path / "elsewhere" / ".worc-connect"), clone) is None
