"""The crossing into worc, driven end to end through fake ``gh``, fake ``worc`` and a fake ``git``.

These are the assertions the whole boundary rests on, and they are made from recordings rather than
from the state of the host: the only worc invocation is ``promote``, the only launcher named ``git``
is one that would have refused and was never called, and the only file left under the clone is the
task itself. A test that inspected the filesystem alone would pass just as happily against a
connector that had run ``git status`` on the way.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from support import FakeGit, FakeWorc, StubAdapter, connector_config, work_item
from worc_connect.core.loop import Watcher
from worc_connect.core.state import Phase, StateStore
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome

pytestmark = pytest.mark.slow

SENTINEL = "canary-a7f3e1-do-not-leak"


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(home: ConnectorHome, adapter: StubAdapter, store: StateStore) -> Watcher:
    """A watcher wired to the fakes on ``PATH`` and to the clone the fixtures created."""
    return Watcher(
        config=connector_config(home.clone_path),
        adapter=adapter,
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
    )


def clone_files(clone: Path) -> set[str]:
    """Every file under the clone except the connector's own home, as posix paths."""
    return {
        path.relative_to(clone).as_posix()
        for path in clone.rglob("*")
        if path.is_file() and ".worc-connect" not in path.parts
    }


def test_a_gated_item_becomes_a_promoted_worc_task(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc, refuse_git: FakeGit
) -> None:
    adapter = StubAdapter(items=[work_item()])

    watcher(home, adapter, store).tick(dry_run=False)

    assert clone_files(home.clone_path) == {"tasks/pending/gh-142.md"}
    assert fake_worc.calls == [["promote", "gh-142"]]
    assert refuse_git.calls == []
    row = store.latest_row("github", "142")
    assert row is not None
    assert (row.phase, row.task_id) == (Phase.QUEUED, "gh-142")


def test_the_connector_creates_the_staging_directory_it_needs(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    assert not (clone / "tasks").exists()

    watcher(home, StubAdapter(items=[work_item()]), store).tick(dry_run=False)

    assert (clone / "tasks" / "preparing").is_dir()


def test_a_crash_before_promote_leaves_one_file_and_one_row(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(promote={"stdout": "promote: the daemon is busy\n", "exit_code": 1})
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)

    loop.tick(dry_run=False)

    staged = store.latest_row("github", "142")
    assert staged is not None and staged.phase is Phase.STAGED
    assert clone_files(clone) == {"tasks/preparing/gh-142.md"}

    fake_worc.configure(promote=False)  # the next tick runs a promote that works
    loop.tick(dry_run=False)

    assert clone_files(clone) == {"tasks/pending/gh-142.md"}
    assert fake_worc.calls == [["promote", "gh-142"], ["promote", "gh-142"]]
    assert len(store.rows()) == 1
    queued = store.latest_row("github", "142")
    assert queued is not None and queued.phase is Phase.QUEUED


def test_a_task_already_in_pending_is_a_success_not_an_error(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    queued_before = clone / "tasks" / "pending" / "gh-142.md"
    queued_before.parent.mkdir(parents=True)
    queued_before.write_text("the task worc already holds\n", encoding="utf-8", newline="")

    watcher(home, StubAdapter(items=[work_item()]), store).tick(dry_run=False)

    row = store.latest_row("github", "142")
    assert row is not None and row.phase is Phase.QUEUED
    # worc's file is untouched: promote refuses to overwrite a queued task, and so does the
    # connector, which never writes into pending/ itself.
    assert queued_before.read_text(encoding="utf-8") == "the task worc already holds\n"


def test_a_task_file_that_vanished_without_reaching_worc_is_reported_as_failed(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(promote={"stdout": "promote: the daemon is busy\n", "exit_code": 1})
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    (clone / "tasks" / "preparing" / "gh-142.md").unlink()  # a gate reject, seen from outside

    loop.tick(dry_run=False)

    row = store.latest_row("github", "142")
    assert row is not None and row.phase is Phase.FAILED


def test_a_vanished_task_worc_does_know_about_is_queued_not_failed(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(promote={"stdout": "promote: the daemon is busy\n", "exit_code": 1})
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    (clone / "tasks" / "preparing" / "gh-142.md").unlink()
    fake_worc.entries(**{"gh-142": "running (paused)"})

    loop.tick(dry_run=False)

    row = store.latest_row("github", "142")
    assert row is not None and row.phase is Phase.QUEUED


def test_the_task_file_is_written_with_unix_line_endings_on_every_platform(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(promote={"stdout": "promote: not now\n", "exit_code": 1})

    watcher(home, StubAdapter(items=[work_item()]), store).tick(dry_run=False)

    raw = (clone / "tasks" / "preparing" / "gh-142.md").read_bytes()
    assert b"\r\n" not in raw
    assert raw.decode("utf-8").startswith("---\n")


def test_the_item_text_reaches_the_task_file_and_nowhere_else(
    *,
    home: ConnectorHome,
    store: StateStore,
    fake_worc: FakeWorc,
    refuse_git: FakeGit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    item = work_item(title=f"{SENTINEL} in the title", body=f"body with {SENTINEL} inside")
    adapter = StubAdapter(items=[item])

    with caplog.at_level(logging.DEBUG):
        watcher(home, adapter, store).tick(dry_run=False)

    task_file = home.clone_path / "tasks" / "pending" / "gh-142.md"
    assert SENTINEL in task_file.read_text(encoding="utf-8")
    assert SENTINEL not in caplog.text
    for call in fake_worc.calls:
        assert SENTINEL not in " ".join(call)
    assert refuse_git.calls == []


def test_a_re_triggered_item_gets_the_next_id_and_never_the_previous_one(
    home: ConnectorHome, store: StateStore, clone: Path, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    first = store.latest_row("github", "142")
    assert first is not None
    store.save(replace(first, phase=Phase.DONE))  # worc finished it; the write-back is phase 05

    adapter.items = [work_item(labels=())]  # the maintainer takes the trigger label off
    loop.tick(dry_run=False)

    armed = store.latest_row("github", "142")
    assert armed is not None and armed.retrigger_armed is True
    assert len(store.rows()) == 1

    adapter.items = [work_item()]  # and puts it back: a new request for the same item
    loop.tick(dry_run=False)

    assert {row.task_id for row in store.rows()} == {"gh-142", "gh-142.2"}
    assert clone_files(clone) == {"tasks/pending/gh-142.md", "tasks/pending/gh-142.2.md"}


def test_a_terminal_item_still_carrying_the_label_is_not_re_triggered(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    first = store.latest_row("github", "142")
    assert first is not None
    store.save(replace(first, phase=Phase.FAILED))

    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    assert [row.task_id for row in store.rows()] == ["gh-142"]


def test_a_dry_run_stages_nothing_and_launches_no_worc(
    home: ConnectorHome, clone: Path, fake_worc: FakeWorc, refuse_git: FakeGit
) -> None:
    read_only = StateStore.read_only(home.state_path)
    adapter = StubAdapter(items=[work_item()])

    report = watcher(home, adapter, read_only).tick(dry_run=True)

    planned = report.actions[0]
    assert (planned.task_id, planned.branch) == (
        "gh-142",
        "worc/gh-142-signup-form-accepts-foo-as-an-email",
    )
    assert clone_files(clone) == set()
    assert fake_worc.calls == []
    assert refuse_git.calls == []
    read_only.close()
