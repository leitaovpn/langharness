"""Lifecycle manager for Pelix/iPOPO plugin bundles."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from pelix import ldapfilter
from pelix.framework import BundleContext, Framework, FrameworkFactory, create_framework
from pelix.ipopo.constants import SERVICE_IPOPO

from langharness_plugin.contracts import PluginRegistrar, ScopedPluginRegistrar
from langharness_plugin.discovery import _installed_entry_points, descriptor_discovery
from langharness_plugin.errors import (
    InstanceNotFoundError,
    InstanceStateError,
    PluginAlreadyInstalledError,
    PluginHasInstancesError,
    PluginIdentityConflictError,
    PluginNotFoundError,
    ScopeHasChildrenError,
    ScopeHasInstancesError,
)
from langharness_plugin.registry import (
    DefinitionStatus,
    DiscoverySnapshot,
    InstanceStatus,
    PluginDefinitionSnapshot,
    PluginDescriptor,
    PluginInstanceRecord,
    PluginInstanceSnapshot,
    PluginRegistry,
    validate_descriptor,
)
from langharness_plugin.scope_const import (
    BUILTIN_SCOPES,
    PLUGIN_KEY,
    PLUGIN_SCOPE_CHAIN,
    PLUGIN_SCOPE_ID,
)
from langharness_plugin.scope_policy import PluginScopePolicy
from langharness_plugin.scoped_dependencies import scoped_fields_from_module
from langharness_plugin.state_store import (
    DescriptorSource,
    HistoryEntry,
    PersistedDescriptorRecord,
    PersistedPluginRegistration,
    PluginHistoryStore,
    RuntimeStateSnapshot,
    RuntimeStateStore,
)
from langharness_plugin.validation import ContractViolationError, contract_for, validate
from langharness_scope import ROOT_SCOPE_ID, Scope, ScopeId, ScopeTree

LOGGER = logging.getLogger("langharness.plugin_manager")

FILTERS_PROPERTY = "requires.filters"
SERVICE_RANKING = "service.ranking"
SCOPE_RANKING_STRIDE = 1_000_000
PLUGIN_RANKING = "plugin.ranking"
PLUGIN_INSTANCE_ID = "plugin.instance_id"

_RUNTIME_KEYS = (
    PLUGIN_SCOPE_ID,
    PLUGIN_SCOPE_CHAIN,
    PLUGIN_KEY,
    PLUGIN_INSTANCE_ID,
    PLUGIN_RANKING,
    SERVICE_RANKING,
)


def _json_safe(value: Any) -> Any:
    """Reduce a property value to something the snapshot JSON can encode.

    Runtime-only values such as injected service objects become their repr;
    derived agent-scoped instances are dropped on restore, so the lossy
    conversion never affects restored state.
    """
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _validate_filters(properties: dict[str, Any]) -> None:
    """Reject malformed requires.filters: iPOPO silently ignores them."""
    filters = properties.get(FILTERS_PROPERTY)
    if filters is None:
        return
    if not isinstance(filters, dict):
        raise ValueError(f"{FILTERS_PROPERTY} must be a mapping of field to filter")
    for field, value in filters.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid filter for {field}: {value!r}")
        try:
            ldapfilter.get_ldap_filter(value)
        except ValueError as exc:
            raise ValueError(f"Invalid filter for {field}: {value!r}") from exc


class PluginManager:
    """Discovers, installs, instantiates, and persists plugin components."""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        *,
        scope_tree: ScopeTree | None = None,
        discovery: Callable[[], Iterable[Any]] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else PluginRegistry()
        self.scope_tree = scope_tree if scope_tree is not None else ScopeTree()
        self._scope_policy = PluginScopePolicy(self.scope_tree)
        self._discovery_loader = discovery or _installed_entry_points
        self._lock = RLock()
        self._framework: Framework | None = None
        self._context: BundleContext | None = None
        self._ipopo: Any = None
        self._discovered: dict[tuple[str, str], PluginDescriptor] = {}
        self._plugins: dict[tuple[str, str], PluginDefinitionSnapshot] = {}
        self._bundles: dict[str, Any] = {}
        self._module_refs: dict[str, set[tuple[str, str]]] = {}
        self._instances: dict[str, PluginInstanceSnapshot] = {}
        self._registration_instances: dict[tuple[str, str, ScopeId], set[str]] = {}
        self._sources: dict[tuple[str, str], DescriptorSource] = {}
        self._provenance: dict[str, PersistedPluginRegistration] = {}
        self._user_properties: dict[str, dict[str, Any]] = {}
        self._state_store: RuntimeStateStore | None = None
        self._history: PluginHistoryStore | None = None
        self._state_version = 0
        self._registration: Any = None
        self._scope_registration: Any = None
        self._dynamic_registration: Any = None

    @property
    def started(self) -> bool:
        return self._framework is not None

    def start(self) -> None:
        if self.started:
            raise RuntimeError("PluginManager is already started")
        self._framework = create_framework(["pelix.ipopo.core"])
        self._framework.start()
        self._context = self._framework.get_bundle_context()
        ipopo_reference: Any = self._context.get_service_reference(SERVICE_IPOPO)
        assert ipopo_reference is not None
        self._ipopo = self._context.get_service(ipopo_reference)
        self._registration = self._context.register_service(PluginRegistrar, self, {})
        self._scope_registration = self._context.register_service(
            ScopedPluginRegistrar, self, {}
        )
        try:
            self._seed_builtin_scopes()
        except Exception:
            self.stop()
            raise

    def _seed_builtin_scopes(self) -> None:
        for scope_id, name, parent_id in BUILTIN_SCOPES:
            self.add_scope(scope_id, name=name, parent_id=parent_id)

    def stop(self) -> None:
        if self._framework is None:
            return
        FrameworkFactory.delete_framework(self._framework)
        self._framework = None
        self._context = None
        self._ipopo = None
        self._registration = None
        self._scope_registration = None
        self._dynamic_registration = None
        self._bundles.clear()
        self._module_refs.clear()
        self._instances.clear()
        self._registration_instances.clear()
        self._plugins.clear()
        self._discovered.clear()
        self._provenance.clear()
        self._user_properties.clear()

    def _require_started(self) -> None:
        if not self.started or self._context is None or self._ipopo is None:
            raise RuntimeError("PluginManager is not started")

    # ------------------------------------------------------------------ discovery

    def discover(self) -> DiscoverySnapshot:
        self._require_started()
        descriptors, warnings = descriptor_discovery(self._discovery_loader)
        self._discovered = descriptors
        return DiscoverySnapshot(
            tuple(
                sorted(descriptors.values(), key=lambda d: (d.module, d.factory))
            ),
            warnings,
            datetime.now(UTC),
        )

    # --------------------------------------------------------------------- scopes

    def list_scope(self) -> tuple[Scope, ...]:
        return self.scope_tree.snapshot().scopes

    def add_scope(
        self,
        scope_id: ScopeId,
        *,
        name: str,
        parent_id: ScopeId | None = None,
    ) -> Scope:
        wanted_parent = parent_id if parent_id is not None else ROOT_SCOPE_ID
        existing = self.scope_tree.get(scope_id)
        if existing is None:
            return self.scope_tree.create(scope_id, name, wanted_parent)
        if existing.parent_id != wanted_parent:
            raise ValueError(f"Scope {scope_id!r} already has a different parent")
        return existing

    def remove_scope(self, scope_id: ScopeId, *, recursive: bool = False) -> None:
        with self._lock:
            targets = {scope_id}
            if recursive:
                targets.update(
                    item.id for item in self.scope_tree.descendants(scope_id)
                )
            affected = [
                instance
                for instance in self._instances.values()
                if instance.scope_id in targets
            ]
            if not recursive:
                children = self.scope_tree.children(scope_id)
                if children:
                    raise ScopeHasChildrenError(
                        f"Scope {scope_id!r} has child scopes: "
                        f"{[str(item.id) for item in children]}"
                    )
                if affected:
                    raise ScopeHasInstancesError(
                        f"Scope {scope_id!r} has plugin instances"
                    )
            for instance in reversed(affected):
                self._remove_instance_record(instance.instance)
            self.scope_tree.remove(scope_id, recursive=recursive)
            self._persist_state()

    def _remove_instance_record(self, instance: str) -> None:
        snapshot = self._instances.get(instance)
        if snapshot is None:
            return
        if snapshot.status == "active":
            self._ipopo.kill(instance)
        self._instances.pop(instance, None)
        self._user_properties.pop(instance, None)
        self._registration_instances.get(
            (snapshot.module, snapshot.factory, snapshot.scope_id), set()
        ).discard(instance)

    # ------------------------------------------------------------- definitions

    def install_descriptor(
        self,
        descriptor: PluginDescriptor,
        *,
        source: DescriptorSource = "assembly",
    ) -> PluginDefinitionSnapshot:
        with self._lock:
            self._require_started()
            return self._install_definition(descriptor, source=source)

    def _install_definition(
        self,
        descriptor: PluginDescriptor,
        *,
        source: DescriptorSource,
        persist: bool = True,
    ) -> PluginDefinitionSnapshot:
        validate_descriptor(descriptor)
        key = (descriptor.module, descriptor.factory)
        if key in self._plugins:
            installed_snapshot = self._plugins[key]
            if installed_snapshot.descriptor == descriptor:
                # Idempotent re-install of the same definition (assembly
                # paths run after restore).
                return installed_snapshot
            raise PluginAlreadyInstalledError(
                f"Plugin definition {descriptor.factory!r} from "
                f"{descriptor.module!r} is already installed"
            )
        existing = self.registry.get(descriptor.factory)
        if existing is not None and existing.module != descriptor.module:
            raise PluginIdentityConflictError(
                f"Factory {descriptor.factory!r} is already registered by "
                f"module {existing.module!r}; cannot install from "
                f"{descriptor.module!r}"
            )
        bundle = self._install_bundle(descriptor.module)
        self._module_refs.setdefault(descriptor.module, set()).add(key)
        self.registry.add(descriptor)
        self._sources[key] = source
        snapshot = PluginDefinitionSnapshot(
            descriptor, True, descriptor.module, 0, ()
        )
        self._plugins[key] = snapshot
        if persist:
            try:
                self._persist_state()
            except Exception:
                self._rollback_definition(key, bundle)
                raise
            self._record_history(
                "install_definition",
                factory=descriptor.factory,
                module=descriptor.module,
            )
        return snapshot

    def _install_bundle(self, module: str) -> Any:
        bundle = self._bundles.get(module)
        if bundle is not None:
            return bundle
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        bundle = self._context.install_bundle(module)
        bundle.start()
        self._bundles[module] = bundle
        return bundle

    def _rollback_definition(self, key: tuple[str, str], bundle: Any) -> None:
        self._plugins.pop(key, None)
        self._sources.pop(key, None)
        self.registry.remove(key[1])
        self._module_refs.get(key[0], set()).discard(key)
        self._unload_bundle_if_unused(key[0], _rollback_bundle=bundle)

    def _unload_bundle_if_unused(
        self, module: str, *, _rollback_bundle: Any | None = None
    ) -> None:
        refs = self._module_refs.get(module, set())
        if refs:
            return
        bundle = self._bundles.pop(module, None) or _rollback_bundle
        if bundle is None:
            return
        try:
            bundle.stop()
            bundle.uninstall()
        except Exception:
            pass

    def install_plugin(
        self, factory: str, *, module: str | None = None
    ) -> PluginDefinitionSnapshot:
        with self._lock:
            self._require_started()
            matches = [
                descriptor
                for descriptor in self._discovered.values()
                if descriptor.factory == factory
            ]
            if not matches:
                raise PluginNotFoundError(
                    f"Plugin factory {factory!r} is not discovered"
                )
            if module is not None:
                matches = [
                    descriptor for descriptor in matches if descriptor.module == module
                ]
                if not matches:
                    raise PluginIdentityConflictError(
                        f"No discovered plugin with factory {factory!r} "
                        f"in module {module!r}"
                    )
            if len(matches) > 1:
                raise PluginIdentityConflictError(
                    f"Factory {factory!r} is discovered in multiple modules: "
                    f"{sorted(item.module for item in matches)}; pass module="
                )
            return self._install_definition(matches[0], source="discovered")

    def uninstall_plugin(self, factory: str, *, module: str | None = None) -> None:
        with self._lock:
            self._require_started()
            key = self._definition_key(factory, module)
            affected = [
                snapshot
                for snapshot in self._instances.values()
                if (snapshot.module, snapshot.factory) == key
            ]
            if affected:
                raise PluginHasInstancesError(
                    f"Plugin {factory!r} still has {len(affected)} instance(s); "
                    "delete them first"
                )
            self.registry.remove(factory)
            self._plugins.pop(key)
            self._sources.pop(key, None)
            self._module_refs.get(key[0], set()).discard(key)
            self._unload_bundle_if_unused(key[0])
            self._persist_state()
            self._record_history(
                "uninstall_definition", factory=factory, module=key[0]
            )

    def _definition_key(
        self, factory: str, module: str | None
    ) -> tuple[str, str]:
        definition = self.registry.get(factory)
        if definition is None:
            raise PluginNotFoundError(f"Plugin factory {factory!r} is not installed")
        if module is not None and definition.module != module:
            raise PluginIdentityConflictError(
                f"Factory {factory!r} is installed from {definition.module!r}, "
                f"not {module!r}"
            )
        return (definition.module, factory)

    def list_plugin(self) -> tuple[PluginDefinitionSnapshot, ...]:
        return tuple(
            sorted(self._plugins.values(), key=lambda item: item.descriptor.factory)
        )

    def show_plugin(
        self, factory: str, *, module: str | None = None
    ) -> PluginDefinitionSnapshot:
        key = self._definition_key(factory, module)
        snapshot = self._plugins.get(key)
        if snapshot is None:
            raise PluginNotFoundError(f"Plugin factory {factory!r} is not installed")
        return snapshot

    # -------------------------------------------------------------- instances

    def create_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None = None,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool = True,
        ranking: int = 0,
    ) -> PluginInstanceSnapshot:
        with self._lock:
            self._require_started()
            scope_id = scope_id if scope_id is not None else ROOT_SCOPE_ID
            self.scope_tree.require(scope_id)
            key = (module, factory)
            definition = self._plugins.get(key)
            if definition is None:
                raise PluginNotFoundError(
                    f"Plugin definition {factory!r} from {module!r} is not installed"
                )
            user = dict(properties or {})
            instance_uuid = uuid.uuid4().hex
            effective = self._effective_properties(
                definition.descriptor, scope_id, user,
                ranking=ranking, instance_uuid=instance_uuid,
            )
            if enabled:
                self._instantiate_uuid(definition.descriptor, instance_uuid, effective)
                status: InstanceStatus = "active"
            else:
                status = "disabled"
            snapshot = PluginInstanceSnapshot(
                instance_uuid, factory, module, scope_id, effective,
                enabled, ranking, status,
            )
            self._instances[instance_uuid] = snapshot
            self._user_properties[instance_uuid] = user
            registration_key = (module, factory, scope_id)
            self._registration_instances.setdefault(
                registration_key, set()
            ).add(instance_uuid)
            try:
                self._persist_state()
            except Exception:
                if enabled:
                    self._ipopo.kill(instance_uuid)
                self._instances.pop(instance_uuid, None)
                self._user_properties.pop(instance_uuid, None)
                self._registration_instances.get(
                    registration_key, set()
                ).discard(instance_uuid)
                raise
            self._record_history(
                "create_instance", factory=factory, module=module,
                scope_id=str(scope_id), instance=instance_uuid,
            )
            return snapshot

    def ensure_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None,
        *,
        properties: Mapping[str, Any] | None,
        enabled: bool,
    ) -> PluginInstanceSnapshot:
        """Idempotent assembly helper: reconcile-or-create one instance."""
        scope_id = scope_id if scope_id is not None else ROOT_SCOPE_ID
        for snapshot in self._instances.values():
            if (
                snapshot.factory == factory
                and snapshot.module == module
                and snapshot.scope_id == scope_id
            ):
                user = self._user_properties.get(snapshot.instance, {})
                if snapshot.enabled != enabled or user != dict(properties or {}):
                    return self.update_instance(
                        snapshot.instance,
                        properties=dict(properties or {}) or None,
                        enabled=enabled,
                    )
                return snapshot
        return self.create_instance(
            factory, module, scope_id, properties=properties, enabled=enabled
        )

    def get_instance(self, instance: str) -> PluginInstanceSnapshot:
        snapshot = self._instances.get(instance)
        if snapshot is None:
            raise InstanceNotFoundError(instance)
        return snapshot

    def delete_instance(self, instance: str) -> None:
        with self._lock:
            self._require_started()
            snapshot = self._instances.get(instance)
            if snapshot is None:
                raise InstanceNotFoundError(instance)
            self._remove_instance_record(instance)
            try:
                self._persist_state()
            except Exception:
                if snapshot.status == "active":
                    self._instantiate_uuid(
                        self._plugins[(snapshot.module, snapshot.factory)].descriptor,
                        instance,
                        dict(snapshot.properties),
                    )
                self._instances[instance] = snapshot
                self._user_properties[instance] = dict(snapshot.properties)
                self._registration_instances.setdefault(
                    (snapshot.module, snapshot.factory, snapshot.scope_id), set()
                ).add(instance)
                raise
            self._record_history(
                "delete_instance", factory=snapshot.factory,
                module=snapshot.module, scope_id=str(snapshot.scope_id),
                instance=instance,
            )

    def update_instance(
        self,
        instance: str,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        ranking: int | None = None,
    ) -> PluginInstanceSnapshot:
        with self._lock:
            self._require_started()
            current = self._instances.get(instance)
            if current is None:
                raise InstanceNotFoundError(instance)
            definition = self._plugins.get((current.module, current.factory))
            if definition is None:
                raise InstanceStateError(
                    f"Definition for instance {instance!r} is not installed"
                )
            descriptor = definition.descriptor
            user = dict(self._user_properties.get(instance, {}))
            if properties is not None:
                user.update(properties)
            new_enabled = current.enabled if enabled is None else enabled
            new_ranking = current.ranking if ranking is None else ranking
            effective = self._effective_properties(
                descriptor, current.scope_id, user,
                ranking=new_ranking, instance_uuid=instance,
            )
            new_snapshot = PluginInstanceSnapshot(
                instance, current.factory, current.module, current.scope_id,
                effective, new_enabled, new_ranking,
                "active" if new_enabled else "disabled",
            )
            needs_rebind = (
                new_enabled != current.enabled
                or new_ranking != current.ranking
                or user != self._user_properties.get(instance)
            )
            try:
                if not new_enabled:
                    if current.status == "active":
                        self._ipopo.kill(instance)
                elif current.status == "disabled":
                    self._instantiate_uuid(descriptor, instance, effective)
                elif needs_rebind:
                    # Pelix's iPOPO service has no in-place reconfigure, so
                    # both swap policies rebuild the component under the same
                    # UUID; the swap_policy branch is reserved for a future
                    # iPOPO upgrade.
                    self._ipopo.kill(instance)
                    self._instantiate_uuid(descriptor, instance, effective)
                else:
                    pass  # nothing changed
            except Exception:
                if current.status == "active" and new_enabled:
                    self._ipopo.kill(instance)
                    self._instantiate_uuid(
                        descriptor, instance, dict(current.properties)
                    )
                raise
            self._instances[instance] = new_snapshot
            self._user_properties[instance] = user
            try:
                self._persist_state()
            except Exception:
                self._instances[instance] = current
                self._user_properties[instance] = dict(current.properties)
                raise
            self._record_history(
                "update_instance", factory=current.factory,
                module=current.module, scope_id=str(current.scope_id),
                instance=instance,
            )
            return new_snapshot

    def list_instance(
        self,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: ScopeId | None = None,
        enabled: bool | None = None,
    ) -> tuple[PluginInstanceSnapshot, ...]:
        result = [
            snapshot
            for snapshot in self._instances.values()
            if (factory is None or snapshot.factory == factory)
            and (module is None or snapshot.module == module)
            and (scope_id is None or snapshot.scope_id == scope_id)
            and (enabled is None or snapshot.enabled == enabled)
        ]
        return tuple(
            sorted(
                result,
                key=lambda item: (item.factory, str(item.scope_id), item.instance),
            )
        )

    def _effective_properties(
        self,
        descriptor: PluginDescriptor,
        scope_id: ScopeId,
        user: dict[str, Any],
        *,
        ranking: int,
        instance_uuid: str | None = None,
    ) -> dict[str, Any]:
        effective = dict(user)
        effective[PLUGIN_SCOPE_ID] = str(scope_id)
        effective[PLUGIN_SCOPE_CHAIN] = [
            str(item) for item in self._scope_policy.visible_scopes(scope_id)
        ]
        effective[PLUGIN_KEY] = descriptor.factory
        if instance_uuid is not None:
            effective[PLUGIN_INSTANCE_ID] = instance_uuid
        effective[PLUGIN_RANKING] = ranking
        effective[SERVICE_RANKING] = (
            self.scope_tree.depth(scope_id) * SCOPE_RANKING_STRIDE + ranking
        )
        # User filters must be valid LDAP before any merge: Pelix's parser is
        # lenient enough to accept broken merged strings.
        _validate_filters(effective)
        self._apply_scoped_filters(effective, descriptor, scope_id)
        _validate_filters(effective)
        return effective

    def _apply_scoped_filters(
        self,
        properties: dict[str, Any],
        descriptor: PluginDescriptor,
        scope_id: ScopeId,
    ) -> None:
        fields = scoped_fields_from_module(descriptor.module)
        if not fields:
            return
        filters = dict(properties.get(FILTERS_PROPERTY, {}))
        scope_filter = self._scope_policy.visibility_filter(scope_id)
        for field in fields:
            user = filters.get(field)
            if user and user.startswith(scope_filter):
                continue  # already in canonical form (scope filter first)
            filters[field] = (
                scope_filter if not user else f"(&{scope_filter}{user})"
            )
        properties[FILTERS_PROPERTY] = filters

    def _instantiate_uuid(
        self,
        descriptor: PluginDescriptor,
        instance_uuid: str,
        properties: dict[str, Any],
    ) -> Any:
        instance = self._ipopo.instantiate(
            descriptor.factory, instance_uuid, properties or None
        )
        protocol = contract_for(descriptor.specification)
        if protocol is not None:
            violations = validate(instance, protocol)
            if violations:
                self._ipopo.kill(instance_uuid)
                raise ContractViolationError(
                    plugin=descriptor.name,
                    specification=descriptor.specification,
                    protocol=protocol.__name__,
                    violations=violations,
                )
        setattr(instance, "_plugin_scope_id", properties.get(PLUGIN_SCOPE_ID, ""))
        setattr(instance, "_plugin_key", properties.get(PLUGIN_KEY, ""))
        setattr(instance, "_plugin_ranking", int(properties.get(PLUGIN_RANKING, 0)))
        return instance

    # ------------------------------------------------------------ service helpers

    def scope_filter(self, scope_id: ScopeId) -> str:
        return self._scope_policy.visibility_filter(scope_id)

    def register_runtime_service(self, specification: type[Any], service: Any) -> None:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        if self._dynamic_registration is not None:
            raise RuntimeError("Dynamic plugin manager is already registered")
        self._dynamic_registration = self._context.register_service(
            specification,
            service,
            {PLUGIN_SCOPE_ID: str(ROOT_SCOPE_ID), PLUGIN_KEY: "dynamic-plugin-manager"},
        )

    def installed_modules(self) -> set[str]:
        return set(self._bundles)

    def find_service(
        self, specification: str, filter: str | None = None
    ) -> Any | None:
        """Return the highest-ranked service matching specification and filter."""
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(
            specification, filter
        ) or []
        if not references:
            return None
        reference = max(
            references,
            key=lambda item: int(item.get_property(SERVICE_RANKING) or 0),
        )
        return self._context.get_service(reference)

    def find_services(
        self, specification: str, filter: str | None = None
    ) -> list[Any]:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(
            specification, filter
        ) or []
        return [self._context.get_service(reference) for reference in references]

    def get_service(self, specification: str, filter: str | None = None) -> Any | None:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        reference: Any = self._context.get_service_reference(specification, filter)
        if reference is None:
            return None
        return self._context.get_service(reference)

    def get_services(self, specification: str) -> list[Any]:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(specification) or []
        return [self._context.get_service(reference) for reference in references]

    def service_properties(self, specification: str) -> list[dict[str, Any]]:
        if self._context is None:
            raise RuntimeError("PluginManager is not started")
        references: Any = self._context.get_all_service_references(specification) or []
        return [dict(reference.get_properties()) for reference in references]

    def apply_config(
        self, overrides: dict[str, dict[str, Any]]
    ) -> dict[str, list[str]]:
        """Replace stored plugin configs; instances update in place by UUID."""
        applied: list[str] = []
        for name, override in overrides.items():
            try:
                descriptor = self.registry.get_by_name(name)
            except KeyError as exc:
                raise ValueError(f"Unknown plugin: {name}") from exc
            changed = False
            for snapshot in tuple(self._instances.values()):
                if snapshot.factory != descriptor.factory:
                    continue
                before = self._instances[snapshot.instance]
                self._user_properties[snapshot.instance] = {}
                updated = self.update_instance(
                    snapshot.instance,
                    properties=dict(override.get("properties") or {}),
                    enabled=bool(override.get("enabled", True)),
                )
                if updated != before:
                    changed = True
            if changed:
                applied.append(name)
        return {"applied": applied, "restart_required": []}

    # ------------------------------------------------------------ persistence

    def bind_state(
        self, store: RuntimeStateStore, history: PluginHistoryStore
    ) -> None:
        self._state_store = store
        self._history = history
        # Sync the CAS counter with the persisted store so mutations made
        # before restore() do not conflict with existing state. Old schema
        # files raise RuntimeStateSchemaError here at boot.
        loaded = store.load()
        self._state_version = loaded.version if loaded is not None else 0

    def registrations(self) -> tuple[PersistedPluginRegistration, ...]:
        return tuple(self._provenance.values())

    def attach_provenance(self, registration: PersistedPluginRegistration) -> None:
        self._provenance[registration.instance] = registration

    def detach_provenance(self, instance: str) -> None:
        self._provenance.pop(instance, None)

    def _persist_state(self) -> None:
        if self._state_store is None:
            return
        scopes = self.scope_tree.snapshot().scopes
        descriptors = tuple(
            PersistedDescriptorRecord(
                descriptor,
                self._sources.get(
                    (descriptor.module, descriptor.factory), "assembly"
                ),
            )
            for descriptor in self.registry.list()
        )
        instances = tuple(
            PluginInstanceRecord(
                snapshot.instance,
                snapshot.factory,
                snapshot.module,
                snapshot.scope_id,
                _json_safe(snapshot.properties),
                snapshot.enabled,
                snapshot.ranking,
                snapshot.status,
            )
            for snapshot in self._instances.values()
        )
        snapshot = RuntimeStateSnapshot(
            self._state_version,
            tuple(
                {
                    "id": str(scope.id),
                    "parent_id": (
                        str(scope.parent_id) if scope.parent_id is not None else None
                    ),
                    "name": scope.name,
                }
                for scope in scopes
            ),
            descriptors,
            instances,
            tuple(self._provenance.values()),
        )
        self._state_version = self._state_store.save(
            snapshot, expected_version=self._state_version
        )

    def _record_history(
        self,
        action: str,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: str | None = None,
        instance: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self._history is None:
            return
        try:
            self._history.append(
                HistoryEntry(
                    datetime.now(UTC).isoformat(),
                    action,
                    factory,
                    module,
                    scope_id,
                    instance,
                    dict(detail or {}),
                )
            )
        except Exception:
            LOGGER.warning("Could not append lifecycle history for %s", action)

    def restore(self) -> tuple[PluginInstanceSnapshot, ...]:
        """Rebuild persisted scopes, definitions, and instances after restart."""
        if self._state_store is None:
            return ()
        loaded = self._state_store.load()
        if loaded is None:
            return ()
        self._state_version = loaded.version
        self._restore_scopes(loaded.scopes)
        for record in loaded.descriptors:
            descriptor = record.descriptor
            key = (descriptor.module, descriptor.factory)
            if key in self._plugins:
                continue
            if record.source == "assembly":
                self._install_definition(descriptor, source="assembly", persist=False)
                continue
            discovered = self._discovered.get(key)
            status: DefinitionStatus = "installed"
            if discovered is None:
                status = "missing"
            elif discovered.version != descriptor.version:
                status = "upgrade_available"
            self._install_definition(descriptor, source="discovered", persist=False)
            self._plugins[key] = replace(self._plugins[key], status=status)
        restored: list[PluginInstanceSnapshot] = []
        dropped = False
        for instance_record in loaded.instances:
            if str(instance_record.scope_id).startswith("agent:"):
                # Agent-scoped instances are derived state: the agent
                # directory re-materializes them from stored agent configs.
                dropped = True
                continue
            key = (instance_record.module, instance_record.factory)
            definition = self._plugins.get(key)
            if definition is None:
                snapshot = PluginInstanceSnapshot(
                    instance_record.instance, instance_record.factory,
                    instance_record.module, instance_record.scope_id,
                    instance_record.properties, instance_record.enabled,
                    instance_record.ranking, "missing",
                )
                self._instances[instance_record.instance] = snapshot
                restored.append(snapshot)
                continue
            if not instance_record.enabled:
                snapshot = PluginInstanceSnapshot(
                    instance_record.instance, instance_record.factory,
                    instance_record.module, instance_record.scope_id,
                    instance_record.properties, False,
                    instance_record.ranking, "disabled",
                )
                self._instances[instance_record.instance] = snapshot
                restored.append(snapshot)
                continue
            try:
                effective = self._rehydrate_properties(
                    instance_record, definition.descriptor
                )
                self._instantiate_uuid(
                    definition.descriptor, instance_record.instance, effective
                )
                snapshot = PluginInstanceSnapshot(
                    instance_record.instance, instance_record.factory,
                    instance_record.module, instance_record.scope_id,
                    effective, True, instance_record.ranking, "active",
                )
            except Exception:
                snapshot = PluginInstanceSnapshot(
                    instance_record.instance, instance_record.factory,
                    instance_record.module, instance_record.scope_id,
                    instance_record.properties, instance_record.enabled,
                    instance_record.ranking, "failed",
                )
            self._instances[instance_record.instance] = snapshot
            self._user_properties[instance_record.instance] = {
                item: value
                for item, value in instance_record.properties.items()
                if item not in _RUNTIME_KEYS
            }
            self._registration_instances.setdefault(
                (instance_record.module, instance_record.factory,
                 instance_record.scope_id), set()
            ).add(instance_record.instance)
            restored.append(snapshot)
        # Provenance is durable registration state: agent-scoped instances are
        # derived and dropped above, but the coordinator rebuilds them from
        # these records.
        self._provenance = {
            registration.instance: registration
            for registration in loaded.registrations
        }
        if dropped:
            self._persist_state()
        return tuple(restored)

    def _restore_scopes(self, scopes: tuple[dict[str, str | None], ...]) -> None:
        pending = {
            ScopeId(str(item["id"])): item
            for item in scopes
            if item["id"] != str(ROOT_SCOPE_ID)
            and self.scope_tree.get(ScopeId(str(item["id"]))) is None
        }
        while pending:
            progressed = False
            for scope_id, item in tuple(pending.items()):
                parent = ScopeId(str(item["parent_id"] or str(ROOT_SCOPE_ID)))
                if self.scope_tree.get(parent) is None:
                    continue
                self.scope_tree.create(scope_id, str(item["name"]), parent)
                pending.pop(scope_id)
                progressed = True
            if not progressed:
                raise RuntimeError("Persisted scope tree contains an orphan")

    def _rehydrate_properties(
        self, record: PluginInstanceRecord, descriptor: PluginDescriptor
    ) -> dict[str, Any]:
        """Recompute deterministic scope metadata over persisted user config."""
        user = {
            key: value for key, value in record.properties.items()
            if key not in _RUNTIME_KEYS
        }
        return self._effective_properties(
            descriptor, record.scope_id, user,
            ranking=record.ranking, instance_uuid=record.instance,
        )
