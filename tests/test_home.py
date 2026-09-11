"""The connector's home: the configuration it seeds and how it stays out of git.

The gitignore probe is textual because the connector never runs ``git``, so the cases worth pinning
down are the ones a text append gets wrong: a file with no trailing newline, a line already there,
and a file that does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

from worc_connect.home import GITIGNORE_LINE, ConnectorHome, ensure_gitignore_entry, render_config


def test_the_line_is_added_to_a_clone_with_no_gitignore(clone: Path) -> None:
    assert ensure_gitignore_entry(clone) is True

    lines = (clone / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[-1] == GITIGNORE_LINE
    assert lines[-2].startswith("#")


def test_a_file_without_a_trailing_newline_does_not_lose_its_last_line(clone: Path) -> None:
    (clone / ".gitignore").write_text("build/", encoding="utf-8", newline="")

    ensure_gitignore_entry(clone)

    lines = (clone / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "build/"
    assert GITIGNORE_LINE in lines


def test_a_line_already_present_is_not_added_again(clone: Path) -> None:
    (clone / ".gitignore").write_text(f"{GITIGNORE_LINE}\n", encoding="utf-8", newline="")

    assert ensure_gitignore_entry(clone) is False
    assert (clone / ".gitignore").read_text(encoding="utf-8") == f"{GITIGNORE_LINE}\n"


def test_a_line_the_operator_indented_still_counts_as_present(clone: Path) -> None:
    (clone / ".gitignore").write_text(f"  {GITIGNORE_LINE}  \n", encoding="utf-8", newline="")

    assert ensure_gitignore_entry(clone) is False


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
