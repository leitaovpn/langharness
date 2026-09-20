"""LangChain tools wrapping the runtime plugin-management coordinator."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field, field_validator

from langharness_core.contracts import ToolProvider
from langharness_plugin.contracts import DynamicPluginManager
from langharness_plugin.validation import ContractGuard, is_runtime_scope
from langharness_scope import ScopeId
from langharness_scope.render import render_scope_tree

SCOPE_HELP = (
    "Explicit runtime scope: root, server, ui, agent, or agent:<id>. "
    "The bare prefix 'agent:' is rejected."
)


class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


class RuntimeScopeArgs(BaseModel):
    """Base schema requiring an explicit, valid runtime scope."""

    scope: str = Field(description=SCOPE_HELP)

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        if not is_runtime_scope(value):
            raise ValueError(f"Unknown runtime scope: {value!r}")
        return value


class ListRuntimePluginsArgs(BaseModel):
    """Optional scope filter; omit to aggregate every runtime scope."""

    scope: str | None = Field(
        default=None, description=f"Optional filter. {SCOPE_HELP}"
    )

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str | None) -> str | None:
        if value is not None and not is_runtime_scope(value):
            raise ValueError(f"Unknown runtime scope: {value!r}")
        return value


class InstallPluginArgs(RuntimeScopeArgs):
    package_id: str = Field(
        min_length=1, description="Package id from discover_plugins."
    )
    contribution_id: str = Field(
        min_length=1, description="Contribution id from discover_plugins."
    )


class NameScopeArgs(RuntimeScopeArgs):
    name: str = Field(
        min_length=1,
        description="Exact registered plugin name from list_runtime_plugins.",
    )


class DisablePluginArgs(NameScopeArgs):
    confirm: Literal["DISABLE"] = Field(
        description="Must be the exact string 'DISABLE'."
    )


class UninstallPluginArgs(NameScopeArgs):
    confirm: Literal["UNINSTALL"] = Field(
        description="Must be the exact string 'UNINSTALL'."
    )


class UpdatePropertiesArgs(NameScopeArgs):
    properties: dict[str, Any] = Field(
        min_length=1, description="Non-empty mapping of property overrides."
    )


def _registration_summary(registration: Any) -> dict[str, Any]:
    """Safe registration view; never serializes embedded service objects."""
    return {
        "package_id": registration.package_id,
        "contribution_id": registration.contribution_id,
        "package_version": registration.package_version,
        "instance": registration.instance,
        "factory": registration.factory,
        "module": registration.module,
        "scope_id": str(registration.scope_id),
        "enabled": registration.enabled,
        "status": registration.status,
    }


def _discovered_summary(package: Any) -> dict[str, Any]:
    return {
        "package_id": package.id,
        "version": package.version,
        "contributions": [
            {
                "id": item.id,
                "name": item.descriptor.name,
                "target": item.target,
                "module": item.descriptor.module,
                "specification": item.descriptor.specification,
            }
            for item in package.contributions
        ],
    }


def _guard(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a mutation and map coordinator errors to a readable dict."""
    try:
        return action()
    except (KeyError, ValueError, RuntimeError) as exc:
        return {"error": str(exc)}


