"""Unit tests for the management tools plugin."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from langharness_core.plugins.tools.management import ManagementToolsPlugin
from langharness_plugin.coordinator import RuntimeMutationError
from langharness_scope import Scope, ScopeId

READ_TOOLS = {"list_scope_tree", "list_runtime_plugins", "discover_plugins"}


def _registration(name: str = "demo-plugin", scope_id: str = "agent") -> Any:
    return SimpleNamespace(
        package_id="dynamic.core",
        contribution_id="demo-template",
        package_version="1.0.0",
        instance=f"uuid-{name}",
        factory=f"{name}-factory",
        module="langharness_core.plugins.tools.demo",
        scope_id=ScopeId(scope_id),
        enabled=True,
        status="installed",
    )


class FakeManager:
    def __init__(self) -> None:
        self.rescan_calls = 0
        self.registrations_ = [
            _registration(),
            _registration(name="other-plugin", scope_id="server"),
        ]
        self.scopes_ = (
            Scope(ScopeId("root"), None, "root"),
            Scope(ScopeId("agent"), ScopeId("root"), "agent"),
            Scope(ScopeId("agent:a"), ScopeId("agent"), "A"),
        )
        self.discovered_ = [
            SimpleNamespace(
                id="dynamic.core",
                version="1.0.0",
                contributions=[
                    SimpleNamespace(
                        id="demo-template",
                        descriptor=SimpleNamespace(
                            name="demo-template",
                            module="langharness_core.plugins.tools.demo",
                            specification="agent.plugin.tools",
                        ),
                        target="agent",
                    )
                ],
            )
        ]

    def scopes(self) -> tuple[Scope, ...]:
        return self.scopes_

    def registrations(self) -> list[Any]:
        return self.registrations_

    def rescan(self) -> Any:
        self.rescan_calls += 1
        return SimpleNamespace(packages=(), failures=())

    def discovered(self) -> list[Any]:
        return self.discovered_


def _plugin(manager: FakeManager) -> ManagementToolsPlugin:
    plugin = ManagementToolsPlugin()
    plugin._dynamic_manager = manager
    return plugin


def _tools(plugin: ManagementToolsPlugin) -> dict[str, Any]:
    return {tool.name: tool for tool in plugin.get_tools()}


def test_get_tools_empty_without_manager() -> None:
    assert ManagementToolsPlugin().get_tools() == []


def test_get_tools_exposes_read_tools() -> None:
    assert READ_TOOLS <= set(_tools(_plugin(FakeManager())))


def test_list_scope_tree_renders_tree() -> None:
    tool = _tools(_plugin(FakeManager()))["list_scope_tree"]
    assert tool.invoke({}) == {
        "scope_tree": "root\n└── agent\n    └── agent:a"
    }


def test_list_runtime_plugins_aggregates_all_scopes() -> None:
    tool = _tools(_plugin(FakeManager()))["list_runtime_plugins"]
    result = tool.invoke({})
    assert [item["factory"] for item in result["plugins"]] == [
        "demo-plugin-factory",
        "other-plugin-factory",
    ]


def test_list_runtime_plugins_filters_by_scope() -> None:
    tool = _tools(_plugin(FakeManager()))["list_runtime_plugins"]
    result = tool.invoke({"scope": "server"})
    assert [item["factory"] for item in result["plugins"]] == [
        "other-plugin-factory"
    ]
    assert result["plugins"][0]["scope_id"] == "server"


def test_list_runtime_plugins_rejects_bare_agent_prefix() -> None:
    from pydantic import ValidationError

    tool = _tools(_plugin(FakeManager()))["list_runtime_plugins"]
    with pytest.raises(ValidationError):
        tool.invoke({"scope": "agent:"})


def test_discover_plugins_rescans_and_lists() -> None:
    manager = FakeManager()
    tool = _tools(_plugin(manager))["discover_plugins"]
    result = tool.invoke({})
    assert manager.rescan_calls == 1
    assert result["packages"][0]["package_id"] == "dynamic.core"
    assert result["packages"][0]["contributions"][0]["id"] == "demo-template"


ALL_TOOLS = READ_TOOLS | {
    "install_plugin",
    "enable_plugin",
    "disable_plugin",
    "upgrade_plugin",
    "uninstall_plugin",
    "update_plugin_properties",
}


class MutatingManager(FakeManager):
    def __init__(self) -> None:
        super().__init__()
        self.installed: list[tuple[str, str, str]] = []
        self.enabled_calls: list[tuple[str, bool, str]] = []
        self.uninstalled: list[tuple[str, str]] = []
        self.upgraded: list[tuple[str, str]] = []
        self.properties_calls: list[tuple[str, dict[str, Any], str]] = []

    def install(
        self, package_id: str, contribution_id: str, *, scope_id: ScopeId | None = None
    ) -> Any:
        self.installed.append((package_id, contribution_id, str(scope_id)))
        return _registration()

    def set_enabled(self, name: str, enabled: bool, *, scope_id: ScopeId) -> Any:
        self.enabled_calls.append((name, enabled, str(scope_id)))
        registration = _registration(name=name, scope_id=str(scope_id))
        registration.enabled = enabled
        return registration

    def update_properties(
        self, name: str, properties: dict[str, object], *, scope_id: ScopeId
    ) -> Any:
        self.properties_calls.append((name, dict(properties), str(scope_id)))
        return _registration(name=name, scope_id=str(scope_id))

    def upgrade(self, name: str, *, scope_id: ScopeId) -> Any:
        self.upgraded.append((name, str(scope_id)))
        return _registration(name=name, scope_id=str(scope_id))

    def uninstall(self, name: str, *, scope_id: ScopeId) -> None:
        self.uninstalled.append((name, str(scope_id)))


class RaisingManager(MutatingManager):
    def install(
        self, package_id: str, contribution_id: str, *, scope_id: ScopeId | None = None
    ) -> Any:
        raise RuntimeMutationError("Plugin is already installed: demo-plugin")


def test_get_tools_exposes_all_nine_tools() -> None:
    assert set(_tools(_plugin(MutatingManager()))) == ALL_TOOLS


def test_install_delegates_and_summarizes() -> None:
    manager = MutatingManager()
    tool = _tools(_plugin(manager))["install_plugin"]
    result = tool.invoke(
        {
            "package_id": "dynamic.core",
            "contribution_id": "demo-template",
            "scope": "agent:a",
        }
    )
    assert manager.installed == [("dynamic.core", "demo-template", "agent:a")]
    assert result["factory"] == "demo-plugin-factory"
    assert result["instance"]
    assert result["scope_id"] == "agent"


def test_install_requires_valid_scope() -> None:
    from pydantic import ValidationError

    tool = _tools(_plugin(MutatingManager()))["install_plugin"]
    with pytest.raises(ValidationError):
        tool.invoke(
            {"package_id": "p", "contribution_id": "c", "scope": "api"}
        )


def test_enable_delegates() -> None:
    manager = MutatingManager()
    tool = _tools(_plugin(manager))["enable_plugin"]
    result = tool.invoke({"name": "demo-plugin", "scope": "agent"})
    assert manager.enabled_calls == [("demo-plugin", True, "agent")]
    assert result["enabled"] is True


def test_disable_requires_exact_confirm_token() -> None:
    from pydantic import ValidationError

    manager = MutatingManager()
    tool = _tools(_plugin(manager))["disable_plugin"]
    with pytest.raises(ValidationError):
        tool.invoke({"name": "demo-plugin", "scope": "agent"})
    with pytest.raises(ValidationError):
        tool.invoke(
            {"name": "demo-plugin", "scope": "agent", "confirm": "DISABLE-IT"}
        )
    result = tool.invoke(
        {"name": "demo-plugin", "scope": "agent", "confirm": "DISABLE"}
    )
    assert manager.enabled_calls == [("demo-plugin", False, "agent")]
    assert result["enabled"] is False


def test_upgrade_delegates() -> None:
    manager = MutatingManager()
    tool = _tools(_plugin(manager))["upgrade_plugin"]
    tool.invoke({"name": "demo-plugin", "scope": "agent"})
    assert manager.upgraded == [("demo-plugin", "agent")]


def test_uninstall_requires_exact_confirm_token() -> None:
    from pydantic import ValidationError

    manager = MutatingManager()
    tool = _tools(_plugin(manager))["uninstall_plugin"]
    with pytest.raises(ValidationError):
        tool.invoke({"name": "demo-plugin", "scope": "agent"})
    result = tool.invoke(
        {"name": "demo-plugin", "scope": "agent", "confirm": "UNINSTALL"}
    )
    assert manager.uninstalled == [("demo-plugin", "agent")]
    assert result == {"removed": True}


def test_update_properties_delegates_and_requires_nonempty() -> None:
    from pydantic import ValidationError

    manager = MutatingManager()
    tool = _tools(_plugin(manager))["update_plugin_properties"]
    with pytest.raises(ValidationError):
        tool.invoke({"name": "demo-plugin", "scope": "agent", "properties": {}})
    result = tool.invoke(
        {
            "name": "demo-plugin",
            "scope": "agent",
            "properties": {"plugin.timeout": 5},
        }
    )
    assert manager.properties_calls == [
        ("demo-plugin", {"plugin.timeout": 5}, "agent")
    ]
    assert result["factory"] == "demo-plugin-factory"


def test_coordinator_errors_map_to_error_dicts() -> None:
    tool = _tools(_plugin(RaisingManager()))["install_plugin"]
    result = tool.invoke(
        {"package_id": "p", "contribution_id": "c", "scope": "agent"}
    )
    assert result == {"error": "Plugin is already installed: demo-plugin"}
