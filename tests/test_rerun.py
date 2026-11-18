"""`worc rerun`, the recovery the docs prescribe for `worc:failed`, seen by the connector (F-8).

In the second real run `gh-32.2` ended `manual_action_required`, the operator reran it, worc opened
and merged a pull request, GitHub closed the issue — and the issue kept `worc:failed`: a failed row
was followed by nobody, and a closed item leaves the open listing for good. A failure worc itself
reported is now watched for a bounded while, at the cost of one `worc list` per tick, and the item
is read only once worc says the task is moving again.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

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
from worc_connect.core.items import PullRequestState, WorkItemState
from worc_connect.core.loop import Watcher
from worc_connect.core.reconcile import RERUN_WINDOW
from worc_connect.core.state import ItemRow, Phase, StateStore
from worc_connect.core.triage import Verdict
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome

pytestmark = pytest.mark.slow

MERGED_AT = datetime(2026, 11, 18, 18, 53, tzinfo=UTC)


@dataclass
class Clock:
    """A clock a test moves by hand, so the watch window can be crossed without waiting."""

    now: datetime = datetime(2026, 11, 18, 18, 40, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(
    home: ConnectorHome, adapter: StubAdapter, store: StateStore, clock: Clock, **config: object
) -> Watcher:
    return Watcher(
        config=connector_config(home.clone_path, **config),
        adapter=adapter,
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
        now_fn=clock,
    )


def row_of(store: StateStore) -> ItemRow:
    found = store.latest_row("github", "142")
    assert found is not None
    return found


def listings(fake_worc: FakeWorc) -> int:
    return sum(1 for call in fake_worc.calls if call[:1] == ["list"])


def failed(
    home: ConnectorHome,
    store: StateStore,
    fake_worc: FakeWorc,
    *,
    adapter: StubAdapter,
    clock: Clock,
    status: str = "manual_action_required",
    **config: object,
) -> Watcher:
    """Run the item to a task worc ended with ``status``, and return the loop."""
    loop = watcher(home, adapter, store, clock, **config)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": status})
    loop.tick(dry_run=False)
    assert row_of(store).phase is Phase.FAILED
    return loop


def close_item(adapter: StubAdapter) -> None:
    """GitHub closing the issue itself — the closing keyword in a merged pull request."""
    adapter.items[0] = replace(adapter.items[0], state=WorkItemState.CLOSED)


def test_a_rerun_that_merged_brings_a_closed_item_to_done(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock)

    # `worc rerun gh-142`: a pull request, merged by worc; GitHub closes the issue.
    fake_worc.entries(**{"gh-142": "done"})
    adapter.pull_request = pull_request(state=PullRequestState.MERGED, merged_at=MERGED_AT)
    close_item(adapter)
    clock.now += timedelta(hours=1)
    loop.tick(dry_run=False)

    row = row_of(store)
    assert (row.phase, row.pr_number, row.pr_merged) == (Phase.DONE, 201, True)
    assert adapter.states[-1][1] == "done"
    assert "142" in adapter.fetched


def test_a_rerun_still_running_is_shown_in_progress(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock)

    fake_worc.entries(**{"gh-142": "running"})
    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.RUNNING
    assert adapter.states[-1][1] == "in-progress"


def test_a_failure_worc_still_lists_reads_no_item_and_keeps_its_stamp(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock)
    close_item(adapter)
    before = row_of(store)
    fetched = len(adapter.fetched)

    clock.now += timedelta(hours=1)
    loop.tick(dry_run=False)

    assert row_of(store) == before  # untouched: the window still measures from the failure
    assert len(adapter.fetched) == fetched  # worc said nothing moved, so the tracker was not asked


def test_past_the_window_a_failure_costs_nothing_at_all(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock)
    close_item(adapter)
    fake_worc.entries(**{"gh-142": "running"})  # a rerun nobody is watching for any more
    clock.now += RERUN_WINDOW + timedelta(minutes=1)
    before = listings(fake_worc)

    loop.tick(dry_run=False)

    assert listings(fake_worc) == before
    assert row_of(store).phase is Phase.FAILED


@pytest.mark.parametrize("status", ["rejected", "failed"])
def test_only_a_failure_worc_ran_into_is_watched(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc, status: str
) -> None:
    # A refusal never ran, so nothing reruns it; `failed` is worc's own failure and is watched.
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock, status=status)
    close_item(adapter)
    fake_worc.entries(**{"gh-142": "running"})

    loop.tick(dry_run=False)

    expected = Phase.FAILED if status == "rejected" else Phase.RUNNING
    assert row_of(store).phase is expected


def test_a_failure_whose_pull_request_was_closed_is_left_to_the_reopen(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    clock = Clock()
    loop = watcher(home, adapter, store, clock)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    adapter.pull_requests = {201: pull_request(state=PullRequestState.CLOSED)}
    loop.tick(dry_run=False)
    close_item(adapter)
    before = listings(fake_worc)

    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.FAILED
    assert listings(fake_worc) == before


def test_a_rerun_triage_task_has_its_report_read(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock, research=ResearchMode.WORC)

    write_report(home, "gh-142", report_text(Verdict.ACTIONABLE, reason="reproduced"))
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)

    assert row_of(store).task_id == "gh-142.2"
    assert (home.clone_path / "tasks" / "pending" / "gh-142.2.md").is_file()


def test_the_log_quotes_worc_only_for_what_worc_said_this_tick(
    home: ConnectorHome,
    store: StateStore,
    fake_worc: FakeWorc,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The real run kept printing `worc="manual_action_required"` — the row's cache — while the
    # rerun was running.
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock)
    fake_worc.entries(**{"gh-142": "running"})
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="worc_connect"):
        loop.tick(dry_run=False)

    assert "action=follow result=rerun" in caplog.text
    line = next(line for line in caplog.text.splitlines() if "action=follow result=running" in line)
    assert 'worc="running"' in line
    assert "manual_action_required" not in caplog.text


def test_a_failure_past_the_window_is_not_quoted_as_worcs_answer(
    home: ConnectorHome,
    store: StateStore,
    fake_worc: FakeWorc,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = StubAdapter(items=[work_item()])
    clock = Clock()
    loop = failed(home, store, fake_worc, adapter=adapter, clock=clock)
    clock.now += RERUN_WINDOW + timedelta(minutes=1)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="worc_connect"):
        loop.tick(dry_run=False)

    line = next(line for line in caplog.text.splitlines() if "action=follow result=" in line)
    assert "worc=" not in line
