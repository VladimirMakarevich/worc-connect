"""What the item is told about its pull request: the link once, the merge, and a close unmerged.

Four findings of the second real run, all in what the item reads. A pull request worc merged before
any tick saw it open left the item with no link to it at all (F-2). The closing message rode on
`gh issue close --comment`, which posts nothing on an issue GitHub had already closed through
`Fixes #<n>` — so it was lost on every merge, and the log still said `close result=merged` (F-3). A
pull request closed unmerged was reported as "opened no pull request" (F-4). And reopening one, or
continuing it with a follow-up, posted "opened a pull request" again for the same request (O-6).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support import FakeWorc, StubAdapter, connector_config, pull_request, work_item
from worc_connect.core.items import PullRequestState, WorkItemState
from worc_connect.core.loop import Watcher
from worc_connect.core.state import ItemRow, Phase, StateStore
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.core.writeback import Published, WriteBack
from worc_connect.home import ConnectorHome

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=UTC)
MERGED_AT = datetime(2026, 11, 18, 18, 19, tzinfo=UTC)
URL = "https://github.com/OWNER/REPO/pull/201"


def row(phase: Phase, **fields: object) -> ItemRow:
    base = ItemRow(
        tracker="github",
        item_id="142",
        seq=1,
        phase=phase,
        created_at=NOW,
        updated_at=NOW,
        task_id="gh-142",
        branch="worc/gh-142-slug",
        pr_number=201,
        pr_url=URL,
    )
    return replace(base, **fields)  # type: ignore[arg-type]


def write_back(clone: Path, adapter: StubAdapter, **config: object) -> WriteBack:
    return WriteBack(config=connector_config(clone, **config), adapter=adapter)


def bodies(adapter: StubAdapter) -> list[str]:
    return [body for _, body in adapter.comments]


# --- F-2: the link to a request merged before anybody saw it open -----------------------------


def test_a_merge_nobody_saw_open_still_tells_the_item_the_link(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])

    write_back(clone, adapter, close_on_merge=False).publish(
        row(Phase.DONE, pr_merged=True), adapter.items[0]
    )

    assert adapter.states == [("142", "done", "in-progress")]
    assert len(adapter.comments) == 1
    assert URL in bodies(adapter)[0]
    assert "merged" in bodies(adapter)[0]
    assert adapter.closed == []


def test_a_link_the_item_was_already_told_is_not_repeated_on_the_merge(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])

    write_back(clone, adapter, close_on_merge=False).publish(
        row(Phase.DONE, pr_merged=True, pr_announced=True), adapter.items[0]
    )

    assert adapter.comments == []


# --- F-3: the closing message survives the code host closing first ----------------------------


def test_the_closing_message_is_posted_although_the_item_is_already_closed(
    clone: Path, caplog: pytest.LogCaptureFixture
) -> None:
    item = work_item(labels=("worc", "worc:in-progress"), state=WorkItemState.CLOSED)
    adapter = StubAdapter(items=[item])

    with caplog.at_level(logging.INFO, logger="worc_connect"):
        published = write_back(clone, adapter).publish(row(Phase.DONE, pr_merged=True), item)

    assert published is Published.CLOSED  # closed by the merge: a later trigger is a new request
    assert adapter.closed == []  # nothing to close, and no claim that the connector closed it
    assert len(adapter.comments) == 1
    assert "gh-142" in bodies(adapter)[0] and URL in bodies(adapter)[0]
    assert "action=close result=already-closed" in caplog.text
    assert "action=close result=merged" not in caplog.text


def test_an_item_still_open_is_told_first_and_closed_after(
    clone: Path, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])

    with caplog.at_level(logging.INFO, logger="worc_connect"):
        write_back(clone, adapter).publish(
            row(Phase.DONE, pr_merged=True, pr_announced=True), adapter.items[0]
        )

    assert adapter.closed == ["142"]
    assert URL in bodies(adapter)[0]
    assert "action=close result=merged" in caplog.text


def test_the_closing_message_is_not_switched_off_with_the_comments(clone: Path) -> None:
    # As before: `write_back.comment: false` silences the progress comments, not the close's word.
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])

    write_back(clone, adapter, comment=False).publish(
        row(Phase.DONE, pr_merged=True), adapter.items[0]
    )

    assert adapter.closed == ["142"]
    assert len(adapter.comments) == 1


# --- F-4: a request closed unmerged is named, not denied ---------------------------------------


def test_a_request_closed_unmerged_is_named_in_the_failure(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])

    write_back(clone, adapter).publish(
        row(Phase.FAILED, last_status="done", pr_announced=True), adapter.items[0]
    )

    body = bodies(adapter)[0]
    assert URL in body
    assert "closed without being merged" in body
    assert "opened no pull request" not in body
    assert "ended as `done`" not in body


# --- O-6: one announcement per request ---------------------------------------------------------


def test_a_reopened_request_is_not_announced_again(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:failed"))])

    write_back(clone, adapter).publish(row(Phase.PR_OPEN, pr_announced=True), adapter.items[0])

    assert adapter.states == [("142", "pr-open", "failed")]
    assert adapter.comments == []


def test_a_follow_up_says_it_continued_the_request(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])

    write_back(clone, adapter).publish(
        row(Phase.PR_OPEN, seq=3, task_id="gh-142.3", continued_pr=201), adapter.items[0]
    )

    body = bodies(adapter)[0]
    assert "gh-142.3" in body and URL in body
    assert "continued the open pull request" in body
    assert "opened" not in body


# --- the whole path, through the loop -----------------------------------------------------------


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


def link_comments(adapter: StubAdapter) -> list[str]:
    return [body for body in bodies(adapter) if URL in body]


@pytest.mark.slow
def test_an_auto_merged_task_gets_its_link_once_and_no_close_on_a_closed_item(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "running"})
    loop.tick(dry_run=False)

    # worc merged its own pull request, and GitHub closed the issue through `Fixes #142`, all
    # between two ticks.
    fake_worc.entries(**{"gh-142": "done"})
    adapter.pull_request = pull_request(state=PullRequestState.MERGED, merged_at=MERGED_AT)
    adapter.items[0] = replace(adapter.items[0], state=WorkItemState.CLOSED)
    loop.tick(dry_run=False)
    loop.tick(dry_run=False)

    assert adapter.states[-1][1] == "done"
    assert len(link_comments(adapter)) == 1
    assert adapter.closed == []


@pytest.mark.slow
def test_a_request_closed_and_reopened_is_announced_once(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)  # pr-open, with the link

    adapter.pull_requests = {201: pull_request(state=PullRequestState.CLOSED)}
    loop.tick(dry_run=False)  # failed, naming the closed request
    adapter.pull_requests = {201: pull_request(state=PullRequestState.OPEN)}
    loop.tick(dry_run=False)  # pr-open again, quietly

    texts = bodies(adapter)
    assert sum("opened a pull request" in text for text in texts) == 1
    assert sum("closed without being merged" in text for text in texts) == 1
    assert adapter.states[-1][1] == "pr-open"


@pytest.mark.slow
def test_a_follow_up_on_the_open_request_says_it_continued_it(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()], pull_request=pull_request())
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)

    adapter.items[0] = replace(adapter.items[0], labels=("worc:pr-open",))
    loop.tick(dry_run=False)  # the trigger withdrawn: armed
    adapter.items[0] = replace(adapter.items[0], labels=("worc", "worc:pr-open"))
    loop.tick(dry_run=False)  # re-applied: gh-142.2 continues the branch of the open request
    fake_worc.entries(**{"gh-142": "done", "gh-142.2": "done"})
    loop.tick(dry_run=False)

    follow_up = store.latest_row("github", "142")
    assert follow_up is not None
    assert (follow_up.task_id, follow_up.continued_pr) == ("gh-142.2", 201)
    texts = bodies(adapter)
    assert any("`gh-142.2` continued the open pull request" in text for text in texts)
    assert sum("opened a pull request" in text for text in texts) == 1
