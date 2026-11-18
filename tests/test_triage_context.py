"""What a triage task is told besides the item's own text: the reporter's answer, the other items.

Two findings of the second real run. A `needs-info` reply re-ran triage with the issue's original
body only — the answer never reached the agent (F-6). And a duplicate of an issue still open came
back `actionable`, because the triage run could not see any other issue (F-5). Both are text from
the tracker, as untrusted as the item's own, and both go where the item's text goes: into the body
of the triage task, and nowhere else.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

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
from worc_connect.core.items import Reply
from worc_connect.core.loop import Watcher
from worc_connect.core.state import Phase, StateStore
from worc_connect.core.triage import Verdict
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome

pytestmark = pytest.mark.slow


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(
    home: ConnectorHome, adapter: StubAdapter, store: StateStore, **config: object
) -> Watcher:
    config.setdefault("research", ResearchMode.WORC)
    return Watcher(
        config=connector_config(home.clone_path, **config),
        adapter=adapter,
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
    )


def pending(home: ConnectorHome, task_id: str) -> str:
    return (home.clone_path / "tasks" / "pending" / f"{task_id}.md").read_text(encoding="utf-8")


def asked(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc, adapter: StubAdapter
) -> tuple[Watcher, datetime]:
    """Run the item to a `needs-info` verdict; returns the loop and the question's stamp."""
    loop = watcher(home, adapter, store)
    loop.tick(dry_run=False)
    write_report(home, "gh-142", report_text(Verdict.NEEDS_INFO, reason="Which version?"))
    fake_worc.entries(**{"gh-142": "done"})
    loop.tick(dry_run=False)
    row = store.latest_row("github", "142")
    assert row is not None
    assert row.phase is Phase.NEEDS_INFO
    assert row.item_updated_at is not None
    return loop, row.item_updated_at


# --- F-6: the answer reaches the next triage run -----------------------------------------------


def test_the_reporters_answer_reaches_the_next_triage_task(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item(body="some findings look wrong")])
    loop, question = asked(home, store, fake_worc, adapter)

    # The connector's own question is a comment too, posted under the operator's login — and older
    # than the stamp it left on the item. The answer comes after.
    adapter.posted = [
        Reply(author="operator", created_at=question, body="Triage needs more information"),
        Reply(
            author="reporter",
            created_at=question + timedelta(minutes=5),
            body="2.3.1 on Linux; the textSpans exclusion drops a finding.",
        ),
    ]
    later = question + timedelta(minutes=5)
    adapter.items = [
        work_item(
            body="some findings look wrong",
            labels=("worc", "worc:needs-info"),
            updated_at=later,
        )
    ]
    loop.tick(dry_run=False)

    task = pending(home, "gh-142.2")
    assert "some findings look wrong" in task
    assert "the textSpans exclusion drops a finding" in task
    assert "Triage needs more information" not in task
    assert adapter.replies_since == [("142", question)]


def test_a_first_triage_run_reads_no_replies(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])
    adapter.posted = [
        Reply(author="someone", created_at=datetime(2026, 9, 10, 11, tzinfo=UTC), body="+1")
    ]

    watcher(home, adapter, store).tick(dry_run=False)

    assert adapter.replies_since == []
    assert "+1" not in pending(home, "gh-142")


def test_an_answer_started_implementation_task_carries_the_answer_too(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    # The triage step switched off between the question and the answer: the answer still starts the
    # next attempt, and the task it starts is the one worth telling.
    adapter = StubAdapter(items=[work_item()])
    _, question = asked(home, store, fake_worc, adapter)
    adapter.posted = [
        Reply(author="reporter", created_at=question + timedelta(minutes=1), body="It is 2.3.1")
    ]
    adapter.items = [
        work_item(labels=("worc", "worc:needs-info"), updated_at=question + timedelta(minutes=1))
    ]

    watcher(home, adapter, store, research=ResearchMode.OFF).tick(dry_run=False)

    task = pending(home, "gh-142.2")
    assert "task_type: issue_triage" not in task
    assert "It is 2.3.1" in task


# --- F-5: the other open items, so a duplicate is recognised ---------------------------------


def test_the_triage_task_lists_the_other_open_items(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(
        items=[
            work_item("32", title="REF-007: CRLF fixture for line/column", labels=()),
            work_item(title="A duplicate of the CRLF fixture"),
        ]
    )

    watcher(home, adapter, store).tick(dry_run=False)

    task = pending(home, "gh-142")
    assert "- #32 REF-007: CRLF fixture for line/column" in task
    assert "- #142 " not in task


def test_the_open_items_are_listed_once_per_tick_whatever_is_gated(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item("7"), work_item("8"), work_item("9")])

    watcher(home, adapter, store).tick(dry_run=False)

    assert adapter.open_listed == 1
    assert "- #8 " in pending(home, "gh-7")


def test_without_the_triage_step_nothing_is_listed(
    home: ConnectorHome, store: StateStore, fake_worc: FakeWorc
) -> None:
    adapter = StubAdapter(items=[work_item()])

    watcher(home, adapter, store, research=ResearchMode.OFF).tick(dry_run=False)

    assert adapter.open_listed == 0
    assert "## Other open issues" not in pending(home, "gh-142")