@ComponentFactory("management-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "management-tools-plugin")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest(
    "_dynamic_manager", DynamicPluginManager, optional=True, immediate_rebind=True
)
class ManagementToolsPlugin:
    """Exposes runtime plugin-management operations as LangChain tools."""

    def __init__(self) -> None:
        self._plugin_name = "management-tools-plugin"
        self._plugin_version = "1.0.0"
        self._dynamic_manager: Any = None
        self._guard = ContractGuard(self, "_dynamic_manager", DynamicPluginManager)

    @BindField("_dynamic_manager", if_valid=True)
    def _on_manager_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guard.admit(service):
            return

    @UnbindField("_dynamic_manager")
    def _on_manager_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guard.release(service)

    def get_tools(self) -> list[Any]:
        manager = self._dynamic_manager
        if manager is None:
            return []
        return [
            self._scope_tree_tool(manager),
            self._list_tool(manager),
            self._discover_tool(manager),
            self._install_tool(manager),
            self._enable_tool(manager),
            self._disable_tool(manager),
            self._upgrade_tool(manager),
            self._uninstall_tool(manager),
            self._properties_tool(manager),
        ]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    @staticmethod
    def _scope_tree_tool(manager: Any) -> StructuredTool:
        def list_scope_tree() -> dict[str, Any]:
            scopes = [
                {
                    "id": str(scope.id),
                    "parent_id": (
                        str(scope.parent_id)
                        if scope.parent_id is not None
                        else None
                    ),
                    "name": scope.name,
                }
                for scope in manager.scopes()
            ]
            return {"scope_tree": render_scope_tree(scopes)}

        return StructuredTool.from_function(
            func=list_scope_tree,
            name="list_scope_tree",
            description=(
                "Show the runtime scope tree (root/server/ui/agent/"
                "agent:<id>) with parent-child structure. Use it to "
                "understand scope topology before targeting plugin "
                "operations, because every plugin mutation requires an "
                "explicit scope. Returns {\"scope_tree\": <text tree>}, "
                "or {\"error\": ...}."
            ),
            args_schema=NoArgs,
        )

    @staticmethod
    def _list_tool(manager: Any) -> StructuredTool:
        def list_runtime_plugins(scope: str | None = None) -> dict[str, Any]:
            registrations = manager.registrations()
            if scope is not None:
                registrations = [
                    item for item in registrations if str(item.scope_id) == scope
                ]
            return {
                "plugins": [_registration_summary(item) for item in registrations]
            }

        return StructuredTool.from_function(
            func=list_runtime_plugins,
            name="list_runtime_plugins",
            description=(
                "List installed runtime plugins, optionally filtered by "
                "scope (omit to aggregate every runtime scope). Use it "
                "before enable/disable/upgrade/uninstall to learn the exact "
                "registered plugin names and scopes, because mutations "
                "match by (name, scope). Returns {\"plugins\": [{name, "
                "scope_id, enabled, status, package_id, contribution_id, "
                "specification}]}, or {\"error\": ...}."
            ),
            args_schema=ListRuntimePluginsArgs,
        )

    @staticmethod
    def _discover_tool(manager: Any) -> StructuredTool:
        def discover_plugins() -> dict[str, Any]:
            manager.rescan()
            return {
                "packages": [
                    _discovered_summary(item) for item in manager.discovered()
                ]
            }

        return StructuredTool.from_function(
            func=discover_plugins,
            name="discover_plugins",
            description=(
                "Rescan the plugin catalog and list discoverable packages "
                "with their contributions. Always run this before "
                "install_plugin to obtain valid package_id/contribution_id "
                "pairs. Returns {\"packages\": [{package_id, version, "
                "contributions: [{id, name, target, module, "
                "specification}]}]}, or {\"error\": ...}."
            ),
            args_schema=NoArgs,
        )

    @staticmethod
    def _install_tool(manager: Any) -> StructuredTool:
        def install_plugin(
            package_id: str, contribution_id: str, scope: str
        ) -> dict[str, Any]:
            return _guard(
                lambda: _registration_summary(
                    manager.install(
                        package_id,
                        contribution_id,
                        scope_id=ScopeId(scope),
                    )
                )
            )

        return StructuredTool.from_function(
            func=install_plugin,
            name="install_plugin",
            description=(
                "Install a discovered plugin contribution into a runtime "
                "scope. Run discover_plugins first to get valid "
                "package_id/contribution_id. Scope must be one of "
                "root/server/ui/agent/agent:<id> and is required — omitting "
                "or misspelling it fails instead of targeting the wrong "
                "scope. Built-in packages cannot be installed. Installed "
                "plugins start disabled; use enable_plugin to activate. "
                "Example: install_plugin(package_id='dynamic.core', "
                "contribution_id='management-tools-plugin-template', "
                "scope='agent'). Returns a registration summary, or "
                "{\"error\": ...}."
            ),
            args_schema=InstallPluginArgs,
        )

    @staticmethod
    def _enable_tool(manager: Any) -> StructuredTool:
        def enable_plugin(name: str, scope: str) -> dict[str, Any]:
            return _guard(
                lambda: _registration_summary(
                    manager.set_enabled(name, True, scope_id=ScopeId(scope))
                )
            )

        return StructuredTool.from_function(
            func=enable_plugin,
            name="enable_plugin",
            description=(
                "Enable a disabled runtime plugin in an explicit scope, "
                "activating its components immediately — tools it provides "
                "become available right away. name must be the exact "
                "registered plugin name from list_runtime_plugins. Returns "
                "a registration summary, or {\"error\": ...}."
            ),
            args_schema=NameScopeArgs,
        )

    @staticmethod
    def _disable_tool(manager: Any) -> StructuredTool:
        def disable_plugin(
            name: str, scope: str, confirm: str
        ) -> dict[str, Any]:
            return _guard(
                lambda: _registration_summary(
                    manager.set_enabled(name, False, scope_id=ScopeId(scope))
                )
            )

        return StructuredTool.from_function(
            func=disable_plugin,
            name="disable_plugin",
            description=(
                "Disable a runtime plugin in an explicit scope, unbinding "
                "its components immediately — its tools disappear from the "
                "agent loop. This includes disabling this management plugin "
                "itself; do that only when the user asked for it, because "
                "you cannot re-enable it afterwards. You MUST pass "
                "confirm='DISABLE' exactly or the call fails. Returns a "
                "registration summary, or {\"error\": ...}."
            ),
            args_schema=DisablePluginArgs,
        )

    @staticmethod
    def _upgrade_tool(manager: Any) -> StructuredTool:
        def upgrade_plugin(name: str, scope: str) -> dict[str, Any]:
            return _guard(
                lambda: _registration_summary(
                    manager.upgrade(name, scope_id=ScopeId(scope))
                )
            )

        return StructuredTool.from_function(
            func=upgrade_plugin,
            name="upgrade_plugin",
            description=(
                "Upgrade an installed runtime plugin in an explicit scope "
                "to the latest discovered package version. Use "
                "list_runtime_plugins first to confirm the status is "
                "'upgrade_available'. Returns a registration summary, or "
                "{\"error\": ...}."
            ),
            args_schema=NameScopeArgs,
        )

    @staticmethod
    def _uninstall_tool(manager: Any) -> StructuredTool:
        def uninstall_plugin(
            name: str, scope: str, confirm: str
        ) -> dict[str, Any]:
            def action() -> dict[str, Any]:
                manager.uninstall(name, scope_id=ScopeId(scope))
                return {"removed": True}

            return _guard(action)

        return StructuredTool.from_function(
            func=uninstall_plugin,
            name="uninstall_plugin",
            description=(
                "Permanently uninstall a runtime plugin from an explicit "
                "scope and delete its persisted registration; this cannot "
                "be undone automatically. Use disable_plugin instead when "
                "you only want to pause it. You MUST pass "
                "confirm='UNINSTALL' exactly or the call fails. Returns "
                "{\"removed\": true}, or {\"error\": ...}."
            ),
            args_schema=UninstallPluginArgs,
        )

    @staticmethod
    def _properties_tool(manager: Any) -> StructuredTool:
        def update_plugin_properties(
            name: str, scope: str, properties: dict[str, Any]
        ) -> dict[str, Any]:
            return _guard(
                lambda: _registration_summary(
                    manager.update_properties(
                        name, properties, scope_id=ScopeId(scope)
                    )
                )
            )

        return StructuredTool.from_function(
            func=update_plugin_properties,
            name="update_plugin_properties",
            description=(
                "Replace or add runtime properties of an installed plugin "
                "instance in an explicit scope (the runtime 'set' "
                "operation). properties must be a non-empty mapping of "
                "key/value pairs. Use list_runtime_plugins first for the "
                "exact registered name. Returns a registration summary, or "
                "{\"error\": ...}."
            ),
            args_schema=UpdatePropertiesArgs,
        )
