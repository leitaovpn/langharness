
"""Two agents must not share a goal or a verifier."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

from pathlib import Path
from typing import Any

from langharness_core.contracts import SPEC_GOAL, SPEC_TOOL, SPEC_VERIFIER
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id

DESCRIPTION = (
    "Goal scope test plugin. Implements a test specification. Properties vary "
    "per instance. Requires a restart for property changes. Uninstall when "
    "tests finish."
)


def descriptor(
    name: str, module: str, factory: str, specification: str
) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=DESCRIPTION,
    )


BUNDLES = (
    descriptor(
        "goal",
        "langharness_core.plugins.goals.sqlite",
        "sqlite-goal-plugin-factory",
        SPEC_GOAL,
    ),
    descriptor(
        "verifier",
        "langharness_core.plugins.verifier.project",
        "project-verifier-plugin-factory",
        SPEC_VERIFIER,
    ),
    descriptor(
        "goal-tools",
        "langharness_core.plugins.goals.tools",
        "goal-tools-plugin-factory",
        SPEC_TOOL,
    ),
)


def start_agent(manager: PluginManager, agent_id: str, tmp_path: Path) -> None:
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    manag = {
        "sqlite-goal-plugin-factory": {
            "plugin.goal.path": str(tmp_path / agent_id / "goals.sqlite3")
        },
        "project-verifier-plugin-factory": {},
        "goal-tools-plugin-factory": {},
    }
    for factory, properties in manag.items():
        definition = next(item for item in BUNDLES if item.factory == factory)
        manager.create_instance(
            definition.factory, definition.module, scope, properties=properties
        )


def test_goals_are_per_agent(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        start_agent(manager, "alpha", tmp_path)
        start_agent(manager, "beta", tmp_path)

        alpha_goals: list[Any] = manager.find_services(
            SPEC_GOAL, filter="(plugin.scope_id=agent:alpha)"
        )
        beta_goals: list[Any] = manager.find_services(
            SPEC_GOAL, filter="(plugin.scope_id=agent:beta)"
        )
        assert len(alpha_goals) == 1
        assert len(beta_goals) == 1

        alpha_goals[0].create_goal("alpha objective", [{"id": "c1", "text": "t"}])
        assert alpha_goals[0].current_goal().objective == "alpha objective"
        assert beta_goals[0].current_goal() is None
    finally:
        manager.stop()


def test_the_goal_tool_gets_its_own_scopes_services(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        start_agent(manager, "alpha", tmp_path)
        goal = manager.find_service(
            SPEC_GOAL, filter="(plugin.scope_id=agent:alpha)"
        )
        goal.create_goal("alpha objective", [{"id": "c1", "text": "t"}])
        tool_providers: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:alpha)"
        )
        assert len(tool_providers) == 1
        assert tool_providers[0]._goals is goal
        names = {item.name for item in tool_providers[0].get_tools()}
        assert names == {"get_goal", "finish_goal"}
    finally:
        manager.stop()
