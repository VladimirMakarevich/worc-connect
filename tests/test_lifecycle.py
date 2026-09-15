"""One item from the trigger label to a closed issue, and every way that path can bend.

Driven through the loop rather than through the pieces, because what these tests are about is the
handover between them: worc's listing decides a task is finished, the pull request decides what
finished means, and the item is where both become visible. The fake ``worc`` answers the listing,
the stub adapter stands in for the tracker, and nothing here launches the real thing.

The pull-request phases are recomputed from the request's live state on every tick, never carried
over, which is what the merge, the reopen and the owner's own edits are here to prove.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support import (
    FakeWorc,
    StubAdapter,
    connector_config,
    listed_task,
    pull_request,
    work_item,
)
from worc_connect.core.items import PullRequestState
from worc_connect.core.loop import Watcher
from worc_connect.core.state import ItemRow, Phase, StateStore
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome

pytestmark = pytest.mark.slow

MERGED_AT = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
BRANCH = "worc/gh-142-signup-form-accepts-foo-as-an-email"


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(
    home: ConnectorHome, adapter: StubAdapter, store: StateStore, **config: object
) -> Watcher:
    return Watcher(
        config=connector_config(home.clone_path, **config),
        adapter=adapter,
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
    )


def row_of(store: StateStore) -> ItemRow:
    found = store.latest_row("github", "142")
    assert found is not None
    return found


def test_an_item_walks_from_queued_to_a_closed_issue(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)

    loop.tick(dry_run=False)  # staged and promoted
    assert row_of(store).phase is Phase.QUEUED
    assert adapter.states[-1][1] == "queued"

    fake_worc.entries(**{"gh-142": "running"})
    loop.tick(dry_run=False)
    assert row_of(store).phase is Phase.RUNNING
    assert adapter.states[-1][1] == "in-progress"

    fake_worc.entries(**{"gh-142": "done"})
    adapter.pull_request = pull_request()
    loop.tick(dry_run=False)
    row = row_of(store)
    assert (row.phase, row.pr_number) == (Phase.PR_OPEN, 201)
    assert adapter.states[-1][1] == "pr-open"
    assert row.pr_url in adapter.comments[-1][1]

    adapter.pull_requests = {201: pull_request(state=PullRequestState.MERGED, merged_at=MERGED_AT)}
    loop.tick(dry_run=False)
    row = row_of(store)
    assert (row.phase, row.pr_merged) == (Phase.DONE, True)
    assert adapter.states[-1][1] == "done"
    assert adapter.closed[0][0] == "142"


def test_the_pull_request_is_found_once_and_read_by_number_afterwards(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})

    loop.tick(dry_run=False)
    adapter.pull_request = None  # the branch was deleted after the merge
    adapter.pull_requests = {201: pull_request(state=PullRequestState.OPEN)}
    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    # Found once, by the branch the connector named; read by number on every tick after that.
    assert adapter.found_by_branch == [BRANCH]
    assert adapter.read_by_number == [201, 201]
    assert row_of(store).phase is Phase.PR_OPEN


def test_a_pull_request_closed_without_a_merge_fails_the_row_once(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)

    adapter.pull_requests = {201: pull_request(state=PullRequestState.CLOSED)}
    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.FAILED
    assert [state for _, state, _ in adapter.states].count("failed") == 1
    assert adapter.closed == []


def test_a_pull_request_reopened_puts_the_row_back(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    adapter.pull_requests = {201: pull_request(state=PullRequestState.CLOSED)}
    loop.tick(dry_run=False)

    adapter.pull_requests = {201: pull_request(state=PullRequestState.OPEN)}
    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.PR_OPEN
    assert [state for _, state, _ in adapter.states][-1] == "pr-open"


def test_the_owners_edits_and_squash_merge_still_close_the_item(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    found = len(adapter.found_by_branch)

    # Retitled, squash-merged, branch deleted: none of it reaches the connector's discovery.
    adapter.pull_request = None
    adapter.pull_requests = {201: pull_request(state=PullRequestState.MERGED, merged_at=MERGED_AT)}
    loop.tick(dry_run=False)

    assert len(adapter.found_by_branch) == found
    assert row_of(store).phase is Phase.DONE
    assert adapter.closed[0][0] == "142"


def test_a_task_worc_failed_is_reported_on_the_item(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "manual_action_required"})

    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.FAILED
    assert adapter.states[-1][1] == "failed"
    assert "manual_action_required" in adapter.comments[-1][1]
    assert "worc status gh-142" in adapter.comments[-1][1]


def test_a_task_worcs_gate_rejected_is_reported_without_inventing_a_reason(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    # What a Phase-A reject looks like from outside worc's home: the file left pending/ and the
    # listing never gained a row for it.
    (home.clone_path / "tasks" / "pending" / "gh-142.md").unlink()

    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.FAILED
    body = adapter.comments[-1][1]
    assert "gh-142" in body and "worc status gh-142" in body


def test_a_refused_task_reports_the_reason_worc_published_for_it(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    # The reject as worc now publishes it: the file left pending/, and the `--all` listing carries
    # the id in its rejected section with the reason the gate recorded.
    (home.clone_path / "tasks" / "pending" / "gh-142.md").unlink()
    fake_worc.configure(
        entries=[listed_task("gh-142", "rejected", validation_reason="injection_suspected")]
    )

    loop.tick(dry_run=False)

    row = row_of(store)
    assert (row.phase, row.validation_reason) == (Phase.FAILED, "injection_suspected")
    assert adapter.states[-1][1] == "failed"
    assert "injection_suspected" in adapter.comments[-1][1]


def test_a_refused_task_is_terminal_even_while_worc_still_lists_the_id(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    # The id never leaves the rejected section, so a connector that read it as "worc has this one"
    # would wait on a task that will never run.
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.configure(
        entries=[listed_task("gh-142", "rejected", validation_reason="injection_suspected")]
    )

    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    assert row_of(store).phase is Phase.FAILED
    assert [state for _, state, _ in adapter.states].count("failed") == 1


def test_the_pull_request_worc_recorded_is_used_instead_of_searching_the_branch(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    url = "https://github.com/OWNER/REPO/pull/201"
    adapter = StubAdapter(items=[work_item()], pull_requests={201: pull_request()})
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.configure(entries=[listed_task("gh-142", "done", pr_url=url)])

    loop.tick(dry_run=False)

    row = row_of(store)
    assert (row.phase, row.pr_number, row.pr_url) == (Phase.PR_OPEN, 201, url)
    # worc's own record names the request exactly, so the branch is never searched — which is the
    # case a squash merge that deleted the head leaves behind.
    assert adapter.found_by_url == [url]
    assert adapter.found_by_branch == []


def test_a_worc_that_recorded_no_pull_request_is_still_searched_by_branch(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})  # an entry whose `pr_url` is null

    loop.tick(dry_run=False)

    assert adapter.found_by_url == []
    assert adapter.found_by_branch == [BRANCH]
    assert row_of(store).pr_number == 201


def test_a_worc_that_ships_the_contract_gets_the_tracker_s_closing_line(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    watcher(home, StubAdapter(items=[work_item()]), store).tick(dry_run=False)

    staged = (home.clone_path / "tasks" / "pending" / "gh-142.md").read_text(encoding="utf-8")
    # Quoted by the YAML writer because `#` after a space would otherwise open a comment; worc
    # reads back the bare line and appends exactly that to the pull-request body.
    assert "references:\n- 'Fixes #142'\n" in staged


def test_a_worc_from_before_the_contract_gets_no_references_key_at_all(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(version="0.13.0a1")

    watcher(home, StubAdapter(items=[work_item()]), store).tick(dry_run=False)

    staged = (home.clone_path / "tasks" / "pending" / "gh-142.md").read_text(encoding="utf-8")
    assert "references" not in staged
    assert "Fixes #142" not in staged


def test_a_tracker_with_no_closing_keyword_emits_no_references_key(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], closing_keyword=None)

    watcher(home, adapter, store).tick(dry_run=False)

    staged = (home.clone_path / "tasks" / "pending" / "gh-142.md").read_text(encoding="utf-8")
    assert "references" not in staged


def test_a_trigger_label_removed_while_queued_changes_nothing_about_the_task(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "running"})
    adapter.items = [work_item(labels=("worc:queued",))]

    loop.tick(dry_run=False)

    row = row_of(store)
    assert (row.phase, row.task_id) == (Phase.RUNNING, "gh-142")
    assert row.retrigger_armed is True


def test_a_quiet_tick_writes_nothing_anywhere(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "running"})
    loop.tick(dry_run=False)
    before = (row_of(store), list(adapter.states), list(adapter.comments), len(fake_worc.calls))

    loop.tick(dry_run=False)

    after = (row_of(store), adapter.states, adapter.comments, len(fake_worc.calls))
    assert after[0].phase is before[0].phase
    assert after[1] == before[1]
    assert after[2] == before[2]
    assert after[3] == before[3] + 1  # one more `worc list`, and nothing else


def test_the_rows_are_rebuilt_from_the_item_and_worc_when_the_database_is_gone(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    assert row_of(store).phase is Phase.PR_OPEN

    # The operator deletes the cache; the item still carries `worc:pr-open`.
    store.close()
    home.state_path.unlink()
    rebuilt_store = StateStore(home.state_path)
    assert rebuilt_store.rows() == []
    adapter.found_by_branch.clear()

    watcher(home, adapter, rebuilt_store).tick(dry_run=False)

    rebuilt = rebuilt_store.latest_row("github", "142")
    assert rebuilt is not None
    assert (rebuilt.task_id, rebuilt.phase) == ("gh-142", Phase.PR_OPEN)
    assert adapter.found_by_branch == [BRANCH]
    assert Path(home.clone_path / "tasks" / "pending" / "gh-142.2.md").exists() is False
    rebuilt_store.close()


def test_a_follow_up_while_the_pull_request_is_open_continues_the_same_branch(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    assert row_of(store).phase is Phase.PR_OPEN

    adapter.items = [work_item(labels=("worc:pr-open",))]  # the label comes off
    loop.tick(dry_run=False)
    adapter.items = [work_item(labels=("worc", "worc:pr-open"))]  # and goes back on
    loop.tick(dry_run=False)

    follow_up = row_of(store)
    assert (follow_up.seq, follow_up.task_id) == (2, "gh-142.2")
    staged = (home.clone_path / "tasks" / "pending" / "gh-142.2.md").read_text(encoding="utf-8")
    assert "branch_mode: existing" in staged
    assert f"branch_ref: {BRANCH}" in staged
    assert "branch_name:" not in staged


def test_a_follow_up_after_a_merge_gets_a_fresh_branch(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    adapter.pull_requests = {201: pull_request(state=PullRequestState.MERGED, merged_at=MERGED_AT)}
    loop.tick(dry_run=False)  # merged: the item is closed and the re-trigger is armed

    adapter.items = [work_item(labels=("worc", "worc:done"))]
    loop.tick(dry_run=False)

    follow_up = row_of(store)
    assert follow_up.task_id == "gh-142.2"
    staged = (home.clone_path / "tasks" / "pending" / "gh-142.2.md").read_text(encoding="utf-8")
    assert "branch_name: worc/gh-142.2-" in staged
    assert "branch_ref" not in staged


def test_a_trigger_cycled_while_the_task_runs_produces_no_second_task_when_it_ends(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "running"})
    adapter.items = [work_item(labels=("worc:in-progress",))]  # the label comes off mid-run
    loop.tick(dry_run=False)
    assert row_of(store).retrigger_armed is True
    adapter.items = [work_item(labels=("worc", "worc:in-progress"))]  # and goes back a minute on
    loop.tick(dry_run=False)
    assert row_of(store).retrigger_armed is False

    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)

    row = row_of(store)
    assert (row.seq, row.task_id, row.phase) == (1, "gh-142", Phase.PR_OPEN)
    assert not (home.clone_path / "tasks" / "pending" / "gh-142.2.md").exists()


def test_a_trigger_re_applied_only_once_the_pull_request_is_open_is_a_new_request(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    # The boundary of the rule above: withdrawn during the run and still withdrawn when the task
    # hands over to a pull request, the label coming back asks for more — the same request as
    # cycling it while the pull request is open.
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "running"})
    adapter.items = [work_item(labels=("worc:in-progress",))]
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    assert (row_of(store).phase, row_of(store).retrigger_armed) == (Phase.PR_OPEN, True)

    adapter.items = [work_item(labels=("worc", "worc:pr-open"))]
    loop.tick(dry_run=False)

    assert (row_of(store).seq, row_of(store).task_id) == (2, "gh-142.2")
