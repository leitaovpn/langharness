"""Two agents must not see each other's workspace."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

from pathlib import Path
from typing import Any

from langharness_core.contracts import (
    SPEC_TOOL,
    SPEC_WORKSPACE,
    SPEC_WORKSPACE_STATE,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id
from langharness_scope import ScopeId

DESCRIPTION = (
    "Scope e2e test plugin. Implements a test specification. Properties vary "
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
    (
        descriptor(
            "workspace",
            "langharness_core.plugins.workspace.root",
            "workspace-root-plugin-factory",
            SPEC_WORKSPACE,
        ),
    ),
    (
        descriptor(
            "workspace-state",
            "langharness_core.plugins.workspace.state",
            "workspace-state-plugin-factory",
            SPEC_WORKSPACE_STATE,
        ),
    ),
    (
        descriptor(
            "code-tools",
            "langharness_core.plugins.tools.code_workspace",
            "code-workspace-tools-plugin-factory",
            SPEC_TOOL,
        ),
    ),
)


def install_bundles(manager: PluginManager) -> None:
    """Install each descriptor exactly once, as the directory does."""
    for (item,) in BUNDLES:
        manager.install_descriptor(item)


def start_agent(manager: PluginManager, agent_id: str, workspace: Path) -> ScopeId:
    """Give one agent its own scope, workspace and tool instance."""
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    manager.create_instance(
        "workspace-root-plugin-factory",
        "langharness_core.plugins.workspace.root",
        scope,
        properties={"plugin.workspace.root_dir": str(workspace)},
    )
    manager.create_instance(
        "workspace-state-plugin-factory",
        "langharness_core.plugins.workspace.state",
        scope,
    )
    manager.create_instance(
        "code-workspace-tools-plugin-factory",
        "langharness_core.plugins.tools.code_workspace",
        scope,
    )
    return scope


def test_two_agents_get_their_own_workspace_and_tools(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        (first / "only-in-one.py").write_text("x\n", encoding="utf-8")
        install_bundles(manager)
        start_agent(manager, "alpha", first)
        start_agent(manager, "beta", second)

        alpha_tools: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:alpha)"
        )
        beta_tools: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:beta)"
        )
        assert len(alpha_tools) == 1
        assert len(beta_tools) == 1
        alpha = {item.name: item for item in alpha_tools[0].get_tools()}
        beta = {item.name: item for item in beta_tools[0].get_tools()}

        alpha_listing = alpha["list_directory"].invoke({})
        assert [entry["name"] for entry in alpha_listing["entries"]] == [
            "only-in-one.py"
        ]
        assert beta["list_directory"].invoke({})["entries"] == []
        # A relative read must resolve inside the caller's own workspace.
        assert (
            beta["read_file"].invoke({"path": "only-in-one.py"})["error"]["code"]
            == "path_not_found"
        )
    finally:
        manager.stop()


def test_mutation_generations_are_not_shared(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_bundles(manager)
        for name in ("alpha", "beta"):
            workspace = tmp_path / name
            workspace.mkdir()
            start_agent(manager, name, workspace)
        alpha_state = manager.find_service(
            SPEC_WORKSPACE_STATE, filter="(plugin.scope_id=agent:alpha)"
        )
        alpha_state.record_mutation("a.py", "apply_patch")
        beta_state = manager.find_service(
            SPEC_WORKSPACE_STATE, filter="(plugin.scope_id=agent:beta)"
        )
        assert alpha_state.get_generation() == 1
        assert beta_state.get_generation() == 0
    finally:
        manager.stop()
