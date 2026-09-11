"""The worc surface: how the connector launches it, and how it reads the one listing it may read.

Two rules are pinned here. The first is that worc's status is a **display label** — a running task
can read ``running (paused until …)`` or ``parked (no daemon)`` — so the connector matches its
leading token and never the whole string. The second is that an answer the connector cannot read
is a failure, not a guess: the listing decides whether a task is still running or has ended, and
half of it would be worse than none of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import FakeWorc
from worc_connect.core.reconcile import phase_for_status
from worc_connect.core.state import Phase
from worc_connect.core.worc_cli import WorcCommand, WorcUnavailable, status_token

pytestmark = pytest.mark.slow


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("new", Phase.QUEUED),
        ("validated", Phase.QUEUED),
        ("preparing", Phase.QUEUED),
        ("pending", Phase.QUEUED),
        ("running", Phase.RUNNING),
        ("running (paused)", Phase.RUNNING),
        ("running (paused until 2026-09-11T12:00:00Z)", Phase.RUNNING),
        ("parked (no daemon)", Phase.RUNNING),
        ("done", Phase.DONE),
        ("failed", Phase.FAILED),
        ("manual_action_required", Phase.FAILED),
    ],
)
def test_worcs_display_status_maps_to_a_connector_phase(status: str, expected: Phase) -> None:
    assert phase_for_status(status) is expected


def test_a_status_this_build_does_not_know_is_not_guessed_at() -> None:
    assert phase_for_status("hibernating") is None
    assert phase_for_status("") is None


def test_the_leading_token_is_what_a_status_is_matched_on() -> None:
    assert status_token("running (paused)") == "running"
    assert status_token("  ") == ""


def test_the_listing_is_read_as_a_mapping_of_task_id_to_status(
    clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.entries(**{"gh-1": "done", "gh-2": "running (paused)"})

    statuses = WorcCommand(command="worc", repo_path=clone).list_tasks()

    assert statuses == {"gh-1": "done", "gh-2": "running (paused)"}
    assert fake_worc.calls == [["list", "--format", "json", "--all"]]


def test_a_pending_entry_without_a_database_row_still_counts(
    clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(
        list={
            "stdout": json.dumps(
                [{"task_id": "gh-3", "status": "pending", "file": "gh-3.md", "rank": "1"}]
            )
        }
    )

    assert WorcCommand(command="worc", repo_path=clone).list_tasks() == {"gh-3": "pending"}


def test_output_that_is_not_json_stops_the_tick(clone: Path, fake_worc: FakeWorc) -> None:
    fake_worc.configure(list={"stdout": "list: no tasks\n"})

    with pytest.raises(WorcUnavailable):
        WorcCommand(command="worc", repo_path=clone).list_tasks()


def test_json_that_is_not_a_list_of_entries_stops_the_tick(
    clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(list={"stdout": json.dumps({"gh-1": "done"})})

    with pytest.raises(WorcUnavailable):
        WorcCommand(command="worc", repo_path=clone).list_tasks()


def test_a_listing_worc_refused_to_produce_stops_the_tick(clone: Path, fake_worc: FakeWorc) -> None:
    fake_worc.configure(list={"stdout": "[]", "exit_code": 2})

    with pytest.raises(WorcUnavailable):
        WorcCommand(command="worc", repo_path=clone).list_tasks()


def test_a_worc_that_is_not_on_path_names_the_key_to_fix(clone: Path) -> None:
    with pytest.raises(WorcUnavailable, match=r"worc\.command"):
        WorcCommand(command="worc-that-nobody-installed", repo_path=clone).list_tasks()
