"""The configuration loader: every value it accepts and every configuration it refuses.

The refusals are the point. This loader is what stands between a mistyped key and a connector that
turns items nobody gated into tasks, so each rejection is asserted by the key it names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from support import base_config, write_config
from worc_connect import config as config_module
from worc_connect.config import ConfigError, is_valid_repo, is_valid_task_id, load
from worc_connect.home import ConnectorHome


def test_a_full_configuration_loads_with_paths_resolved(home: ConnectorHome) -> None:
    document = base_config()
    document["task"]["commit_type_by_label"] = {"bug": "fix"}
    path = write_config(home, document)

    config = load(path)

    assert config.tracker == "github"
    assert config.repo == "OWNER/REPO"
    assert config.poll_interval_seconds == 300
    assert config.gate.labels == ("worc",)
    assert config.task.commit_type_by_label == {"bug": "fix"}
    assert config.worc.repo_path == home.clone_path.resolve()


def test_absent_optional_task_fields_stay_absent(home: ConnectorHome) -> None:
    path = write_config(home, base_config())

    config = load(path)

    assert config.task.task_type is None
    assert config.task.auto_merge is None
    assert config.task.commit_type_by_label == {}


def test_the_written_template_is_a_configuration_the_loader_accepts(home: ConnectorHome) -> None:
    from worc_connect.home import render_config

    path = write_config(home, render_config(tracker="github", repo="OWNER/REPO"))

    config = load(path)

    assert config.gate.labels == (config_module.DEFAULT_TRIGGER_LABEL,)
    assert config.task.auto_merge is None
    assert config.write_back.close_on_merge is True


def test_a_missing_file_names_the_command_that_writes_one(home: ConnectorHome) -> None:
    with pytest.raises(ConfigError, match="worc-connect init"):
        load(home.config_path)


def test_unreadable_yaml_is_refused(home: ConnectorHome) -> None:
    path = write_config(home, "gate: [unclosed\n")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load(path)


def test_an_empty_file_is_refused(home: ConnectorHome) -> None:
    path = write_config(home, "\n")

    with pytest.raises(ConfigError, match="empty"):
        load(path)


def test_a_gate_with_no_rule_is_refused_by_name(home: ConnectorHome) -> None:
    document = base_config()
    document["gate"] = {"labels": [], "authors": [], "allow_all": False}
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`gate` admits nothing")):
        load(path)


def test_allow_all_replaces_the_need_for_a_rule(home: ConnectorHome) -> None:
    document = base_config()
    document["gate"] = {"labels": [], "authors": [], "allow_all": True}
    path = write_config(home, document)

    assert load(path).gate.allow_all is True


def test_an_author_allow_list_alone_is_a_rule(home: ConnectorHome) -> None:
    document = base_config()
    document["gate"] = {"labels": [], "authors": ["maintainer"]}
    path = write_config(home, document)

    assert load(path).gate.authors == ("maintainer",)


def test_a_mistyped_key_is_refused_rather_than_ignored(home: ConnectorHome) -> None:
    document = base_config()
    document["gate"]["authros"] = ["maintainer"]
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("unknown configuration key `gate.authros`")):
        load(path)


def test_a_mistyped_top_level_key_is_refused(home: ConnectorHome) -> None:
    document = base_config()
    document["pol_interval_seconds"] = 30
    path = write_config(home, document)

    with pytest.raises(
        ConfigError, match=re.escape("unknown configuration key `pol_interval_seconds`")
    ):
        load(path)


def test_a_missing_repository_is_refused(home: ConnectorHome) -> None:
    document = base_config()
    del document["repo"]
    path = write_config(home, document)

    with pytest.raises(ConfigError, match="`repo` is required"):
        load(path)


@pytest.mark.parametrize("repo", ["OWNER", "OWNER/REPO/extra", "-owner/repo", "owner /repo", ""])
def test_a_repository_that_is_not_owner_slash_repo_is_refused(
    home: ConnectorHome, repo: str
) -> None:
    document = base_config()
    document["repo"] = repo
    path = write_config(home, document)

    with pytest.raises(ConfigError, match="`repo`"):
        load(path)


@pytest.mark.parametrize("tracker", ["GitHub", "github/adapter", "1github", ""])
def test_a_tracker_that_is_not_a_lowercase_name_is_refused(
    home: ConnectorHome, tracker: str
) -> None:
    document = base_config()
    document["tracker"] = tracker
    path = write_config(home, document)

    with pytest.raises(ConfigError, match="`tracker`"):
        load(path)


def test_an_unsupported_schema_version_is_refused(home: ConnectorHome) -> None:
    document = base_config()
    document["schema_version"] = 2
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`schema_version` 2")):
        load(path)


@pytest.mark.parametrize("interval", [0, -5, "300", True])
def test_a_poll_interval_that_is_not_a_positive_integer_is_refused(
    home: ConnectorHome, interval: object
) -> None:
    document = base_config()
    document["poll_interval_seconds"] = interval
    path = write_config(home, document)

    with pytest.raises(ConfigError, match="`poll_interval_seconds`"):
        load(path)


@pytest.mark.parametrize("id_prefix", ["GH", "con.", "gh/2", "-gh", "nul."])
def test_an_id_prefix_that_cannot_build_a_worc_task_id_is_refused(
    home: ConnectorHome, id_prefix: str
) -> None:
    document = base_config()
    document["task"]["id_prefix"] = id_prefix
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`task.id_prefix`")):
        load(path)


def test_a_repo_path_that_is_not_a_directory_is_refused(home: ConnectorHome) -> None:
    document = base_config()
    document["worc"]["repo_path"] = "not-there"
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`worc.repo_path`")):
        load(path)


def test_an_absolute_repo_path_is_taken_as_written(home: ConnectorHome, tmp_path: Path) -> None:
    elsewhere = tmp_path / "other-clone"
    elsewhere.mkdir()
    document = base_config()
    document["worc"]["repo_path"] = str(elsewhere)
    path = write_config(home, document)

    assert load(path).worc.repo_path == elsewhere.resolve()


def test_a_non_boolean_flag_is_refused_rather_than_read_for_truthiness(
    home: ConnectorHome,
) -> None:
    document = base_config()
    document["gate"]["allow_all"] = "yes"
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`gate.allow_all` must be true or false")):
        load(path)


def test_a_label_list_of_something_other_than_strings_is_refused(home: ConnectorHome) -> None:
    document = base_config()
    document["gate"]["labels"] = ["worc", ""]
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`gate.labels`")):
        load(path)


def test_a_commit_type_mapping_of_something_other_than_strings_is_refused(
    home: ConnectorHome,
) -> None:
    document = base_config()
    document["task"]["commit_type_by_label"] = {"bug": 7}
    path = write_config(home, document)

    with pytest.raises(ConfigError, match=re.escape("`task.commit_type_by_label`")):
        load(path)


def test_a_section_that_is_not_a_mapping_is_refused(home: ConnectorHome) -> None:
    document = base_config()
    document["gate"] = ["worc"]
    path = write_config(home, document)

    with pytest.raises(ConfigError, match="`gate` must be a mapping"):
        load(path)


@pytest.mark.parametrize("candidate", ["gh-142", "gh-142.2", "a", "0.1_2-3", "nul-1"])
def test_task_ids_worc_accepts(candidate: str) -> None:
    assert is_valid_task_id(candidate)


@pytest.mark.parametrize(
    "candidate",
    ["GH-142", "gh 142", "gh-142.", ".gh", "con", "con.txt", "nul.1", "-gh", "x" * 65, ""],
)
def test_task_ids_worc_rejects(candidate: str) -> None:
    assert not is_valid_task_id(candidate)


@pytest.mark.parametrize("repo", ["OWNER/REPO", "a/b", "Some-Owner/some.repo_1"])
def test_repositories_that_can_be_pinned(repo: str) -> None:
    assert is_valid_repo(repo)
