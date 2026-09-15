"""What the connector puts back on the item: one state at a time, and comments that say why.

The assertions here are about restraint as much as about action. A state that is already shown is
not shown again, a quiet tick says nothing, and every body is checked for the one thing it must
never carry: a word the item's author wrote.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from support import StubAdapter, connector_config, work_item
from worc_connect.core.state import ItemRow, Phase
from worc_connect.core.writeback import PHASE_STATES, STATE_PHASES, Published, WriteBack

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
SENTINEL = "canary-a7f3e1-do-not-leak"


def row(
    phase: Phase,
    *,
    task_id: str = "gh-142",
    pr_url: str | None = None,
    pr_merged: bool = False,
    last_status: str | None = None,
    validation_reason: str | None = None,
) -> ItemRow:
    return ItemRow(
        tracker="github",
        item_id="142",
        seq=1,
        phase=phase,
        created_at=NOW,
        updated_at=NOW,
        task_id=task_id,
        branch="worc/gh-142-slug",
        pr_number=201 if pr_url else None,
        pr_url=pr_url,
        pr_merged=pr_merged,
        last_status=last_status,
        validation_reason=validation_reason,
    )


def write_back(clone: Path, adapter: StubAdapter, **config: object) -> WriteBack:
    return WriteBack(config=connector_config(clone, **config), adapter=adapter)


def test_every_visible_phase_maps_to_one_state_and_back(clone: Path) -> None:
    assert set(PHASE_STATES) == {
        Phase.QUEUED,
        Phase.RUNNING,
        Phase.PR_OPEN,
        Phase.DONE,
        Phase.FAILED,
        Phase.NEEDS_INFO,
        Phase.DECLINED,
    }
    reversed_mapping = {state: phase for phase, state in PHASE_STATES.items()}
    assert reversed_mapping == STATE_PHASES


def test_a_triage_task_whose_report_was_actionable_shows_the_item_nothing() -> None:
    # What the item should show then is the implementation task's own state; "analysed" is a step
    # nobody can act on, and a label for it would be one more notification for nothing.
    assert Phase.RESEARCHED not in PHASE_STATES


@pytest.mark.parametrize("phase", [Phase.GATED, Phase.STAGED])
def test_a_phase_the_operator_has_no_business_seeing_shows_nothing(
    clone: Path, phase: Phase
) -> None:
    adapter = StubAdapter(items=[work_item()])

    write_back(clone, adapter).publish(row(phase), adapter.items[0])

    assert adapter.states == []
    assert adapter.comments == []


def test_a_state_change_removes_the_previous_label_and_adds_the_new_one(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:queued"))])
    publisher = write_back(clone, adapter)

    publisher.publish(row(Phase.RUNNING), adapter.items[0])

    assert adapter.states == [("142", "in-progress", "queued")]


def test_re_applying_the_state_the_item_already_shows_does_nothing(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])
    publisher = write_back(clone, adapter)

    publisher.publish(row(Phase.RUNNING), adapter.items[0])
    publisher.publish(row(Phase.RUNNING), adapter.items[0])

    assert adapter.states == []
    assert adapter.comments == []


def test_the_state_labels_are_created_once_before_the_first_one_is_used(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item()])
    publisher = write_back(clone, adapter)

    publisher.publish(row(Phase.QUEUED), adapter.items[0])
    publisher.publish(row(Phase.RUNNING), adapter.items[0])

    assert adapter.ensured == [tuple(PHASE_STATES.values())]


def test_queueing_comments_with_the_task_id(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item()])

    write_back(clone, adapter).publish(row(Phase.QUEUED), adapter.items[0])

    identifier, body = adapter.comments[0]
    assert identifier == "142"
    assert "gh-142" in body


def test_an_open_pull_request_comments_with_its_url(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])
    url = "https://github.com/OWNER/REPO/pull/201"

    write_back(clone, adapter).publish(row(Phase.PR_OPEN, pr_url=url), adapter.items[0])

    assert url in adapter.comments[0][1]


def test_a_task_that_ended_without_a_pull_request_points_at_worc_on_the_host(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])

    write_back(clone, adapter).publish(
        row(Phase.FAILED, last_status="manual_action_required"), adapter.items[0]
    )

    body = adapter.comments[0][1]
    assert "manual_action_required" in body
    assert "worc status gh-142" in body


def test_a_failure_comment_never_repeats_worcs_own_output(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:queued"))])

    write_back(clone, adapter).publish(row(Phase.FAILED), adapter.items[0])

    body = adapter.comments[0][1]
    assert "gh-142" in body
    assert "worc status gh-142" in body
    assert "Traceback" not in body and "diff" not in body


def test_a_refused_task_names_the_reason_worc_published_and_nothing_else(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:queued"))])

    write_back(clone, adapter).publish(
        row(Phase.FAILED, last_status="rejected", validation_reason="injection_suspected"),
        adapter.items[0],
    )

    body = adapter.comments[0][1]
    assert "gh-142" in body
    assert "injection_suspected" in body
    # The entry carries more than the reason; none of the rest is the connector's to republish.
    assert "rejected_at" not in body and "2026-" not in body
    assert ".worc/" not in body


def test_a_refusal_worc_published_no_reason_for_reads_as_it_did_before_the_contract(
    clone: Path,
) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:queued"))])

    write_back(clone, adapter).publish(row(Phase.FAILED, last_status="failed"), adapter.items[0])

    body = adapter.comments[0][1]
    assert "worc status gh-142" in body
    assert "validation" not in body


def test_a_merged_pull_request_closes_the_item_with_a_message(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])
    url = "https://github.com/OWNER/REPO/pull/201"

    write_back(clone, adapter).publish(
        row(Phase.DONE, pr_url=url, pr_merged=True), adapter.items[0]
    )

    assert adapter.states == [("142", "done", "pr-open")]
    identifier, message = adapter.closed[0]
    assert identifier == "142"
    assert "gh-142" in message and url in message
    assert adapter.comments == []  # the closing message is the comment


def test_close_on_merge_off_still_labels_the_item_done(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])

    write_back(clone, adapter, close_on_merge=False).publish(
        row(Phase.DONE, pr_url="https://example.test/pull/201", pr_merged=True), adapter.items[0]
    )

    assert adapter.states == [("142", "done", "pr-open")]
    assert adapter.closed == []


def test_a_task_that_finished_without_a_merge_is_never_closed(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:in-progress"))])

    write_back(clone, adapter).publish(row(Phase.DONE), adapter.items[0])

    assert adapter.states == [("142", "done", "in-progress")]
    assert adapter.closed == []


def test_comments_can_be_switched_off_without_switching_off_the_labels(clone: Path) -> None:
    adapter = StubAdapter(items=[work_item()])

    write_back(clone, adapter, comment=False).publish(row(Phase.QUEUED), adapter.items[0])

    assert adapter.states == [("142", "queued", None)]
    assert adapter.comments == []


def test_no_comment_body_can_carry_a_word_the_item_wrote(clone: Path) -> None:
    item = work_item(title=f"{SENTINEL} title", body=f"{SENTINEL} body")
    adapter = StubAdapter(items=[item])
    publisher = write_back(clone, adapter)

    for phase in (Phase.QUEUED, Phase.RUNNING, Phase.PR_OPEN, Phase.FAILED):
        publisher.publish(
            row(phase, pr_url="https://example.test/pull/201", last_status="failed"),
            adapter.items[0],
        )

    assert adapter.comments
    for _, body in adapter.comments:
        assert SENTINEL not in body


def test_publish_says_what_it_did_to_the_item(clone: Path) -> None:
    # The loop acts on the answer: a close arms the re-trigger, and a state actually written is
    # what moved the item's update stamp — which a row that just asked a question is measured from.
    adapter = StubAdapter(items=[work_item(labels=("worc", "worc:pr-open"))])
    publisher = write_back(clone, adapter)
    merged = row(Phase.DONE, pr_url="https://example.test/pull/201", pr_merged=True)

    assert publisher.publish(row(Phase.STAGED), adapter.items[0]) is Published.NOTHING
    assert publisher.publish(row(Phase.PR_OPEN), adapter.items[0]) is Published.NOTHING
    assert publisher.publish(row(Phase.FAILED), adapter.items[0]) is Published.SHOWN
    assert publisher.publish(merged, adapter.items[0]) is Published.CLOSED
