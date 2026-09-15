"""The GitHub adapter's write side: the argument lists it builds, and what never appears in one.

Spawned against the fake ``gh`` rather than patched, for the same reason as the read side: the
things worth proving are that the tool is resolved under its real name, that every call carries the
repository pin, that a comment body travels as a file, and that the only item-derived value in any
argument list is a number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import FakeGh, work_item
from worc_connect.core.items import ItemState, PullRequestState
from worc_connect.trackers.base import TrackerUnavailable
from worc_connect.trackers.github.adapter import GitHubAdapter
from worc_connect.trackers.github.gh import GhCommand

pytestmark = pytest.mark.slow

SENTINEL = "canary-a7f3e1-do-not-leak"


def adapter(*, labels_prefix: str = "worc:") -> GitHubAdapter:
    return GitHubAdapter(
        command=GhCommand(repo="OWNER/REPO", timeout=30.0), labels_prefix=labels_prefix
    )


def test_a_state_change_is_one_edit_that_removes_and_adds(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue edit", stdout="")

    adapter().set_state("142", ItemState.IN_PROGRESS, previous=ItemState.QUEUED)

    assert fake_gh.calls_for("issue edit") == [
        [
            "issue",
            "edit",
            "142",
            "--remove-label",
            "worc:queued",
            "--add-label",
            "worc:in-progress",
            "--repo",
            "OWNER/REPO",
        ]
    ]


def test_the_first_state_removes_nothing(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue edit", stdout="")

    adapter().set_state("142", ItemState.QUEUED, previous=None)

    assert "--remove-label" not in fake_gh.calls_for("issue edit")[0]


def test_the_label_prefix_is_the_operators(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue edit", stdout="")

    adapter(labels_prefix="orchestrator/").set_state("142", ItemState.DONE, previous=None)

    assert "orchestrator/done" in fake_gh.calls_for("issue edit")[0]


def test_a_comment_body_travels_as_a_file(fake_gh: FakeGh, tmp_path: Path) -> None:
    fake_gh.respond("issue comment", stdout="")
    body = tmp_path / "comment.md"
    body.write_text("Queued as worc task `gh-142`.\n", encoding="utf-8", newline="")

    adapter().comment("142", body)

    call = fake_gh.calls_for("issue comment")[0]
    assert call[:3] == ["issue", "comment", "142"]
    assert call[3] == "--body-file"
    assert Path(call[4]) == body
    assert "Queued" not in " ".join(call)


def test_closing_carries_a_connector_written_message(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue close", stdout="")

    adapter().close("142", "Closed by the merged pull request of worc task `gh-142`.")

    call = fake_gh.calls_for("issue close")[0]
    assert call[:3] == ["issue", "close", "142"]
    assert call[3] == "--comment"
    assert "gh-142" in call[4]


def test_only_the_missing_labels_are_created_the_trigger_label_included(fake_gh: FakeGh) -> None:
    fake_gh.respond("label list", payload=[{"name": "worc:queued"}, {"name": "bug"}])
    fake_gh.respond("label create", stdout="")

    adapter().ensure_labels((ItemState.QUEUED, ItemState.DONE), triggers=("worc", "Bug"))

    created = [call[2] for call in fake_gh.calls_for("label create")]
    # `Bug` is present as `bug`: GitHub keeps label names unique case-insensitively.
    assert created == ["worc:done", "worc"]
    trigger = next(call for call in fake_gh.calls_for("label create") if call[2] == "worc")
    assert "worc" in trigger[trigger.index("--description") + 1].casefold()


def test_a_label_the_capped_listing_hid_is_not_a_failure(fake_gh: FakeGh) -> None:
    # The listing is capped, so a label can exist without being listed; `gh label create` then
    # says so, and that answer is the state the connector wanted rather than an error that would
    # abort every tick for good.
    fake_gh.respond("label list", payload=[])
    fake_gh.respond(
        "label create",
        stderr='label with name "worc:done" already exists; use `--force` to update it\n',
        exit_code=1,
    )

    adapter().ensure_labels((ItemState.DONE,), triggers=())

    assert len(fake_gh.calls_for("label create")) == 1


def test_any_other_label_failure_still_stops_the_tick(fake_gh: FakeGh) -> None:
    fake_gh.respond("label list", payload=[])
    fake_gh.respond("label create", stderr="HTTP 500: something broke\n", exit_code=1)

    with pytest.raises(TrackerUnavailable):
        adapter().ensure_labels((ItemState.DONE,), triggers=())


def test_a_pull_request_is_read_by_number(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "pr view",
        payload={
            "number": 201,
            "url": "https://github.com/OWNER/REPO/pull/201",
            "state": "MERGED",
            "mergedAt": "2026-09-11T09:00:00Z",
        },
    )

    request = adapter().get_pull_request(201)

    assert (request.number, request.state) is not None
    assert request.state is PullRequestState.MERGED
    assert request.merged_at is not None
    assert fake_gh.calls_for("pr view")[0][:3] == ["pr", "view", "201"]


def test_a_pull_request_payload_that_is_not_an_object_is_an_infrastructure_failure(
    fake_gh: FakeGh,
) -> None:
    fake_gh.respond("pr view", stdout=json.dumps([]))

    with pytest.raises(TrackerUnavailable):
        adapter().get_pull_request(201)


def test_the_item_state_is_read_off_the_items_own_labels() -> None:
    item = work_item(labels=("bug", "worc:pr-open"))

    assert adapter().current_state(item) is ItemState.PR_OPEN


def test_a_label_under_the_prefix_that_is_not_a_state_is_not_one() -> None:
    item = work_item(labels=("worc:something-else",))

    assert adapter().current_state(item) is None


def test_an_item_with_no_state_label_shows_no_state() -> None:
    assert adapter().current_state(work_item(labels=("worc",))) is None


def test_an_identifier_that_is_not_a_number_never_reaches_an_argument_list(
    fake_gh: FakeGh,
) -> None:
    with pytest.raises(TrackerUnavailable):
        adapter().set_state(f"142 {SENTINEL}", ItemState.QUEUED, previous=None)

    assert fake_gh.calls == []
