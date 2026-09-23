"""Tests for the agent presets and the code agent assembly."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from langharness_core.plugin import (
    AGENT_PLUGIN_CATALOG,
    AGENT_PRESETS,
    agent_preset_bindings,
    agent_preset_modules,
    builtin_package,
    preset_name_of,
)
from langharness_core.plugins.agents.directory import AgentDirectoryPlugin


def test_the_simple_preset_is_the_default_set() -> None:
    assert agent_preset_bindings("simple") == ("tools", "name")


def test_the_code_preset_carries_what_a_coding_agent_needs() -> None:
    bindings = agent_preset_bindings("code")
    for name in (
        "workspace",
        "workspace-state",
        "code-tools",
        "exec",
        "exec-tools",
        "repository-instructions",
        "prompt",
        "goal",
        "goal-tools",
        "verify-tool",
        "ask-user",
        "verifier",
        "run-controller",
        "name",
    ):
        assert name in bindings, name
    assert "tools" not in bindings


def test_every_preset_plugin_exists_in_the_catalog() -> None:
    for preset, names in AGENT_PRESETS.items():
        for name in names:
            assert name in AGENT_PLUGIN_CATALOG, f"{preset}: {name}"


def test_preset_modules_are_the_catalog_modules() -> None:
    modules = agent_preset_modules("code")
    assert "langharness_core.plugins.tools.code_workspace" in modules
    assert "langharness_core.plugins.controller.goal_round" in modules
    assert len(set(modules)) == len(modules)


def test_an_unknown_preset_falls_back_to_simple() -> None:
    assert agent_preset_bindings("nope") == agent_preset_bindings("simple")


def test_preset_name_is_read_from_the_pseudo_binding() -> None:
    plugins = {"preset": {"properties": {"preset.name": "code"}}}
    assert preset_name_of(plugins) == "code"


def test_preset_baseline_can_be_overridden_and_disabled() -> None:
    directory = AgentDirectoryPlugin()
    directory._configs["coder"] = {
        "preset": {"properties": {"preset.name": "code"}},
        "code-tools": {"enabled": False},
        "name": {"properties": {"plugin.agent_name": "Coder"}},
        "llm": {"enabled": True},
    }

    bindings = directory._bindings("coder")

    assert "preset" not in bindings
    assert "code-tools" not in bindings
    assert bindings["name"]["properties"]["plugin.agent_name"] == "Coder"
    assert bindings["llm"] == {"enabled": True}
    assert "run-controller" in bindings


def test_builtin_package_ships_the_new_code_agent_templates() -> None:
    contributions = {item.id for item in builtin_package().contributions}
    assert {
        "verify-tool-template",
        "ask-user-template",
        "run-controller-template",
    } <= contributions
