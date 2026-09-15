"""The command surface: ``init``, ``watch`` and ``status``, including the first safe command.

The dry run is the one an operator points at a real repository before anything else, so its promise
— stdout describes the plan, the filesystem is untouched, and no tracker call with a side effect is
made — is asserted against the recording of the fake ``gh`` rather than inferred.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from support import (
    FakeGh,
    FakeWorc,
    base_config,
    issue_payload,
    requires_installed_distribution,
    work_item,
    write_config,
)
from worc_connect import flows
from worc_connect.cli import main
from worc_connect.core.state import StateStore
from worc_connect.home import ConnectorHome

SIDE_EFFECT_VERBS = ("issue edit", "issue comment", "issue close", "label create", "pr create")


def test_init_writes_a_configuration_and_keeps_the_home_out_of_git(
    home: ConnectorHome, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["init", "--home", str(home.path), "--repo", "OWNER/REPO"])

    assert code == 0
    assert "repo: OWNER/REPO" in home.config_path.read_text(encoding="utf-8")
    gitignore = (home.clone_path / ".gitignore").read_text(encoding="utf-8")
    assert ".worc-connect/" in gitignore.splitlines()
    out = capsys.readouterr().out
    assert "watch --once --dry-run" in out


def test_init_uses_the_home_in_the_current_directory_by_default(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(clone)

    assert main(["init", "--repo", "OWNER/REPO"]) == 0
    assert (clone / ".worc-connect" / "config.yaml").is_file()


def test_init_refuses_to_overwrite_a_configuration(home: ConnectorHome) -> None:
    write_config(home, base_config())
    before = home.config_path.read_text(encoding="utf-8")

    code = main(["init", "--home", str(home.path), "--repo", "OWNER/REPO"])

    assert code == 1
    assert home.config_path.read_text(encoding="utf-8") == before


def test_init_refuses_a_repository_it_could_not_pin(home: ConnectorHome) -> None:
    code = main(["init", "--home", str(home.path), "--repo", "not-a-repo"])

    assert code == 2
    assert not home.config_path.exists()


def test_init_leaves_an_existing_gitignore_intact_and_adds_the_line_once(
    home: ConnectorHome,
) -> None:
    gitignore = home.clone_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    main(["init", "--home", str(home.path), "--repo", "OWNER/REPO"])
    main(["init", "--home", str(home.clone_path / "second-home"), "--repo", "OWNER/REPO"])

    lines = gitignore.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "build/"
    assert lines.count(".worc-connect/") == 1


def test_watch_without_a_configuration_points_at_init(
    home: ConnectorHome, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["watch", "--home", str(home.path), "--once"])

    assert code == 2
    assert "worc-connect init" in capsys.readouterr().err


def test_watch_with_an_unparseable_configuration_stops_before_any_tracker_call(
    home: ConnectorHome, fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home, "gate: [unclosed\n")

    code = main(["watch", "--home", str(home.path), "--once"])

    assert code == 2
    assert "not valid YAML" in capsys.readouterr().err
    assert fake_gh.calls == []


@requires_installed_distribution
def test_a_tracker_with_no_adapter_installed_names_the_extra(
    home: ConnectorHome, capsys: pytest.CaptureFixture[str]
) -> None:
    document = base_config()
    document["tracker"] = "gitlab"
    write_config(home, document)

    code = main(["watch", "--home", str(home.path), "--once"])

    assert code == 2
    error = capsys.readouterr().err
    assert 'pip install "worc-connect[gitlab]"' in error
    assert "github" in error


@pytest.mark.slow
@requires_installed_distribution
def test_a_dry_run_prints_the_plan_and_writes_nothing(
    home: ConnectorHome, fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home, base_config())
    fake_gh.respond(
        "issue list",
        payload=[
            issue_payload(work_item("142")),
            issue_payload(work_item("143", labels=("worc",))),
            issue_payload(work_item("144", labels=("question",))),
        ],
    )

    code = main(["watch", "--home", str(home.path), "--once", "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert "dry run: nothing is written" in out
    assert "item=142 action=stage-task" in out
    assert "item=143 action=stage-task" in out
    assert "item=144 action=skip reason=no-trigger-label" in out
    assert not home.state_path.exists()
    assert not home.log_path.exists()
    assert not home.pid_path.exists()
    assert [call[:2] for call in fake_gh.calls] == [["issue", "list"]]
    for verb in SIDE_EFFECT_VERBS:
        assert fake_gh.calls_for(verb) == []


@pytest.mark.slow
@requires_installed_distribution
def test_a_dry_run_repeats_a_stranger_s_text_nowhere(
    home: ConnectorHome, fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = "SENTINEL-c0ffee"
    write_config(home, base_config())
    fake_gh.respond(
        "issue list",
        payload=[issue_payload(work_item("142", title=sentinel, body=f"body {sentinel}"))],
    )

    main(["watch", "--home", str(home.path), "--once", "--dry-run"])

    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    assert all(sentinel not in " ".join(call) for call in fake_gh.calls)


@pytest.mark.slow
@requires_installed_distribution
def test_a_single_pass_records_the_item_and_its_own_log(
    home: ConnectorHome, fake_gh: FakeGh, fake_worc: FakeWorc
) -> None:
    write_config(home, base_config())
    fake_gh.respond("issue list", payload=[issue_payload(work_item("142"))])
    fake_gh.respond("label list", payload=[])
    for verb in ("label create", "issue edit", "issue comment"):
        fake_gh.respond(verb, stdout="")

    code = main(["watch", "--home", str(home.path), "--once"])

    assert code == 0
    assert home.state_path.is_file()
    assert "action=stage-task" in home.log_path.read_text(encoding="utf-8")
    store = StateStore.read_only(home.state_path)
    assert [row.item_id for row in store.rows()] == ["142"]
    store.close()
    # The first tick of a process creates the labels — the trigger label the gate is configured
    # with included, since nothing can be gated on a fresh repository until it exists.
    created = [call[2] for call in fake_gh.calls_for("label create")]
    assert "worc" in created and "worc:queued" in created
    assert "worc:needs-info" not in created  # triage is off in this configuration
    # The log is bounded: rotated by size rather than appended forever.
    handlers = logging.getLogger().handlers
    rotating = [handler for handler in handlers if isinstance(handler, RotatingFileHandler)]
    assert len(rotating) == 1
    assert rotating[0].maxBytes > 0 and rotating[0].backupCount > 0


@pytest.mark.slow
@requires_installed_distribution
def test_a_single_pass_reports_an_unreachable_tracker(home: ConnectorHome, fake_gh: FakeGh) -> None:
    write_config(home, base_config())
    fake_gh.respond("issue list", stderr="gh: not logged in\n", exit_code=4)

    assert main(["watch", "--home", str(home.path), "--once"]) == 1


def test_status_reports_the_rows_the_watermark_and_the_last_tick(
    home: ConnectorHome, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home, base_config())
    store = StateStore(home.state_path)
    store.set_meta("watermark", "2026-09-10T10:00:00+00:00")
    store.set_meta("last_tick_at", "2026-09-11T12:00:00+00:00")
    store.set_meta("last_tick_result", "ok listed=1 staged=1")
    store.close()

    code = main(["status", "--home", str(home.path)])

    assert code == 0
    out = capsys.readouterr().out
    assert "tracker=github repo=OWNER/REPO" in out
    assert "watermark=2026-09-10T10:00:00+00:00" in out
    assert "result=ok listed=1 staged=1" in out
    assert "no items taken on yet" in out


def test_status_creates_no_database(home: ConnectorHome) -> None:
    write_config(home, base_config())

    assert main(["status", "--home", str(home.path)]) == 0
    assert not home.state_path.exists()


def test_status_without_a_configuration_is_a_configuration_error(home: ConnectorHome) -> None:
    assert main(["status", "--home", str(home.path)]) == 2


# --- install-flow: the one write the connector makes inside worc's own home --------------------


def install_config(home: ConnectorHome, **research: object) -> None:
    """A configuration with the analysis step set as a test wants it."""
    document = base_config()
    document["research"] = {"mode": "worc", "flow": "issue_triage", **research}
    write_config(home, document)


def flows_dir(home: ConnectorHome) -> Path:
    return home.clone_path / ".worc" / "flows"


def test_install_flow_delivers_the_flow_and_every_prompt_it_names(
    home: ConnectorHome, fake_worc: FakeWorc, capsys: pytest.CaptureFixture[str]
) -> None:
    install_config(home)

    code = main(["install-flow", "--home", str(home.path)])

    assert code == 0
    target = flows_dir(home)
    assert (target / "issue_triage.yaml").is_file()
    assert {path.name for path in (target / "issue_triage").glob("*.md")} == {
        "scope.md",
        "analysis.md",
        "reproduction.md",
        "verifier.md",
        "report.md",
    }
    assert "written" in capsys.readouterr().out


def test_install_flow_ships_the_bytes_this_package_carries(
    home: ConnectorHome, fake_worc: FakeWorc
) -> None:
    # worc fingerprints a flow by hashing it, so an installed copy that is not byte-identical to
    # the shipped one is a different flow than the one this repository's tests judged.
    install_config(home)

    main(["install-flow", "--home", str(home.path)])

    target = flows_dir(home)
    for name, content in flows.packaged().items():
        assert (target / name).read_bytes() == content


def test_installing_twice_changes_nothing_the_second_time(
    home: ConnectorHome, fake_worc: FakeWorc, capsys: pytest.CaptureFixture[str]
) -> None:
    install_config(home)
    main(["install-flow", "--home", str(home.path)])
    capsys.readouterr()

    code = main(["install-flow", "--home", str(home.path)])

    assert code == 0
    assert "written" not in capsys.readouterr().out


def test_a_copy_you_edited_is_left_alone_until_you_say_otherwise(
    home: ConnectorHome, fake_worc: FakeWorc, capsys: pytest.CaptureFixture[str]
) -> None:
    install_config(home)
    main(["install-flow", "--home", str(home.path)])
    edited = flows_dir(home) / "issue_triage" / "analysis.md"
    edited.write_text("my own analysis prompt\n", encoding="utf-8", newline="")

    code = main(["install-flow", "--home", str(home.path)])

    assert code == 1
    assert edited.read_text(encoding="utf-8") == "my own analysis prompt\n"
    assert "--force" in capsys.readouterr().err

    forced = main(["install-flow", "--home", str(home.path), "--force"])

    assert forced == 0
    assert edited.read_text(encoding="utf-8") != "my own analysis prompt\n"


def test_install_flow_refuses_while_the_analysis_step_is_off(
    home: ConnectorHome, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(home, base_config())  # `research.mode` defaults to off

    code = main(["install-flow", "--home", str(home.path)])

    assert code == 2
    assert "research.mode" in capsys.readouterr().err
    assert not (home.clone_path / ".worc").exists()


def test_install_flow_leaves_a_flow_of_your_own_to_you(
    home: ConnectorHome, capsys: pytest.CaptureFixture[str]
) -> None:
    install_config(home, flow="my_triage")

    code = main(["install-flow", "--home", str(home.path)])

    assert code == 2
    assert "my_triage" in capsys.readouterr().err
    assert not (home.clone_path / ".worc").exists()


def test_install_flow_refuses_a_worc_that_would_reject_the_flow(
    home: ConnectorHome, fake_worc: FakeWorc, capsys: pytest.CaptureFixture[str]
) -> None:
    # The flow declares `report_dir`, which a worc from before that key refuses at load — the
    # operator would otherwise learn that from a task that failed rather than from this command.
    install_config(home)
    fake_worc.configure(version="0.13.0a1")

    code = main(["install-flow", "--home", str(home.path)])

    assert code == 1
    assert "report_dir" in capsys.readouterr().err
    assert not (home.clone_path / ".worc").exists()
