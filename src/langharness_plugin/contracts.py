"""Public contracts for runtime plugin management services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from langharness_plugin.registry import PluginDescriptor, PluginInstanceSnapshot
from langharness_plugin.state_store import PersistedPluginRegistration
from langharness_plugin.validation import service_contract
from langharness_scope import Scope, ScopeId

SPEC_PLUGIN_REGISTRAR = "plugin.registrar"
SPEC_PLUGIN_SCOPE = "plugin.scope"
SPEC_DYNAMIC_PLUGIN_MANAGER = "plugin.dynamic.manager"
SPEC_TOOL_EXPORT_TARGET = "plugin.tool_export.target"


@service_contract(SPEC_PLUGIN_REGISTRAR)
@runtime_checkable
class PluginRegistrar(Protocol):
    def install_descriptor(self, descriptor: PluginDescriptor) -> Any: ...


@service_contract(SPEC_PLUGIN_SCOPE)
@runtime_checkable
class ScopedPluginRegistrar(Protocol):
    """Creates and manages scoped component instances."""

    def create_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None = None,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool = True,
        ranking: int = 0,
    ) -> PluginInstanceSnapshot: ...

    def get_instance(self, instance: str) -> PluginInstanceSnapshot: ...

    def delete_instance(self, instance: str) -> None: ...

    def update_instance(
        self,
        instance: str,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        ranking: int | None = None,
    ) -> PluginInstanceSnapshot: ...

    def list_instance(
        self,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: ScopeId | None = None,
        enabled: bool | None = None,
    ) -> tuple[PluginInstanceSnapshot, ...]: ...

    def find_service(
        self, specification: str, filter: str | None = None
    ) -> Any | None: ...

    def find_services(
        self, specification: str, filter: str | None = None
    ) -> list[Any]: ...

    def installed_modules(self) -> set[str]: ...

    def add_scope(
        self, scope_id: ScopeId, *, name: str, parent_id: ScopeId | None = None
    ) -> Scope: ...

    def remove_scope(self, scope_id: ScopeId, *, recursive: bool = False) -> None: ...

    def scope_filter(self, scope_id: ScopeId) -> str: ...

    def apply_config(
        self, overrides: dict[str, dict[str, Any]]
    ) -> dict[str, list[str]]: ...


@service_contract(SPEC_DYNAMIC_PLUGIN_MANAGER)
@runtime_checkable
class DynamicPluginManager(Protocol):
    def rescan(self) -> Any: ...

    def discovered(self) -> tuple[Any, ...]: ...

    def registrations(self) -> tuple[PersistedPluginRegistration, ...]: ...

    def install(
        self,
        package_id: str,
        contribution_id: str,
        *,
        scope_id: ScopeId | None = None,
        registration_key: str | None = None,
    ) -> PersistedPluginRegistration: ...

    def set_enabled(
        self, name: str, enabled: bool, *, scope_id: ScopeId
    ) -> PersistedPluginRegistration: ...

    def update_properties(
        self, name: str, properties: dict[str, Any], *, scope_id: ScopeId
    ) -> PersistedPluginRegistration: ...

    def uninstall(self, name: str, *, scope_id: ScopeId) -> None: ...

    def upgrade(
        self, name: str, *, scope_id: ScopeId
    ) -> PersistedPluginRegistration: ...

    def scopes(self) -> tuple[Scope, ...]: ...


@service_contract(SPEC_TOOL_EXPORT_TARGET)
@runtime_checkable
class ToolExportTarget(Protocol):
    def invoke_export(self, operation: str, arguments: dict[str, Any]) -> Any: ...
