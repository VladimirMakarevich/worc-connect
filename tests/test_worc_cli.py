"""The worc surface: how the connector launches it, and how it reads what worc answers.

Three rules are pinned here. worc's status is a **display label** — a running task can read
``running (paused until …)`` or ``parked (no daemon)`` — so the connector matches its leading token
and never the whole string. An answer the connector cannot read is a failure, not a guess: the
listing decides whether a task is still running or has ended, and half of it would be worse than
none of it. And the version handshake fails **closed**: a worc that cannot be identified is treated
as one that predates the contract, because an unknown front-matter key is a hard reject at its gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import FakeWorc, listed_task
from worc_connect.core.reconcile import phase_for_status
from worc_connect.core.state import Phase
from worc_connect.core.worc_cli import ListedTask, WorcCommand, WorcUnavailable, status_token
from worc_connect.core.worc_version import MINIMUM_VERSION, supports_contract

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
        ("rejected", Phase.FAILED),
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


def test_the_listing_is_read_as_a_mapping_of_task_id_to_entry(
    clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.entries(**{"gh-1": "done", "gh-2": "running (paused)"})

    listed = WorcCommand(command="worc", repo_path=clone).list_tasks()

    assert listed == {"gh-1": ListedTask("done"), "gh-2": ListedTask("running (paused)")}
    assert fake_worc.calls == [["list", "--format", "json", "--all"]]


def test_the_entry_carries_the_pull_request_worc_recorded(clone: Path, fake_worc: FakeWorc) -> None:
    fake_worc.configure(
        entries=[
            listed_task("gh-1", "done", pr_url="https://github.com/OWNER/REPO/pull/201"),
            listed_task("gh-2", "done"),
        ]
    )

    listed = WorcCommand(command="worc", repo_path=clone).list_tasks()

    assert listed["gh-1"].pr_url == "https://github.com/OWNER/REPO/pull/201"
    assert listed["gh-2"].pr_url is None


def test_a_refused_id_is_read_with_its_reason_and_nothing_else(
    clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(
        entries=[listed_task("gh-9", "rejected", validation_reason="injection_suspected")]
    )

    entry = WorcCommand(command="worc", repo_path=clone).list_tasks()["gh-9"]

    assert entry == ListedTask("rejected", validation_reason="injection_suspected")


@pytest.mark.parametrize(
    "published",
    [
        "injection_suspected\nand a second line",
        "  injection_suspected  ",
        "injection_suspected" + "!" * 500,
    ],
)
def test_a_reason_is_bounded_before_it_can_be_repeated_on_an_item(
    clone: Path, fake_worc: FakeWorc, published: str
) -> None:
    fake_worc.configure(entries=[listed_task("gh-9", "rejected", validation_reason=published)])

    entry = WorcCommand(command="worc", repo_path=clone).list_tasks()["gh-9"]

    assert entry.validation_reason is not None
    assert "\n" not in entry.validation_reason
    assert entry.validation_reason == entry.validation_reason.strip()
    assert len(entry.validation_reason) <= 120


def test_an_older_worc_leaves_the_contract_keys_absent(clone: Path, fake_worc: FakeWorc) -> None:
    # The listing a worc from before the contract prints: no `pr_url`, no `rejected` section.
    fake_worc.configure(
        list={
            "stdout": json.dumps(
                [{"task_id": "gh-1", "status": "done", "title": None, "branch": "worc/gh-1"}]
            )
        }
    )

    assert WorcCommand(command="worc", repo_path=clone).list_tasks() == {"gh-1": ListedTask("done")}


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

    assert WorcCommand(command="worc", repo_path=clone).list_tasks() == {
        "gh-3": ListedTask("pending")
    }


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


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        (f"wastech-orchestrator {MINIMUM_VERSION}", True),
        ("wastech-orchestrator 0.14.0a2", True),
        ("wastech-orchestrator 0.14.1", True),
        ("wastech-orchestrator 1.0.0", True),
        ("wastech-orchestrator 0.14.0a1.dev7+gabc1234", False),
        ("wastech-orchestrator 0.13.0a1", False),
        ("wastech-orchestrator 0.10.3a2.dev229+g3e898df0f", False),
        ("wastech-orchestrator", False),
        ("", False),
    ],
)
def test_the_version_handshake_fails_closed(reported: str, expected: bool) -> None:
    assert supports_contract(reported) is expected


def test_the_installed_worc_is_asked_its_version_once_per_process(
    clone: Path, fake_worc: FakeWorc
) -> None:
    worc = WorcCommand(command="worc", repo_path=clone)

    assert worc.accepts_references() is True
    assert worc.accepts_references() is True
    assert fake_worc.calls == [["--version"]]


def test_a_worc_that_will_not_say_what_it_is_gets_a_task_without_the_key(
    clone: Path, fake_worc: FakeWorc
) -> None:
    fake_worc.configure(version={"stdout": "", "exit_code": 2})

    assert WorcCommand(command="worc", repo_path=clone).accepts_references() is False


def test_a_worc_that_is_not_on_path_gets_a_task_without_the_key(clone: Path) -> None:
    # The launcher failure is not raised here: the question is only which keys a task may carry,
    # and the promote that follows is what reports a worc that cannot be launched at all.
    assert (
        WorcCommand(command="nobody-installed-this", repo_path=clone).accepts_references() is False
    )
