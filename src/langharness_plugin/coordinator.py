"""Package-based dynamic plugin mutations over the instance model."""

from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import Any

from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor
from langharness_plugin.scope_const import AGENT_SCOPE_ID, SERVER_SCOPE_ID, UI_SCOPE_ID
from langharness_plugin.state_store import PersistedPluginRegistration
from langharness_scope import ROOT_SCOPE_ID, Scope, ScopeId

TARGET_SCOPES = {
    "root": ROOT_SCOPE_ID,
    "ui": UI_SCOPE_ID,
    "server": SERVER_SCOPE_ID,
    "agent": AGENT_SCOPE_ID,
}

TOOL_ADAPTER_MODULE = "langharness_core.plugins.tools.export_adapter"
TOOL_ADAPTER_FACTORY = "tool-export-adapter-factory"
TOOL_SPECIFICATION = "agent.plugin.tools"
BUILTIN_PACKAGE_PREFIX = "builtin."

TOOL_ADAPTER_DESCRIPTION = (
    "Adapts a tool export target into scoped tool services. Implements "
    "agent.plugin.tools. Properties: plugin.tool_export.target and "
    "plugin.tool_export.exports. Managed by the dynamic plugin coordinator."
)


class RuntimeMutationError(RuntimeError):
    pass


class RuntimeMutationCoordinator:
    """Dynamic plugin mutations over the manager's instance lifecycle."""

    def __init__(
        self,
        manager: PluginManager,
        discovery: PluginDiscovery | None = None,
    ) -> None:
        self.manager = manager
        self.discovery = discovery if discovery is not None else PluginDiscovery()
        self._lock = RLock()
        self._catalog: dict[str, PluginPackage] = {}
        self._failures: tuple[object, ...] = ()
        self._adapter_instances: dict[str, list[str]] = {}
        self._adapter_definition_installed = False

    def rescan(self) -> Any:
        result = self.discovery.scan()
        self._catalog = {package.id: package for package in result.packages}
        self._failures = result.failures
        return result

    def discovered(self) -> tuple[PluginPackage, ...]:
        return tuple(self._catalog[key] for key in sorted(self._catalog))

    def registrations(self) -> tuple[PersistedPluginRegistration, ...]:
        return self.manager.registrations()

    def scopes(self) -> tuple[Scope, ...]:
        return self.manager.scope_tree.snapshot().scopes

    def install(
        self,
        package_id: str,
        contribution_id: str,
        *,
        scope_id: ScopeId | None = None,
        registration_key: str | None = None,
    ) -> PersistedPluginRegistration:
        with self._lock:
            if package_id.startswith(BUILTIN_PACKAGE_PREFIX):
                raise RuntimeMutationError(
                    "Built-in plugins are managed by server/CLI assembly "
                    "and cannot be installed dynamically"
                )
            package, contribution = self._find(package_id, contribution_id)
            scope_id = scope_id if scope_id is not None else self._target_scope(
                contribution
            )
            properties = self._contribution_properties(contribution, scope_id)
            if registration_key is not None:
                existing = next(
                    (
                        item for item in self.manager.registrations()
                        if item.package_id == package_id
                        and item.contribution_id == contribution_id
                        and item.scope_id == scope_id
                        and item.registration_key == registration_key
                    ),
                    None,
                )
                if existing is not None:
                    return existing
            if self.manager.registry.get(contribution.descriptor.factory) is None:
                self.manager.install_descriptor(
                    contribution.descriptor, source="assembly"
                )
            snapshot = self.manager.create_instance(
                contribution.descriptor.factory,
                contribution.descriptor.module,
                scope_id,
                properties=properties,
                enabled=True,
            )
            registration = PersistedPluginRegistration(
                package.id, contribution.id, package.version,
                snapshot.instance, snapshot.factory, snapshot.module,
                scope_id, True, "installed", registration_key,
            )
            self.manager.attach_provenance(registration)
            try:
                self._install_adapters(registration, contribution)
                self.manager._persist_state()
            except Exception:
                self._kill_adapters(registration)
                self.manager.detach_provenance(snapshot.instance)
                self.manager.delete_instance(snapshot.instance)
                raise
            return registration

    def set_enabled(
        self, name: str, enabled: bool, *, scope_id: ScopeId
    ) -> PersistedPluginRegistration:
        with self._lock:
            registration = self._registration(name, scope_id)
            if registration.enabled == enabled:
                return registration
            snapshot = self.manager.update_instance(
                registration.instance, enabled=enabled
            )
            if enabled:
                package, contribution = self._find(
                    registration.package_id, registration.contribution_id
                )
                self._install_adapters(registration, contribution)
            else:
                self._kill_adapters(registration)
            updated = PersistedPluginRegistration(
                registration.package_id, registration.contribution_id,
                registration.package_version, snapshot.instance,
                snapshot.factory, snapshot.module, snapshot.scope_id,
                snapshot.enabled,
                "installed" if snapshot.enabled else "disabled",
                registration.registration_key,
            )
            self.manager.attach_provenance(updated)
            self.manager._persist_state()
            return updated

    def update_properties(
        self, name: str, properties: dict[str, object], *, scope_id: ScopeId
    ) -> PersistedPluginRegistration:
        with self._lock:
            registration = self._registration(name, scope_id)
            self.manager.update_instance(
                registration.instance,
                properties={str(key): value for key, value in properties.items()},
            )
            self.manager._persist_state()
            return next(
                item for item in self.manager.registrations()
                if item.instance == registration.instance
            )

    def upgrade(self, name: str, *, scope_id: ScopeId) -> PersistedPluginRegistration:
        with self._lock:
            registration = self._registration(name, scope_id)
            package, contribution = self._find(
                registration.package_id, registration.contribution_id
            )
            if package.version == registration.package_version:
                return registration
            properties = self._contribution_properties(contribution, scope_id)
            replacement = self.manager.create_instance(
                contribution.descriptor.factory,
                contribution.descriptor.module,
                scope_id,
                properties=properties,
                enabled=registration.enabled,
            )
            updated = PersistedPluginRegistration(
                registration.package_id, registration.contribution_id,
                package.version, replacement.instance,
                replacement.factory, replacement.module, scope_id,
                replacement.enabled,
                "installed" if replacement.enabled else "disabled",
                registration.registration_key,
            )
            self._kill_adapters(registration)
            self.manager.delete_instance(registration.instance)
            self.manager.detach_provenance(registration.instance)
            self.manager.attach_provenance(updated)
            self.manager._persist_state()
            self.manager._record_history(
                "upgrade", factory=updated.factory, module=updated.module,
                scope_id=str(scope_id), instance=updated.instance,
                detail={"replaced_instance": registration.instance},
            )
            return updated

    def uninstall(self, name: str, *, scope_id: ScopeId) -> None:
        with self._lock:
            registration = self._registration(name, scope_id)
            self._kill_adapters(registration)
            self.manager.delete_instance(registration.instance)
            self.manager.detach_provenance(registration.instance)
            self.manager._persist_state()

    def restore(self) -> tuple[PersistedPluginRegistration, ...]:
        """Restore manager state, then reattach adapters for live plugins."""
        with self._lock:
            if not self._catalog:
                self.rescan()
            self.manager.restore()
            # Agent-scoped instances are derived state and are dropped from
            # persistence; dynamic registrations in those scopes are rebuilt
            # from their contribution so their provenance stays valid.
            for registration in tuple(self.manager.registrations()):
                if not str(registration.scope_id).startswith("agent:"):
                    continue
                package = self._catalog.get(registration.package_id)
                if package is None:
                    continue
                contribution = next(
                    (
                        item for item in package.contributions
                        if item.id == registration.contribution_id
                    ),
                    None,
                )
                if contribution is None:
                    continue
                snapshot = self.manager.create_instance(
                    contribution.descriptor.factory,
                    contribution.descriptor.module,
                    registration.scope_id,
                    properties=self._contribution_properties(
                        contribution, registration.scope_id
                    ),
                    enabled=registration.enabled,
                )
                self.manager.attach_provenance(
                    replace(registration, instance=snapshot.instance)
                )
            for registration in tuple(self.manager.registrations()):
                package = self._catalog.get(registration.package_id)
                if package is None or not registration.enabled:
                    continue
                contribution = next(
                    (
                        item for item in package.contributions
                        if item.id == registration.contribution_id
                    ),
                    None,
                )
                if contribution is None:
                    continue
                try:
                    self._install_adapters(registration, contribution)
                except Exception:
                    continue
            self.manager._persist_state()
            return tuple(self.manager.registrations())

    def _find(
        self, package_id: str, contribution_id: str
    ) -> tuple[PluginPackage, PluginContribution]:
        package = self._catalog.get(package_id)
        if package is None:
            raise KeyError(package_id)
        for contribution in package.contributions:
            if contribution.id == contribution_id:
                return package, contribution
        raise KeyError(contribution_id)

    @staticmethod
    def _target_scope(contribution: PluginContribution) -> ScopeId:
        """The contribution's declared scope, used when none is passed."""
        if contribution.target == "agent_instance":
            raise RuntimeMutationError(
                "agent_instance contribution requires agent:<id> scope"
            )
        return TARGET_SCOPES.get(contribution.target, ROOT_SCOPE_ID)

    @staticmethod
    def _contribution_properties(
        contribution: PluginContribution, scope_id: ScopeId
    ) -> dict[str, object]:
        properties: dict[str, object] = {}
        if contribution.target == "agent_instance":
            if not str(scope_id).startswith("agent:"):
                raise RuntimeMutationError(
                    "agent_instance contribution requires agent:<id> scope"
                )
            properties["plugin.agent_id"] = str(scope_id).removeprefix("agent:")
        return properties

    def _registration(
        self, name: str, scope_id: ScopeId
    ) -> PersistedPluginRegistration:
        matches = [
            item for item in self.manager.registrations()
            if item.scope_id == scope_id
            and self._registration_name(item) == name
        ]
        if not matches:
            raise KeyError(f"plugin {name} not found in scope {scope_id}")
        if len(matches) > 1:
            raise RuntimeMutationError(
                f"plugin {name!r} is ambiguous in scope {scope_id!r}; "
                "use the instance UUID"
            )
        return matches[0]

    def _registration_name(self, registration: PersistedPluginRegistration) -> str:
        package = self._catalog.get(registration.package_id)
        if package is None:
            return registration.contribution_id
        for contribution in package.contributions:
            if contribution.id == registration.contribution_id:
                return contribution.descriptor.name
        return registration.contribution_id

    def _install_adapters(
        self,
        registration: PersistedPluginRegistration,
        contribution: PluginContribution,
    ) -> None:
        if not contribution.tool_exports:
            return
        if self.manager.registry.get(TOOL_ADAPTER_FACTORY) is None:
            self.manager.install_descriptor(
                PluginDescriptor(
                    name="tool-export-adapter",
                    version=registration.package_version,
                    module=TOOL_ADAPTER_MODULE,
                    factory=TOOL_ADAPTER_FACTORY,
                    specification=TOOL_SPECIFICATION,
                    description=TOOL_ADAPTER_DESCRIPTION,
                ),
                source="assembly",
            )
        self._adapter_definition_installed = True
        target = self.manager.find_service(
            contribution.descriptor.specification,
            f"(plugin.scope_id={registration.scope_id})",
        )
        if target is None:
            raise RuntimeMutationError("Tool export target service is unavailable")
        groups: dict[str, list[object]] = {}
        for export in contribution.tool_exports:
            target_scope = (
                str(registration.scope_id)
                if export.target_scope == "agent_instance"
                else "agent"
            )
            groups.setdefault(target_scope, []).append(export)
        created: list[str] = []
        for target_scope, exports in groups.items():
            snapshot = self.manager.create_instance(
                TOOL_ADAPTER_FACTORY, TOOL_ADAPTER_MODULE,
                ScopeId(target_scope),
                properties={
                    "plugin.tool_export.target": target,
                    "plugin.tool_export.exports": exports,
                },
            )
            created.append(snapshot.instance)
        self._adapter_instances[registration.instance] = created

    def _kill_adapters(self, registration: PersistedPluginRegistration) -> None:
        for instance in reversed(
            self._adapter_instances.pop(registration.instance, [])
        ):
            try:
                self.manager.delete_instance(instance)
            except Exception:
                pass
