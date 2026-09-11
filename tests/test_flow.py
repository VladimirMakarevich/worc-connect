"""The flow this package ships, judged by worc's own validator rather than by our reading of it.

The connector installs this file into worc's flow directory, and worc refuses a flow it cannot
validate — at load, before any node runs. A second, local copy of worc's flow rules could drift
from the real ones without anything failing here, so these tests run the real validator, the real
loader and the real prompt-variable set.

What they hold the flow to beyond validity is the reason it is safe to point at a stranger's issue
text: it publishes nothing, every write goes into the connector's own home, and only the one node
that needs to run something asks for more than read-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from support import requires_worc, requires_worc_report_dir
from worc_connect import flows
from worc_connect.home import HOME_DIRNAME, ConnectorHome

PACKAGED = Path("src/worc_connect/packaged/flows")
FLOW_PATH = PACKAGED / f"{flows.PACKAGED_FLOW}.yaml"


@pytest.fixture
def document() -> dict[str, object]:
    """The shipped flow's ``flow:`` mapping, as the file on disk declares it."""
    loaded = yaml.safe_load(FLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    flow = loaded["flow"]
    assert isinstance(flow, dict)
    return flow


@requires_worc
@requires_worc_report_dir
def test_the_shipped_flow_passes_worcs_own_validator() -> None:
    from wastech_orchestrator.core.flow.snapshot import load_flow
    from wastech_orchestrator.core.flow.validator import validate_flow

    validate_flow(load_flow(FLOW_PATH))


@requires_worc
@requires_worc_report_dir
def test_every_prompt_variable_the_roles_use_is_one_worc_resolves() -> None:
    # A name outside the set renders verbatim rather than failing, so an instruction to write into
    # `{report_dir}` would reach the agent as the literal text `{report_dir}`.
    import re

    from wastech_orchestrator.core.flow.prompt_vars import valid_prompt_vars
    from wastech_orchestrator.core.flow.snapshot import load_flow

    allowed = set(valid_prompt_vars(load_flow(FLOW_PATH)))
    used = re.compile(r"\{\??([a-z0-9_-]+)\}")
    for role in sorted((PACKAGED / flows.PACKAGED_FLOW).glob("*.md")):
        assert not set(used.findall(role.read_text(encoding="utf-8"))) - allowed, role.name


@requires_worc
@requires_worc_report_dir
def test_the_report_lands_where_the_connector_reads_it(clone: Path) -> None:
    from wastech_orchestrator.core.flow.output_policy import resolve_output_policy
    from wastech_orchestrator.core.flow.snapshot import load_flow

    from worc_connect.core import triage

    doc = load_flow(FLOW_PATH).doc
    resolved = resolve_output_policy(doc.output_policy, "gh-142", doc.report_dir)
    resolved_dir = resolved.report_dir(clone)

    assert resolved_dir is not None
    # worc's resolution and the connector's reader have to name one file; they are written in two
    # places and this is what keeps them from drifting apart.
    home = ConnectorHome.beside(clone)
    assert triage.report_path(home.triage_path, "gh-142") == resolved_dir / "report.md"
    assert resolved.required_files == ("report.md",)
    assert resolved.private is True


def test_the_flow_declares_the_connectors_own_home_as_its_report_directory(
    document: dict[str, object],
) -> None:
    assert document["report_dir"] == ConnectorHome.beside(Path()).report_dir
    assert str(document["report_dir"]).startswith(f"{HOME_DIRNAME}/")


def test_the_flow_publishes_nothing(document: dict[str, object]) -> None:
    # The whole reason an agent may be pointed at untrusted issue text: its analysis cannot become
    # a change anybody has to review, or a branch anybody has to clean up.
    assert document["publishing"] == "none"
    assert document["output_policy"] == "private_control_workspace_report"
    nodes = document["nodes"]
    assert isinstance(nodes, list)
    assert not [
        node
        for node in nodes
        if node.get("kind") == "publish" and node["policy"] != document["output_policy"]
    ]


def test_only_the_reproduction_node_asks_for_more_than_read_only(
    document: dict[str, object],
) -> None:
    nodes = document["nodes"]
    assert isinstance(nodes, list)
    writing = [node["id"] for node in nodes if node.get("permission_profile") == "workspace-write"]

    assert writing == ["reproduction"]


def test_the_flow_grants_no_network_anywhere(document: dict[str, object]) -> None:
    # No `network_policy` is how a flow grants none; a per-node `true` could not widen it, but a
    # flow that named one would be granting the analysis of a stranger's text an outbound channel.
    assert "network_policy" not in document
    nodes = document["nodes"]
    assert isinstance(nodes, list)
    assert not [node for node in nodes if node.get("network_access") is True]


def test_the_flow_name_the_task_type_and_the_file_are_one_string(
    document: dict[str, object],
) -> None:
    # worc resolves a task's `task_type` as `<task_type>.yaml` in its flow directory, so the three
    # have to agree or the task the connector emits names a flow nothing resolves.
    assert document["name"] == flows.PACKAGED_FLOW
    assert document["task_type"] == flows.PACKAGED_FLOW
    assert FLOW_PATH.stem == flows.PACKAGED_FLOW


def test_every_role_file_the_flow_names_ships_with_it(document: dict[str, object]) -> None:
    nodes = document["nodes"]
    assert isinstance(nodes, list)
    named = {node["role_file"] for node in nodes if "role_file" in node}

    assert named
    assert named == set(flows.packaged()) - {f"{flows.PACKAGED_FLOW}.yaml"}
    for role_file in named:
        assert (PACKAGED / role_file).is_file()


def test_the_role_prompts_name_the_report_directory_only_through_the_variable() -> None:
    # A literal path in a prompt is a path that stops being true the moment an operator moves the
    # directory, and the agent would then be told to write somewhere the guard refuses.
    for role in sorted((PACKAGED / flows.PACKAGED_FLOW).glob("*.md")):
        text = role.read_text(encoding="utf-8")
        assert HOME_DIRNAME not in text, role.name
        assert ".worc/" not in text, role.name


def test_the_prompts_tell_the_agent_the_item_is_untrusted() -> None:
    # The one instruction every node that can see the item's text must carry: a report is evidence,
    # never instruction. It is the flow's half of the boundary the connector enforces in code.
    for name in ("scope.md", "analysis.md", "reproduction.md", "verifier.md"):
        assert "untrusted" in (PACKAGED / flows.PACKAGED_FLOW / name).read_text(encoding="utf-8")
