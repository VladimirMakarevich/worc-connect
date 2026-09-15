"""The triage path end to end: one item, two tasks, and every verdict's way out.

Driven through the loop against the fake ``worc``, because what these tests are about is the
handover: worc's listing says the triage task finished, the report says what it concluded, and the
item — or a second task — is where that becomes visible. Nothing here launches an agent, which is
the point of the whole design: the connector queues a task and reads a file.

The last test in each group is the one that matters most: with the switch off, nothing in this file
is reachable at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from support import (
    FakeWorc,
    StubAdapter,
    connector_config,
    pull_request,
    report_text,
    work_item,
    write_report,
)
from worc_connect.config import ResearchMode
from worc_connect.core.loop import Watcher
from worc_connect.core.state import ItemRow, Phase, Stage, StateStore
from worc_connect.core.triage import Verdict
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome
from worc_connect.trackers.base import TrackerRateLimited

pytestmark = pytest.mark.slow

# A frozen clock a day past every stamp the stub hands out, for the one test that reads it.
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(
    home: ConnectorHome,
    adapter: StubAdapter,
    store: StateStore,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    **config: object,
) -> Watcher:
    """A watcher with the analysis step on, unless a test says otherwise."""
    config.setdefault("research", ResearchMode.WORC)
    return Watcher(
        config=connector_config(home.clone_path, **config),
        adapter=adapter,
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
        now_fn=now_fn,
    )


def rows_of(store: StateStore) -> list[ItemRow]:
    return [row for row in store.rows() if row.item_id == "142"]


def live_row(store: StateStore) -> ItemRow:
    found = store.latest_row("github", "142")
    assert found is not None
    return found


def triaged(
    home: ConnectorHome,
    store: StateStore,
    fake_worc: FakeWorc,
    *,
    adapter: StubAdapter,
    report: str,
) -> Watcher:
    """Run the item up to a finished triage task whose report is ``report``, and return the loop."""
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)  # the triage task is staged and promoted
    write_report(home, "gh-142", report)
    fake_worc.entries(**{"gh-142": "done"})
    return loop


# --- the triage task itself -------------------------------------------------------------------


def test_a_gated_item_becomes_a_triage_task_first(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])

    watcher(home, adapter, store).tick(dry_run=False)

    staged = (home.clone_path / "tasks" / "pending" / "gh-142.md").read_text(encoding="utf-8")
    assert "task_type: issue_triage" in staged
    assert "priority: high" in staged
    row = live_row(store)
    assert (row.stage, row.phase, row.task_id) == (Stage.RESEARCH, Phase.QUEUED, "gh-142")
    # The item is told a task exists, as it is for any other task: this one is worc's now too.
    assert adapter.states[-1][1] == "queued"


def test_a_triage_task_still_running_is_only_followed(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "running"})

    loop.tick(dry_run=False)

    row = live_row(store)
    assert (row.stage, row.phase) == (Stage.RESEARCH, Phase.RUNNING)
    assert len(rows_of(store)) == 1
    assert adapter.states[-1][1] == "in-progress"


# --- each verdict's way out -------------------------------------------------------------------


def test_an_actionable_report_produces_the_implementation_task(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(
            Verdict.ACTIONABLE,
            reason="The validator never checks for a dot.",
            acceptance_criteria=("A signup with `foo@` is rejected.",),
            failing_test={"path": "tests/test_signup.py", "body": "assert not validate('foo@')"},
        ),
    )

    loop.tick(dry_run=False)

    # Two rows, two ids, never the same one twice: the analysis and the work it decided on.
    assert [(row.seq, row.stage, row.task_id) for row in rows_of(store)] == [
        (1, Stage.RESEARCH, "gh-142"),
        (2, Stage.IMPLEMENTATION, "gh-142.2"),
    ]
    assert rows_of(store)[0].phase is Phase.RESEARCHED
    implementation = live_row(store)
    assert (implementation.phase, implementation.research_task_id) == (Phase.QUEUED, "gh-142")

    staged = (home.clone_path / "tasks" / "pending" / "gh-142.2.md").read_text(encoding="utf-8")
    assert "The validator never checks for a dot." in staged
    assert "## Acceptance criteria" in staged
    assert "## Failing test" in staged
    assert "task_type: issue_triage" not in staged


def test_the_item_is_never_shown_the_step_between_the_two_tasks(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    # `worc:queued` for the triage task, then `worc:in-progress` when the implementation runs —
    # an "analysed" label in between would be a step nobody can act on.
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.ACTIONABLE, reason="do it"),
    )

    loop.tick(dry_run=False)

    assert [state for _, state, _ in adapter.states] == ["queued"]
    assert "researched" not in {state for _, state, _ in adapter.states}


def test_a_needs_info_report_asks_the_reporter_and_queues_nothing(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.NEEDS_INFO, reason="Which version of the client are you on?"),
    )

    loop.tick(dry_run=False)

    row = live_row(store)
    assert (row.phase, row.seq) == (Phase.NEEDS_INFO, 1)
    assert len(rows_of(store)) == 1
    assert adapter.states[-1][1] == "needs-info"
    body = adapter.comments[-1][1]
    assert "Which version of the client are you on?" in body
    assert not (home.clone_path / "tasks" / "pending" / "gh-142.2.md").exists()


def test_a_duplicate_report_declines_the_item_and_names_the_duplicate(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(
            Verdict.DUPLICATE, reason="Already fixed in the validator.", duplicate_of="#7"
        ),
    )

    loop.tick(dry_run=False)

    assert live_row(store).phase is Phase.DECLINED
    assert adapter.states[-1][1] == "declined"
    body = adapter.comments[-1][1]
    assert "Already fixed in the validator." in body
    assert "#7" in body


def test_a_declined_report_says_why_and_creates_no_task(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.DECLINED, reason="This is a support question, not a defect."),
    )

    loop.tick(dry_run=False)

    assert live_row(store).phase is Phase.DECLINED
    assert "support question" in adapter.comments[-1][1]
    assert len(rows_of(store)) == 1


def test_a_verdict_reached_once_is_not_reached_again_on_every_tick(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.DECLINED, reason="out of scope"),
    )
    loop.tick(dry_run=False)

    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    assert [state for _, state, _ in adapter.states].count("declined") == 1
    assert len(adapter.comments) == 2  # the queued comment, and the one declining it


# --- the report that is not there --------------------------------------------------------------


def test_a_triage_task_that_left_no_report_fails_and_says_where_it_looked(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})  # finished, and wrote nothing

    loop.tick(dry_run=False)

    assert live_row(store).phase is Phase.FAILED
    assert adapter.states[-1][1] == "failed"
    body = adapter.comments[-1][1]
    assert ".worc-connect/triage/gh-142/report.md" in body
    # Never a fallback to another location — least of all inside worc's own home.
    assert ".worc/" not in body.replace(".worc-connect/", "")


def test_a_report_carrying_no_verdict_is_the_same_failure(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(home, store, fake_worc, adapter=adapter, report="prose, no verdict block\n")

    loop.tick(dry_run=False)

    assert live_row(store).phase is Phase.FAILED
    assert not (home.clone_path / "tasks" / "pending" / "gh-142.2.md").exists()


# --- the round trip a needs-info verdict opens -------------------------------------------------


def test_the_reporter_answering_runs_triage_again_with_the_next_id(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.NEEDS_INFO, reason="which version?"),
    )
    loop.tick(dry_run=False)
    assert live_row(store).phase is Phase.NEEDS_INFO

    # The reporter replies: the item is updated, and still carries the trigger label.
    later = datetime(2026, 9, 10, 10, 0, tzinfo=UTC) + timedelta(hours=1)
    adapter.items = [work_item(labels=("worc", "worc:needs-info"), updated_at=later)]
    loop.tick(dry_run=False)

    follow_up = live_row(store)
    assert (follow_up.seq, follow_up.stage, follow_up.task_id) == (2, Stage.RESEARCH, "gh-142.2")
    staged = (home.clone_path / "tasks" / "pending" / "gh-142.2.md").read_text(encoding="utf-8")
    assert "task_type: issue_triage" in staged


def test_a_needs_info_item_nobody_answered_is_left_alone(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    # The trigger label is still on, and the connector's own question — the label and the comment
    # — moved the item's update stamp. Keyed off the label alone, or measured from the stamp the
    # attempt started with, that would read as the reporter's answer and start a fresh triage task
    # on every single tick.
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.NEEDS_INFO, reason="which version?"),
    )
    loop.tick(dry_run=False)
    asked = adapter.items[0].updated_at
    assert asked > work_item().updated_at
    assert live_row(store).item_updated_at == asked

    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    assert [row.seq for row in rows_of(store)] == [1]
    assert [state for _, state, _ in adapter.states].count("needs-info") == 1


def test_a_question_whose_stamp_cannot_be_read_back_is_measured_from_the_connectors_clock(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc, caplog: pytest.LogCaptureFixture
) -> None:
    # The write went through and the read right after it did not, so the connector's own clock at
    # that moment stands in for the tracker's stamp. A reply inside the skew between the two clocks
    # is missed, which costs nothing; a stale stamp would cost a triage run per tick.
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store, now_fn=lambda: NOW)
    loop.tick(dry_run=False)
    write_report(home, "gh-142", report_text(Verdict.NEEDS_INFO, reason="which version?"))
    fake_worc.entries(**{"gh-142": "done"})
    adapter.fetch_failures = [TrackerRateLimited("slow down")]

    with caplog.at_level(logging.WARNING, logger="worc_connect.core.retrigger"):
        loop.tick(dry_run=False)

    assert live_row(store).item_updated_at == NOW
    assert "action=retrigger result=stamped-from-clock (TrackerRateLimited" in caplog.text
    loop.tick(dry_run=False)
    assert [row.seq for row in rows_of(store)] == [1]

    replied = NOW + timedelta(hours=1)
    adapter.items = [work_item(labels=("worc", "worc:needs-info"), updated_at=replied)]
    loop.tick(dry_run=False)

    assert live_row(store).seq == 2


# --- with the switch off -----------------------------------------------------------------------


def test_with_the_step_off_the_item_becomes_an_implementation_task_directly(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())

    loop = watcher(home, adapter, store, research=ResearchMode.OFF)
    loop.tick(dry_run=False)

    row = live_row(store)
    assert row.stage is Stage.IMPLEMENTATION
    staged = (home.clone_path / "tasks" / "pending" / "gh-142.md").read_text(encoding="utf-8")
    assert "task_type: issue_triage" not in staged
    assert "priority: high" not in staged


def test_with_the_step_off_nothing_of_it_is_reachable(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc, clone: Path
) -> None:
    # A report left in place by an earlier run with the step on must not be picked up, no flow file
    # may be written, and the row must never leave the implementation path.
    write_report(home, "gh-142", report_text(Verdict.DECLINED, reason="ignore me"))
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store, research=ResearchMode.OFF)

    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)

    row = live_row(store)
    assert (row.stage, row.phase) == (Stage.IMPLEMENTATION, Phase.PR_OPEN)
    assert row.research_note is None
    assert not (clone / ".worc").exists()
    assert {state for _, state, _ in adapter.states} == {"queued", "pr-open"}


def test_a_dry_run_with_the_step_on_names_the_triage_task_it_would_queue(
    home: ConnectorHome, clone: Path, fake_worc: FakeWorc
) -> None:
    read_only = StateStore.read_only(home.state_path)
    adapter = StubAdapter(items=[work_item()])

    report = watcher(home, adapter, read_only).tick(dry_run=True)

    assert report.actions[0].task_id == "gh-142"
    assert list(clone.rglob("tasks")) == []
    assert fake_worc.calls == []
    read_only.close()


# --- the cache deleted mid-triage ----------------------------------------------------------------


def test_a_row_rebuilt_while_the_triage_task_runs_still_follows_the_triage_path(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    # The state label says `in-progress` and worc's listing names the task; neither says which of
    # the item's two possible tasks it is. The connector's own triage home does: the directory the
    # report will land in exists from the tick the task was staged.
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    assert (home.triage_path / "gh-142").is_dir()
    fake_worc.entries(**{"gh-142": "running"})
    loop.tick(dry_run=False)

    store.close()
    home.state_path.unlink()
    rebuilt_store = StateStore(home.state_path)
    loop = watcher(home, adapter, rebuilt_store)
    loop.tick(dry_run=False)
    rebuilt = rebuilt_store.latest_row("github", "142")
    assert rebuilt is not None
    assert (rebuilt.stage, rebuilt.phase, rebuilt.task_id) == (
        Stage.RESEARCH,
        Phase.RUNNING,
        "gh-142",
    )

    write_report(home, "gh-142", report_text(Verdict.ACTIONABLE, reason="do it"))
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)

    implementation = rebuilt_store.latest_row("github", "142")
    assert implementation is not None
    assert (implementation.seq, implementation.stage, implementation.research_task_id) == (
        2,
        Stage.IMPLEMENTATION,
        "gh-142",
    )
    assert (home.clone_path / "tasks" / "pending" / "gh-142.2.md").exists()
    rebuilt_store.close()


def test_a_rebuilt_implementation_row_remembers_the_triage_task_it_came_from(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = triaged(
        home,
        store,
        fake_worc,
        adapter=adapter,
        report=report_text(Verdict.ACTIONABLE, reason="do it"),
    )
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done", "gh-142.2": "running"})
    loop.tick(dry_run=False)

    store.close()
    home.state_path.unlink()
    rebuilt_store = StateStore(home.state_path)
    watcher(home, adapter, rebuilt_store).tick(dry_run=False)

    rebuilt = rebuilt_store.latest_row("github", "142")
    assert rebuilt is not None
    assert (rebuilt.task_id, rebuilt.stage, rebuilt.research_task_id) == (
        "gh-142.2",
        Stage.IMPLEMENTATION,
        "gh-142",
    )
    rebuilt_store.close()


def test_with_the_step_off_a_rebuilt_row_ignores_a_triage_directory_left_behind(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    (home.triage_path / "gh-142").mkdir(parents=True)
    fake_worc.entries(**{"gh-142": "running"})
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])

    watcher(home, adapter, store, research=ResearchMode.OFF).tick(dry_run=False)

    assert live_row(store).stage is Stage.IMPLEMENTATION
