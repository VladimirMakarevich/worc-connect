"""The GitHub adapter against a fake ``gh``: the argument lists it builds and the failures it maps.

Every test here spawns the fake executable rather than patching ``subprocess``, because the things
worth proving are exactly the ones a patch would paper over: that the tool is resolved on ``PATH``
under its real name, that the repository pin is on every call, and that no item text ever reaches an
argument list.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support import FakeGh, issue_payload, work_item
from worc_connect.core.items import PullRequestState, WorkItemState
from worc_connect.trackers.base import TrackerAuth, TrackerRateLimited, TrackerUnavailable
from worc_connect.trackers.github import build_adapter
from worc_connect.trackers.github.adapter import GitHubAdapter
from worc_connect.trackers.github.gh import GhCommand

pytestmark = pytest.mark.slow

SINCE = datetime(2026, 9, 10, 10, 0, tzinfo=UTC)


def adapter(*, repo: str = "OWNER/REPO", labels_prefix: str = "worc:") -> GitHubAdapter:
    return GitHubAdapter(command=GhCommand(repo=repo, timeout=30.0), labels_prefix=labels_prefix)


def test_the_fake_is_resolved_on_path_under_the_real_name(fake_gh: FakeGh) -> None:
    resolved = shutil.which("gh")

    assert resolved is not None
    assert Path(resolved).parent == fake_gh.bin_dir


def test_listing_pins_the_repository_and_orders_by_oldest_update(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", payload=[issue_payload(work_item())])

    items = adapter().list_items(None)

    assert [item.identifier for item in items] == ["142"]
    argv = fake_gh.calls_for("issue list")[0]
    assert argv[:2] == ["issue", "list"]
    assert argv[argv.index("--repo") + 1] == "OWNER/REPO"
    assert argv[argv.index("--state") + 1] == "open"
    assert argv[argv.index("--search") + 1] == "sort:updated-asc"
    assert "--limit" in argv


def test_a_watermark_becomes_a_search_qualifier(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", payload=[])

    adapter().list_items(SINCE)

    argv = fake_gh.calls_for("issue list")[0]
    assert argv[argv.index("--search") + 1] == "updated:>=2026-09-10T10:00:00Z sort:updated-asc"


def test_a_naive_local_watermark_is_sent_as_utc(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", payload=[])

    adapter().list_items(SINCE.astimezone(UTC))

    argv = fake_gh.calls_for("issue list")[0]
    assert argv[argv.index("--search") + 1].startswith("updated:>=2026-09-10T10:00:00Z")


def test_an_issue_payload_becomes_a_normalized_item(fake_gh: FakeGh) -> None:
    source = work_item(labels=("bug", "worc"), author="reporter")
    fake_gh.respond("issue list", payload=[issue_payload(source)])

    item = adapter().list_items(None)[0]

    assert item.identifier == "142"
    assert item.title == source.title
    assert item.body == source.body
    assert item.author == "reporter"
    assert item.labels == ("bug", "worc")
    assert item.state is WorkItemState.OPEN
    assert item.updated_at == source.updated_at
    assert item.url.endswith("/issues/142")


def test_an_empty_body_and_a_deleted_author_are_ordinary(fake_gh: FakeGh) -> None:
    payload = issue_payload(work_item())
    payload["body"] = None
    payload["author"] = {}
    fake_gh.respond("issue list", payload=[payload])

    item = adapter().list_items(None)[0]

    assert item.body == ""
    assert item.author == ""


def test_a_label_without_a_name_is_dropped(fake_gh: FakeGh) -> None:
    payload = issue_payload(work_item(labels=("worc",)))
    payload["labels"] = [{"name": "worc"}, {"colour": "red"}, "worc"]
    fake_gh.respond("issue list", payload=[payload])

    assert adapter().list_items(None)[0].labels == ("worc",)


def test_an_issue_without_a_number_stops_the_tick(fake_gh: FakeGh) -> None:
    payload = issue_payload(work_item())
    del payload["number"]
    fake_gh.respond("issue list", payload=[payload])

    with pytest.raises(TrackerUnavailable, match="number"):
        adapter().list_items(None)


def test_an_issue_without_a_timestamp_stops_the_tick(fake_gh: FakeGh) -> None:
    payload = issue_payload(work_item())
    payload["updatedAt"] = None
    fake_gh.respond("issue list", payload=[payload])

    with pytest.raises(TrackerUnavailable, match="updatedAt"):
        adapter().list_items(None)


def test_output_that_is_not_json_stops_the_tick(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", stdout="not json at all")

    with pytest.raises(TrackerUnavailable, match="not JSON"):
        adapter().list_items(None)


def test_output_that_is_not_a_list_of_objects_stops_the_tick(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", payload={"items": []})

    with pytest.raises(TrackerUnavailable, match="list of objects"):
        adapter().list_items(None)


def test_reading_one_item_asks_for_its_state_too(fake_gh: FakeGh) -> None:
    payload = issue_payload(work_item())
    payload["state"] = "CLOSED"
    fake_gh.respond("issue view", payload=payload)

    item = adapter().get_item("142")

    assert item.state is WorkItemState.CLOSED
    argv = fake_gh.calls_for("issue view")[0]
    assert argv[:3] == ["issue", "view", "142"]
    assert "state" in argv[argv.index("--json") + 1]


@pytest.mark.parametrize("identifier", ["../etc/passwd", "-142", "142; rm -rf /", ""])
def test_an_identifier_that_is_not_a_number_never_reaches_an_argument_list(
    fake_gh: FakeGh, identifier: str
) -> None:
    with pytest.raises(TrackerUnavailable, match="issue number"):
        adapter().get_item(identifier)

    assert fake_gh.calls == []


def test_a_pull_request_is_searched_by_head_branch_in_any_state(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "pr list",
        payload=[
            {
                "number": 201,
                "url": "https://github.com/OWNER/REPO/pull/201",
                "state": "MERGED",
                "mergedAt": "2026-09-11T09:00:00Z",
            }
        ],
    )

    found = adapter().find_pull_request("worc/gh-142-signup")

    assert found is not None
    assert found.number == 201
    assert found.state is PullRequestState.MERGED
    assert found.merged_at == datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    argv = fake_gh.calls_for("pr list")[0]
    assert argv[argv.index("--head") + 1] == "worc/gh-142-signup"
    assert argv[argv.index("--state") + 1] == "all"


def test_no_pull_request_on_the_branch_reads_as_none(fake_gh: FakeGh) -> None:
    fake_gh.respond("pr list", payload=[])

    assert adapter().find_pull_request("worc/gh-142-signup") is None


def test_an_open_request_wins_over_a_closed_one_on_the_same_branch(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "pr list",
        payload=[
            {"number": 200, "url": "u200", "state": "CLOSED", "mergedAt": None},
            {"number": 201, "url": "u201", "state": "OPEN", "mergedAt": None},
        ],
    )

    found = adapter().find_pull_request("worc/gh-142-signup")

    assert found is not None
    assert found.number == 201
    assert found.merged_at is None


def test_an_unknown_pull_request_state_reads_as_closed(fake_gh: FakeGh) -> None:
    fake_gh.respond("pr list", payload=[{"number": 1, "url": "u", "state": "DRAFTED"}])

    found = adapter().find_pull_request("branch")

    assert found is not None
    assert found.state is PullRequestState.CLOSED


def test_the_url_worc_recorded_is_read_by_number_and_the_branch_is_never_searched(
    fake_gh: FakeGh,
) -> None:
    fake_gh.respond(
        "pr view",
        payload={
            "number": 201,
            "url": "https://github.com/OWNER/REPO/pull/201",
            "state": "MERGED",
            "mergedAt": "2026-09-11T09:00:00Z",
        },
    )

    found = adapter().find_pull_request(
        "worc/gh-142-signup", url="https://github.com/OWNER/REPO/pull/201"
    )

    assert found is not None and found.number == 201
    assert fake_gh.calls_for("pr list") == []
    # The URL itself never reaches the argument list: the number is read out of it first.
    argv = fake_gh.calls_for("pr view")[0]
    assert "201" in argv
    assert not any(argument.startswith("http") for argument in argv)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/OWNER/REPO/issues/201",
        "not a url",
        "https://github.com/OWNER/REPO/pull/",
    ],
)
def test_a_url_this_adapter_does_not_recognise_falls_back_to_the_branch(
    fake_gh: FakeGh, url: str
) -> None:
    fake_gh.respond("pr list", payload=[])

    assert adapter().find_pull_request("worc/gh-142-signup", url=url) is None
    assert fake_gh.calls_for("pr list")


def test_the_closing_reference_names_the_issue_and_nothing_the_item_wrote() -> None:
    item = work_item(title="; rm -rf / `whoami`", body="$(id)")

    assert adapter().closing_reference(item) == "Fixes #142"


def test_a_closing_reference_is_refused_for_an_identifier_that_is_not_a_number() -> None:
    with pytest.raises(TrackerUnavailable):
        adapter().closing_reference(work_item("AB-7"))


def test_an_authentication_failure_is_reported_as_such(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "issue list",
        stderr="gh: To get started with GitHub CLI, please run: gh auth login\n",
        exit_code=4,
    )

    with pytest.raises(TrackerAuth, match="gh auth login"):
        adapter().list_items(None)


def test_an_authentication_failure_is_recognised_by_exit_code_alone(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", stderr="something the release reworded\n", exit_code=4)

    with pytest.raises(TrackerAuth):
        adapter().list_items(None)


def test_a_rate_limit_is_not_mistaken_for_a_login_problem(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "issue list",
        stderr=(
            "gh: API rate limit exceeded for 203.0.113.7. "
            "(Authenticated requests get a higher rate limit.)\n"
        ),
        exit_code=1,
    )

    with pytest.raises(TrackerRateLimited, match="rate limit"):
        adapter().list_items(None)


def test_any_other_failure_is_an_unavailable_tracker(fake_gh: FakeGh) -> None:
    fake_gh.respond(
        "issue list", stderr="GraphQL: Could not resolve to a Repository\n", exit_code=1
    )

    with pytest.raises(TrackerUnavailable, match="Could not resolve"):
        adapter().list_items(None)


def test_a_failure_with_no_diagnostic_still_names_the_call(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", exit_code=7)

    with pytest.raises(TrackerUnavailable, match="issue list` failed with exit code 7"):
        adapter().list_items(None)


def test_a_missing_gh_is_an_unavailable_tracker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(TrackerUnavailable, match="was not found on PATH"):
        adapter().list_items(None)


def test_the_entry_point_factory_builds_a_pinned_adapter(fake_gh: FakeGh) -> None:
    fake_gh.respond("issue list", payload=[])

    build_adapter(repo="OTHER/THING", labels_prefix="worc:").list_items(None)

    argv = fake_gh.calls_for("issue list")[0]
    assert argv[argv.index("--repo") + 1] == "OTHER/THING"
