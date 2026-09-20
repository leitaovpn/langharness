"""Tests for package-based dynamic mutations over the instance model."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from unittest.mock import Mock

import pytest

from langharness_plugin.coordinator import RuntimeMutationCoordinator
from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.state_store import (
    InMemoryPluginHistoryStore,
    InMemoryRuntimeStateStore,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Dynamic test plugin. Implements test.dynamic. Use in coordinator tests. "
    "No properties. Uninstall when tests finish."
)


class EntryPoint:
    def __init__(self, package: PluginPackage, name: str = "dynamic") -> None:
        self.package = package
        self.name = name
        self.value = f"{name}:package"

    def load(self):
        return lambda: self.package


def descriptor(**overrides) -> PluginDescriptor:
    fields = dict(
        name="dynamic",
        version="1.0.0",
        module="dynamic.module",
        factory="dynamic-factory",
        specification="test.dynamic",
        description=DESCRIPTION,
    )
    fields.update(overrides)
    return PluginDescriptor(**fields)


def package(version="1.0.0") -> PluginPackage:
    return PluginPackage(
        "dynamic.package", version,
        (PluginContribution("dynamic", "server", descriptor(version=version)),),
    )


def coordinator() -> tuple[RuntimeMutationCoordinator, PluginManager]:
    manager = PluginManager(PluginRegistry())
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    manager._context.install_bundle.return_value = Mock()
    manager._ipopo.instantiate.return_value = Mock()
    for scope_id in ("ui", "server", "agent"):
        manager.add_scope(ScopeId(scope_id), name=scope_id, parent_id=ROOT_SCOPE_ID)
    manager.add_scope(ScopeId("agent:a"), name="a", parent_id=ScopeId("agent"))
    manager.bind_state(InMemoryRuntimeStateStore(), InMemoryPluginHistoryStore())
    discovery = PluginDiscovery(lambda: [EntryPoint(package())])
    mutations = RuntimeMutationCoordinator(manager, discovery=discovery)
    mutations.rescan()
    return mutations, manager


def test_install_creates_instance_and_persists_provenance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    assert registration.factory == "dynamic-factory"
    assert registration.module == "dynamic.module"
    assert registration.scope_id == ScopeId("server")
    assert len(registration.instance) == 32
    assert registration.status == "installed"
    assert manager.get_instance(registration.instance) is not None
    assert manager.registrations() == (registration,)
    assert manager._state_store.load().registrations == (registration,)


def test_install_defaults_to_contribution_target_scope() -> None:
    mutations, manager = coordinator()
    registration = mutations.install("dynamic.package", "dynamic")
    assert registration.scope_id == ScopeId("server")


def test_reinstall_creates_second_instance() -> None:
    mutations, manager = coordinator()
    first = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    second = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    assert first.instance != second.instance
    assert len(manager.list_instance(scope_id=ScopeId("server"))) == 2


def test_install_with_registration_key_is_idempotent() -> None:
    mutations, manager = coordinator()
    first = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server"),
        registration_key="my-key",
    )
    second = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server"),
        registration_key="my-key",
    )
    assert first.instance == second.instance
    assert len(manager.list_instance()) == 1


def test_agent_instance_requires_agent_scope() -> None:
    agent_package = PluginPackage(
        "agent.package", "1.0.0",
        (PluginContribution("c", "agent_instance", descriptor()),),
    )
    mutations, _ = coordinator()
    mutations._catalog["agent.package"] = agent_package
    with pytest.raises(Exception, match="agent"):
        mutations.install("agent.package", "c", scope_id=ScopeId("server"))
    registration = mutations.install(
        "agent.package", "c", scope_id=ScopeId("agent:a")
    )
    assert registration.scope_id == ScopeId("agent:a")


def test_set_enabled_toggles_instance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    disabled = mutations.set_enabled("dynamic", False, scope_id=ScopeId("server"))
    assert disabled.enabled is False
    assert disabled.status == "disabled"
    assert disabled.instance == registration.instance  # UUID preserved
    enabled = mutations.set_enabled("dynamic", True, scope_id=ScopeId("server"))
    assert enabled.enabled is True
    assert enabled.instance == registration.instance


def test_update_properties_updates_instance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    updated = mutations.update_properties(
        "dynamic", {"plugin.mode": "fast"}, scope_id=ScopeId("server")
    )
    assert updated.instance == registration.instance
    assert (
        manager.get_instance(registration.instance).properties["plugin.mode"]
        == "fast"
    )


def test_upgrade_creates_new_uuid_and_records_replaced() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    upgraded_package = PluginPackage(
        "dynamic.package", "2.0.0",
        (PluginContribution("dynamic", "server", descriptor(version="2.0.0")),),
    )
    mutations._catalog["dynamic.package"] = upgraded_package
    upgraded = mutations.upgrade("dynamic", scope_id=ScopeId("server"))
    assert upgraded.package_version == "2.0.0"
    assert upgraded.instance != registration.instance
    with pytest.raises(Exception):
        manager.get_instance(registration.instance)  # old instance is gone
    history = manager._history.entries()
    replaced = [entry for entry in history if entry.action == "upgrade"]
    assert replaced and replaced[-1].detail["replaced_instance"] == registration.instance


def test_uninstall_removes_instance_and_provenance() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    mutations.uninstall("dynamic", scope_id=ScopeId("server"))
    assert manager.registrations() == ()
    with pytest.raises(Exception):
        manager.get_instance(registration.instance)
    with pytest.raises(KeyError):
        mutations._registration("dynamic", ScopeId("server"))


def test_ambiguous_name_in_scope_raises() -> None:
    mutations, manager = coordinator()
    mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    with pytest.raises(Exception, match="ambiguous"):
        mutations.set_enabled("dynamic", False, scope_id=ScopeId("server"))


def test_restore_hydrates_registrations_and_instances() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )

    # Simulate a fresh process over the same store.
    second = PluginManager(PluginRegistry())
    second._framework = Mock()
    second._context = Mock()
    second._ipopo = Mock()
    second._context.install_bundle.return_value = Mock()
    second._ipopo.instantiate.return_value = Mock()
    for scope_id in ("ui", "server", "agent"):
        second.add_scope(ScopeId(scope_id), name=scope_id, parent_id=ROOT_SCOPE_ID)
    second.bind_state(manager._state_store, InMemoryPluginHistoryStore())
    discovery = PluginDiscovery(lambda: [EntryPoint(package())])
    second_mutations = RuntimeMutationCoordinator(second, discovery=discovery)
    second_mutations.rescan()
    second_mutations.restore()
    assert second.registrations()[0].instance == registration.instance
    assert second.get_instance(registration.instance).status == "active"


def test_install_rejects_builtin_packages() -> None:
    from langharness_plugin.coordinator import RuntimeMutationError

    mutations, manager = coordinator()
    with pytest.raises(RuntimeMutationError, match="Built-in"):
        mutations.install("builtin.core", "anything")


def test_install_unknown_package_raises() -> None:
    mutations, manager = coordinator()
    with pytest.raises(KeyError):
        mutations.install("unknown.package", "echo")


def test_agent_instance_without_scope_raises() -> None:
    from langharness_plugin.coordinator import RuntimeMutationError

    agent_package = PluginPackage(
        "agent.package", "1.0.0",
        (PluginContribution("c", "agent_instance", descriptor()),),
    )
    mutations, _ = coordinator()
    mutations._catalog["agent.package"] = agent_package
    with pytest.raises(RuntimeMutationError, match="agent"):
        mutations.install("agent.package", "c")


def test_adapter_failure_rolls_back_the_instance() -> None:
    from langharness_plugin.coordinator import RuntimeMutationError
    from langharness_plugin.package import ToolExport

    exporting = PluginPackage(
        "export.package", "1.0.0",
        (
            PluginContribution(
                "echo", "server", descriptor(),
                tool_exports=(ToolExport("t", "d", "m", object),),
            ),
        ),
    )
    mutations, manager = coordinator()
    mutations._catalog["export.package"] = exporting
    manager.find_service = lambda spec, filter=None: None  # target unavailable
    with pytest.raises(RuntimeMutationError, match="unavailable"):
        mutations.install("export.package", "echo", scope_id=ScopeId("server"))
    assert manager.list_instance() == ()
    assert manager.registrations() == ()


def test_upgrade_same_version_is_a_noop() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    assert mutations.upgrade("dynamic", scope_id=ScopeId("server")) is registration


def test_scopes_returns_tree_snapshot() -> None:
    mutations, manager = coordinator()
    assert {item.id for item in mutations.scopes()} >= {ROOT_SCOPE_ID}


def test_find_missing_contribution_raises() -> None:
    mutations, manager = coordinator()
    with pytest.raises(KeyError, match="missing"):
        mutations._find("dynamic.package", "missing")


def test_kill_adapters_tolerates_missing_instances() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    mutations._adapter_instances[registration.instance] = ["gone-uuid"]
    mutations._kill_adapters(registration)  # must not raise
    assert mutations._adapter_instances == {}


def test_registration_name_falls_back_to_contribution_id() -> None:
    mutations, manager = coordinator()
    registration = mutations.install(
        "dynamic.package", "dynamic", scope_id=ScopeId("server")
    )
    mutations._catalog = {}  # package unknown: name falls back
    assert mutations._registration_name(registration) == "dynamic"
