"""Packaging: the extension point, the extra, and the dependency set the connector is allowed.

An adapter is an optional install discovered through an entry-point group, which is a packaging
property rather than a code one — so it is asserted against the installed distribution's metadata.
The dependency assertion is the machine-checked half of "no agent runtime here": a model SDK in this
list would mean the polling loop could make a model call.
"""

from __future__ import annotations

import inspect
from importlib.metadata import entry_points, metadata

import pytest

from support import requires_installed_distribution
from worc_connect.cli import ENTRY_POINT_GROUP
from worc_connect.trackers.base import TrackerAdapter

pytestmark = requires_installed_distribution

MODEL_SDK_NAMES = ("anthropic", "openai", "claude", "codex", "langchain", "litellm")


def installed_adapters() -> dict[str, object]:
    return {point.name: point for point in entry_points(group=ENTRY_POINT_GROUP)}


def test_the_github_adapter_registers_itself_in_the_group() -> None:
    assert "github" in installed_adapters()


def test_the_registered_factory_takes_the_repository_and_the_prefix_by_keyword() -> None:
    factory = entry_points(group=ENTRY_POINT_GROUP).select(name="github")["github"].load()

    signature = inspect.signature(factory)

    assert list(signature.parameters) == ["repo", "labels_prefix"]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )


def test_the_registered_factory_builds_something_the_core_can_drive() -> None:
    factory = entry_points(group=ENTRY_POINT_GROUP).select(name="github")["github"].load()

    assert isinstance(factory(repo="OWNER/REPO", labels_prefix="worc:"), TrackerAdapter)


def test_the_github_extra_exists_so_the_documented_install_works() -> None:
    assert "github" in (metadata("worc-connect").get_all("Provides-Extra") or [])


@pytest.mark.parametrize("name", MODEL_SDK_NAMES)
def test_the_connector_depends_on_no_model_sdk(name: str) -> None:
    required = metadata("worc-connect").get_all("Requires-Dist") or []
    runtime = [line for line in required if "extra ==" not in line]

    assert all(name not in line.casefold() for line in runtime)


def test_the_runtime_dependency_set_is_the_configuration_parser_alone() -> None:
    required = metadata("worc-connect").get_all("Requires-Dist") or []
    runtime = [line.split(";")[0].strip().casefold() for line in required if "extra ==" not in line]

    assert runtime == ["pyyaml>=6.0"]
