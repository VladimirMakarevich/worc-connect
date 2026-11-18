"""What a triage task leaves beside its report, and when the connector clears it away (F-7).

The reproduction node writes real source files into the report directory — a test, a runner's
configuration, a runner's cache — and the directory is inside the clone. A project tool that does
not read ``.gitignore`` takes them for the project's own code: in the second real run a linter
failed every later task on a triage scratch file. Once the task is over and the report read, only
the report stays.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support import (
    FakeWorc,
    StubAdapter,
    connector_config,
    report_text,
    work_item,
    write_report,
)
from worc_connect.config import ResearchMode
from worc_connect.core import triage
from worc_connect.core.loop import Watcher
from worc_connect.core.state import Phase, StateStore
from worc_connect.core.triage import Verdict
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(home: ConnectorHome, adapter: StubAdapter, store: StateStore) -> Watcher:
    return Watcher(
        config=connector_config(home.clone_path, research=ResearchMode.WORC),
        adapter=adapter,
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
        now_fn=lambda: datetime.now(tz=UTC),
    )


def leave_scratch(directory: Path) -> None:
    """What the reproduction node left in the second real run, give or take a name."""
    (directory / "ref007-crlf.test.ts").write_text("it('fails', () => {})\n", encoding="utf-8")
    (directory / "vitest.config.mjs").write_text("export default {}\n", encoding="utf-8")
    cache = directory / "node_modules" / ".vite"
    cache.mkdir(parents=True)
    (cache / "results.json").write_text("{}", encoding="utf-8")


# --- the cleanup itself -----------------------------------------------------------------------


def test_everything_but_the_report_is_removed(home: ConnectorHome) -> None:
    report = write_report(home, "gh-142", report_text(Verdict.DECLINED, reason="out of scope"))
    leave_scratch(report.parent)

    assert triage.clear_scratch(home.triage_path, "gh-142") == 0

    assert sorted(path.name for path in report.parent.iterdir()) == ["report.md"]
    # The directory stays: it is how a rebuilt row knows this id was a triage task.
    assert triage.is_triage_task(home.triage_path, "gh-142")


def test_other_tasks_directories_are_left_alone(home: ConnectorHome) -> None:
    write_report(home, "gh-142", "report\n")
    other = write_report(home, "gh-7", "report\n")
    leave_scratch(other.parent)

    triage.clear_scratch(home.triage_path, "gh-142")

    assert (other.parent / "vitest.config.mjs").is_file()


def test_an_empty_id_never_names_the_whole_triage_home(home: ConnectorHome) -> None:
    other = write_report(home, "gh-7", "report\n")
    leave_scratch(other.parent)

    assert triage.clear_scratch(home.triage_path, "") == 0

    assert (other.parent / "vitest.config.mjs").is_file()


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs a privilege on Windows")
def test_a_link_out_of_the_directory_is_removed_and_never_followed(
    home: ConnectorHome, clone: Path
) -> None:
    outside = clone / "src"
    outside.mkdir()
    (outside / "main.ts").write_text("export {}\n", encoding="utf-8")
    report = write_report(home, "gh-142", "report\n")
    (report.parent / "escape").symlink_to(outside, target_is_directory=True)
    (report.parent / "escape-file").symlink_to(outside / "main.ts")

    triage.clear_scratch(home.triage_path, "gh-142")

    assert sorted(path.name for path in report.parent.iterdir()) == ["report.md"]
    assert (outside / "main.ts").is_file()


def test_a_task_that_never_had_a_directory_is_nothing_to_do(home: ConnectorHome) -> None:
    assert triage.clear_scratch(home.triage_path, "gh-404") == 0


# --- when the loop clears it ------------------------------------------------------------------


def test_the_scratch_goes_once_the_report_is_read(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    report = write_report(home, "gh-142", report_text(Verdict.ACTIONABLE, reason="real"))
    leave_scratch(report.parent)
    fake_worc.entries(**{"gh-142": "done"})

    loop.tick(dry_run=False)

    assert sorted(path.name for path in report.parent.iterdir()) == ["report.md"]
    # Read before it was cleared: the implementation task was built from it.
    assert (home.clone_path / "tasks" / "pending" / "gh-142.2.md").is_file()


def test_the_scratch_stays_while_the_triage_task_runs(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    directory = home.triage_path / "gh-142"
    leave_scratch(directory)
    fake_worc.entries(**{"gh-142": "running"})

    loop.tick(dry_run=False)

    assert (directory / "vitest.config.mjs").is_file()


def test_a_triage_task_worc_failed_is_saved_failed_and_its_scratch_cleared(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    directory = home.triage_path / "gh-142"
    leave_scratch(directory)
    fake_worc.entries(**{"gh-142": "manual_action_required"})

    loop.tick(dry_run=False)

    row = store.latest_row("github", "142")
    assert row is not None
    assert row.phase is Phase.FAILED
    assert adapter.states[-1][1] == "failed"
    assert list(directory.iterdir()) == []
