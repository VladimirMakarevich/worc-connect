"""The poll window through the whole loop, against a fake ``gh`` that filters like GitHub.

The adapter tests prove which search string a listing sends; these prove what the loop makes of the
answer when the tracker actually applies it. The fake is a small repository here rather than a
scripted reply: ``issue list`` honours ``--state`` and the ``updated:>=`` qualifier, and every label
edit, comment and close moves the issue's own update stamp, as GitHub does.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from support import FakeGh, FakeWorc, connector_config, work_item
from worc_connect.core.items import WorkItemState
from worc_connect.core.loop import WATERMARK_OVERLAP, Watcher
from worc_connect.core.state import StateStore, read_watermark, write_watermark
from worc_connect.core.worc_cli import WorcCommand
from worc_connect.home import ConnectorHome
from worc_connect.trackers.github import build_adapter

pytestmark = pytest.mark.slow

WATERMARK = datetime(2026, 9, 10, 10, 0, tzinfo=UTC)


@pytest.fixture
def store(home: ConnectorHome) -> Iterator[StateStore]:
    opened = StateStore(home.state_path)
    yield opened
    opened.close()


def watcher(home: ConnectorHome, store: StateStore) -> Watcher:
    return Watcher(
        config=connector_config(home.clone_path),
        adapter=build_adapter(repo="OWNER/REPO", labels_prefix="worc:"),
        store=store,
        home=home,
        worc=WorcCommand(command="worc", repo_path=home.clone_path),
    )


def queued(home: ConnectorHome) -> set[str]:
    pending = home.clone_path / "tasks" / "pending"
    return {path.stem for path in pending.glob("*.md")} if pending.is_dir() else set()


def test_the_tracker_s_own_filter_decides_what_the_loop_takes_on(
    home: ConnectorHome, store: StateStore, fake_gh: FakeGh, fake_worc: FakeWorc
) -> None:
    fake_gh.respond("label list", payload=[])
    fake_gh.respond("label create", stdout="")
    fake_gh.issues(
        # Inside the window: updated just after the watermark.
        work_item("142", updated_at=WATERMARK + timedelta(minutes=1)),
        # Inside the overlap only: updated before the watermark, but by less than the overlap.
        work_item("143", updated_at=WATERMARK - WATERMARK_OVERLAP / 2),
        # Behind the window: gated, open, and last touched long before the watermark.
        work_item("144", updated_at=WATERMARK - timedelta(days=1)),
        # Closed: the listing is pinned to open issues.
        work_item("145", updated_at=WATERMARK + timedelta(minutes=2), state=WorkItemState.CLOSED),
    )
    write_watermark(store, WATERMARK)

    report = watcher(home, store).tick(dry_run=False)

    assert report.listed == 2
    assert queued(home) == {"gh-142", "gh-143"}
    assert {row.item_id for row in store.rows()} == {"142", "143"}
    assert read_watermark(store) == WATERMARK + timedelta(minutes=1)

    # The next tick: the connector's own label edits and comments moved both issues past the
    # watermark, so the tracker lists them again — and the overlap makes that idempotent.
    fake_worc.entries(**{"gh-142": "running", "gh-143": "running"})
    again = watcher(home, store).tick(dry_run=False)

    assert again.listed == 2
    assert queued(home) == {"gh-142", "gh-143"}
    assert len(store.rows()) == 2
    edits = [call for call in fake_gh.calls_for("issue edit") if "worc:in-progress" in call]
    assert sorted(call[2] for call in edits) == ["142", "143"]
