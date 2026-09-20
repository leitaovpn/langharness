"""Unit tests for PluginManager start, discovery, and scope APIs."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from langharness_plugin.errors import ScopeHasChildrenError, ScopeHasInstancesError
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry, plugin_metadata
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Test plugin. Implements test.spec. Use in unit tests only. "
    "No properties. Uninstall when tests finish."
)


class EntryPoint:
    def __init__(self, target, name: str = "dynamic") -> None:
        self.target = target
        self.name = name
        self.value = f"{name}:load"

    def load(self):
        return self.target


@plugin_metadata(
    name="echo",
    version="1.0.0",
    factory="echo-factory",
    specification="test.echo",
    description=DESCRIPTION,
)
class Echo:
    pass


def manager_with(entry_points=()) -> PluginManager:
    manager = PluginManager(
        PluginRegistry(),
        discovery=lambda: [EntryPoint(item, f"ep-{i}") for i, item in enumerate(entry_points)],
    )
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    return manager


class TestLifecycle:
    def test_not_started_errors_and_stop_is_noop(self) -> None:
        manager = PluginManager(PluginRegistry())
        assert manager.started is False
        manager.stop()
        with pytest.raises(RuntimeError, match="not started"):
            manager.discover()

    def test_start_twice_raises(self) -> None:
        manager = PluginManager(PluginRegistry())
        manager._framework = Mock()
        with pytest.raises(RuntimeError, match="already started"):
            manager.start()


class TestDiscovery:
    def test_discover_builds_catalog_without_installing(self) -> None:
        manager = manager_with([Echo])
        snapshot = manager.discover()
        assert len(snapshot.descriptors) == 1
        assert snapshot.descriptors[0].factory == "echo-factory"
        assert manager._discovered == {
            (Echo.__module__, "echo-factory"): snapshot.descriptors[0]
        }
        manager._context.install_bundle.assert_not_called()

    def test_rediscover_replaces_catalog(self) -> None:
        manager = manager_with([Echo])
        manager.discover()
        manager.discover()
        assert len(manager._discovered) == 1

    def test_discovery_warnings_surface_in_snapshot(self) -> None:
        manager = manager_with([42])
        snapshot = manager.discover()
        assert snapshot.descriptors == ()
        assert snapshot.warnings


class TestScopes:
    def test_builtin_scopes_are_seeded_on_real_start(self) -> None:
        manager = PluginManager(PluginRegistry())
        manager.start()
        try:
            scopes = manager.list_scope()
            assert {scope.id for scope in scopes} >= {
                ROOT_SCOPE_ID,
                ScopeId("ui"),
                ScopeId("server"),
                ScopeId("agent"),
            }
        finally:
            manager.stop()

    def test_add_scope_requires_existing_parent(self) -> None:
        manager = manager_with()
        with pytest.raises(Exception):
            manager.add_scope(
                ScopeId("agent:a"), name="A", parent_id=ScopeId("missing")
            )
        manager.add_scope(ScopeId("agent"), name="Agent", parent_id=ROOT_SCOPE_ID)
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        assert manager.scope_tree.get(ScopeId("agent:a")) is not None

    def test_remove_scope_rejects_children_and_instances(self) -> None:
        manager = manager_with()
        manager.add_scope(ScopeId("agent"), name="Agent", parent_id=ROOT_SCOPE_ID)
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        with pytest.raises(ScopeHasChildrenError):
            manager.remove_scope(ScopeId("agent"))

        manager._instances["uuid-1"] = Mock(scope_id=ScopeId("agent:a"))
        manager._registration_instances[("m", "f", ScopeId("agent:a"))] = {"uuid-1"}
        with pytest.raises(ScopeHasInstancesError):
            manager.remove_scope(ScopeId("agent:a"))

    def test_remove_scope_recursive_deletes_instances(self) -> None:
        from langharness_plugin.registry import PluginInstanceSnapshot

        manager = manager_with()
        manager.add_scope(ScopeId("agent"), name="Agent", parent_id=ROOT_SCOPE_ID)
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        manager._instances["uuid-1"] = PluginInstanceSnapshot(
            "uuid-1", "f", "m", ScopeId("agent:a"), {}, True, 0, "active"
        )
        manager._ipopo.kill.return_value = None
        manager.remove_scope(ScopeId("agent:a"), recursive=True)
        assert manager._instances == {}
        manager._ipopo.kill.assert_called_once_with("uuid-1")


class TestDefinitions:
    def make_definition_manager(self) -> tuple[PluginManager, Any]:
        manager = manager_with([Echo])
        manager.discover()
        manager._context.install_bundle.return_value = Mock()
        return manager, manager._context.install_bundle.return_value

    def test_install_plugin_from_discovery(self) -> None:
        manager, bundle = self.make_definition_manager()
        snapshot = manager.install_plugin("echo-factory")
        assert snapshot.descriptor.factory == "echo-factory"
        assert snapshot.installed is True
        bundle.start.assert_called_once()
        assert manager.registry.get("echo-factory") is not None

    def test_install_plugin_unknown_factory_raises(self) -> None:
        from langharness_plugin.errors import PluginNotFoundError

        manager, _ = self.make_definition_manager()
        with pytest.raises(PluginNotFoundError, match="unknown-factory"):
            manager.install_plugin("unknown-factory")

    def test_install_descriptor_identity_conflict(self) -> None:
        from dataclasses import replace

        from langharness_plugin.errors import PluginIdentityConflictError

        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        other = replace(
            manager.registry.get("echo-factory"), module="other.module"
        )
        with pytest.raises(PluginIdentityConflictError):
            manager.install_descriptor(other)

    def test_double_install_is_idempotent_for_identical_definitions(self) -> None:
        from dataclasses import replace

        from langharness_plugin.errors import PluginAlreadyInstalledError

        manager, _ = self.make_definition_manager()
        first = manager.install_plugin("echo-factory")
        second = manager.install_plugin("echo-factory")
        assert first is second

        # A different descriptor for the same (module, factory) is rejected.
        changed = replace(
            manager.registry.get("echo-factory"), version="2.0.0"
        )
        with pytest.raises(PluginAlreadyInstalledError):
            manager.install_descriptor(changed)

    def test_bundle_shared_by_two_definitions_in_one_module(self) -> None:
        from dataclasses import replace

        manager, bundle = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        second = replace(
            manager.registry.get("echo-factory"), factory="echo2-factory"
        )
        manager.install_descriptor(second)
        assert manager._context.install_bundle.call_count == 1
        assert manager._bundles[Echo.__module__] is bundle

    def test_uninstall_with_instances_raises(self) -> None:
        from langharness_plugin.errors import PluginHasInstancesError
        from langharness_plugin.registry import PluginInstanceSnapshot

        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        manager._instances["uuid-1"] = PluginInstanceSnapshot(
            "uuid-1", "echo-factory", Echo.__module__, ROOT_SCOPE_ID, {}, True, 0, "active"
        )
        manager._registration_instances[
            (Echo.__module__, "echo-factory", ROOT_SCOPE_ID)
        ] = {"uuid-1"}
        with pytest.raises(PluginHasInstancesError):
            manager.uninstall_plugin("echo-factory")

    def test_uninstall_releases_bundle_when_module_unused(self) -> None:
        manager, bundle = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        manager.uninstall_plugin("echo-factory")
        bundle.stop.assert_called_once()
        bundle.uninstall.assert_called_once()
        assert manager._bundles == {}

    def test_list_and_show_plugin(self) -> None:
        from langharness_plugin.errors import PluginNotFoundError

        manager, _ = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        assert len(manager.list_plugin()) == 1
        shown = manager.show_plugin("echo-factory")
        assert shown.descriptor.name == "echo"
        assert shown.instance_count == 0
        with pytest.raises(PluginNotFoundError):
            manager.show_plugin("missing-factory")


class TestPersistence:
    def make_definition_manager(self) -> PluginManager:
        manager = manager_with([Echo])
        manager.discover()
        manager._context.install_bundle.return_value = Mock()
        manager._ipopo.instantiate.return_value = Mock()
        return manager

    def test_bind_state_persists_after_mutation(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        loaded = store.load()
        assert loaded is not None
        assert loaded.descriptors[0].descriptor.factory == "echo-factory"
        assert loaded.descriptors[0].source == "discovered"

    def test_persistence_failure_rolls_back_definition(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PluginStateConflictError,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        store.save(RuntimeStateSnapshot(0, (), (), (), ()), expected_version=0)
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager._state_version = 0  # stale version forces CAS conflict
        with pytest.raises(PluginStateConflictError):
            manager.install_plugin("echo-factory")
        assert manager.registry.get("echo-factory") is None
        assert manager._bundles == {}
        manager._context.install_bundle.return_value.stop.assert_called_once()

    def test_history_records_uninstall(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
        )

        manager = self.make_definition_manager()
        history = InMemoryPluginHistoryStore()
        manager.bind_state(InMemoryRuntimeStateStore(), history)
        manager.install_plugin("echo-factory")
        manager.uninstall_plugin("echo-factory")
        actions = [entry.action for entry in history.entries()]
        assert "install_definition" in actions
        assert "uninstall_definition" in actions

    def test_restore_rebuilds_instances_with_persisted_uuid(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        snapshot = manager.create_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID,
            properties={"plugin.mode": "fast"},
        )

        second = manager_with([Echo])
        second.discover()
        second._context.install_bundle.return_value = Mock()
        second._ipopo.instantiate.return_value = Mock()
        second.bind_state(store, InMemoryPluginHistoryStore())
        restored = second.restore()
        assert [item.instance for item in restored] == [snapshot.instance]
        second._ipopo.instantiate.assert_called_once()
        _, instance_name, properties = second._ipopo.instantiate.call_args.args
        assert instance_name == snapshot.instance
        assert properties["plugin.scope_id"] == "root"
        assert properties["plugin.mode"] == "fast"
        assert second.get_instance(snapshot.instance).status == "active"

    def test_restore_marks_missing_definition_instance(self) -> None:
        from langharness_plugin.registry import PluginInstanceRecord
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        record = PluginInstanceRecord(
            "uuid-x", "gone-factory", "gone.module", ROOT_SCOPE_ID, {}
        )
        store.save(
            RuntimeStateSnapshot(0, (), (), (record,), ()), expected_version=0
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        restored = manager.restore()
        assert len(restored) == 1
        assert restored[0].status == "missing"
        assert manager.get_instance("uuid-x").status == "missing"

    def test_restore_rebuilds_persisted_scopes(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        store.save(
            RuntimeStateSnapshot(
                0,
                (
                    {"id": "root", "parent_id": None, "name": "root"},
                    {"id": "server", "parent_id": "root", "name": "Server"},
                    {"id": "agent:a", "parent_id": "agent", "name": "A"},
                    {"id": "agent", "parent_id": "root", "name": "Agent"},
                ),
                (),
                (),
                (),
            ),
            expected_version=0,
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.restore()
        assert manager.scope_tree.get(ScopeId("server")) is not None
        assert manager.scope_tree.get(ScopeId("agent:a")) is not None
        assert manager.scope_tree.get(ScopeId("agent:a")).parent_id == ScopeId("agent")


class TestErrorPaths:
    def make_definition_manager(self) -> PluginManager:
        manager = manager_with([Echo])
        manager.discover()
        manager._context.install_bundle.return_value = Mock()
        manager._ipopo.instantiate.return_value = Mock()
        return manager

    def test_create_instance_persistence_failure_rolls_back(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PluginStateConflictError,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        store.save(RuntimeStateSnapshot(0, (), (), (), ()), expected_version=0)
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        manager._state_version = 0  # stale after the install persisted
        with pytest.raises(PluginStateConflictError):
            manager.create_instance(
                "echo-factory", Echo.__module__, ROOT_SCOPE_ID
            )
        assert manager.list_instance() == ()
        manager._ipopo.kill.assert_called_once()

    def test_update_instance_persistence_failure_keeps_old_snapshot(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PluginStateConflictError,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        snapshot = manager.create_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID,
            properties={"plugin.mode": "fast"},
        )
        manager._state_version = 0  # stale
        with pytest.raises(PluginStateConflictError):
            manager.update_instance(
                snapshot.instance, properties={"plugin.mode": "slow"}
            )
        kept = manager.get_instance(snapshot.instance)
        assert kept.properties["plugin.mode"] == "fast"

    def test_delete_instance_persistence_failure_restores_component(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PluginStateConflictError,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.install_plugin("echo-factory")
        snapshot = manager.create_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID
        )
        manager._ipopo.instantiate.reset_mock()
        manager._state_version = 0  # stale
        with pytest.raises(PluginStateConflictError):
            manager.delete_instance(snapshot.instance)
        assert manager.get_instance(snapshot.instance) is not None
        manager._ipopo.instantiate.assert_called_once()

    def test_restore_disabled_instance_stays_disabled(self) -> None:
        from langharness_plugin.registry import PluginInstanceRecord
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        store = InMemoryRuntimeStateStore()
        record = PluginInstanceRecord(
            "uuid-x", "echo-factory", Echo.__module__, ROOT_SCOPE_ID, {},
            enabled=False, status="disabled",
        )
        store.save(
            RuntimeStateSnapshot(0, (), (), (record,), ()), expected_version=0
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        restored = manager.restore()
        assert restored[0].status == "disabled"
        manager._ipopo.instantiate.assert_not_called()

    def test_restore_failed_instantiation_marks_failed(self) -> None:
        from langharness_plugin.registry import PluginInstanceRecord
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        manager._ipopo.instantiate.side_effect = RuntimeError("boom")
        store = InMemoryRuntimeStateStore()
        record = PluginInstanceRecord(
            "uuid-x", "echo-factory", Echo.__module__, ROOT_SCOPE_ID, {}
        )
        store.save(
            RuntimeStateSnapshot(0, (), (), (record,), ()), expected_version=0
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        restored = manager.restore()
        assert restored[0].status == "failed"

    def test_restore_rejects_orphan_scopes(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        store = InMemoryRuntimeStateStore()
        store.save(
            RuntimeStateSnapshot(
                0,
                ({"id": "orphan", "parent_id": "missing", "name": "O"},),
                (), (), (),
            ),
            expected_version=0,
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        with pytest.raises(RuntimeError, match="orphan"):
            manager.restore()

    def test_restore_drops_agent_scoped_instances_and_persists(self) -> None:
        from langharness_plugin.registry import PluginInstanceRecord
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        store = InMemoryRuntimeStateStore()
        record = PluginInstanceRecord(
            "uuid-x", "echo-factory", Echo.__module__, ScopeId("agent:a"), {}
        )
        store.save(
            RuntimeStateSnapshot(0, (), (), (record,), ()), expected_version=0
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        assert manager.restore() == ()
        assert manager.list_instance() == ()
        # The derived agent instance was pruned from the persisted state.
        assert store.load().instances == ()


class TestRemainingBranches:
    def make_definition_manager(self) -> PluginManager:
        manager = manager_with([Echo])
        manager.discover()
        manager._context.install_bundle.return_value = Mock()
        manager._ipopo.instantiate.return_value = Mock()
        return manager

    def test_install_plugin_with_wrong_module_raises_conflict(self) -> None:
        from langharness_plugin.errors import PluginIdentityConflictError

        manager = self.make_definition_manager()
        with pytest.raises(PluginIdentityConflictError):
            manager.install_plugin("echo-factory", module="wrong.module")

    def test_uninstall_with_wrong_module_raises_conflict(self) -> None:
        from langharness_plugin.errors import PluginIdentityConflictError

        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        with pytest.raises(PluginIdentityConflictError):
            manager.uninstall_plugin("echo-factory", module="wrong.module")

    def test_show_plugin_with_wrong_module_raises_conflict(self) -> None:
        from langharness_plugin.errors import PluginIdentityConflictError

        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        with pytest.raises(PluginIdentityConflictError):
            manager.show_plugin("echo-factory", module="wrong.module")

    def test_update_instance_without_definition_raises_state_error(self) -> None:
        from langharness_plugin.errors import InstanceStateError

        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        snapshot = manager.create_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID
        )
        # Simulate a definition that vanished without a clean uninstall.
        manager._plugins.pop((Echo.__module__, "echo-factory"))
        manager.registry.remove("echo-factory")
        with pytest.raises(InstanceStateError):
            manager.update_instance(snapshot.instance, properties={"x": "y"})

    def test_ensure_instance_reconciles_changed_configuration(self) -> None:
        manager = self.make_definition_manager()
        manager.install_plugin("echo-factory")
        first = manager.ensure_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID,
            properties={"plugin.mode": "one"}, enabled=True,
        )
        second = manager.ensure_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID,
            properties={"plugin.mode": "two"}, enabled=True,
        )
        assert second.instance == first.instance  # UUID preserved
        assert second.properties["plugin.mode"] == "two"
        # No-op when nothing changed.
        third = manager.ensure_instance(
            "echo-factory", Echo.__module__, ROOT_SCOPE_ID,
            properties={"plugin.mode": "two"}, enabled=True,
        )
        assert third.instance == first.instance

    def test_restore_marks_upgrade_available_for_newer_discovery(self) -> None:
        from dataclasses import replace

        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PersistedDescriptorRecord,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        older = replace(
            manager._discovered[(Echo.__module__, "echo-factory")],
            version="0.9.0",
        )
        store = InMemoryRuntimeStateStore()
        store.save(
            RuntimeStateSnapshot(
                0, (),
                (PersistedDescriptorRecord(older, "discovered"),),
                (), (),
            ),
            expected_version=0,
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.restore()
        definition = manager.show_plugin("echo-factory")
        assert definition.status == "upgrade_available"

    def test_restore_marks_missing_for_undiscovered_definitions(self) -> None:
        from langharness_plugin.state_store import (
            InMemoryPluginHistoryStore,
            InMemoryRuntimeStateStore,
            PersistedDescriptorRecord,
            RuntimeStateSnapshot,
        )

        manager = self.make_definition_manager()
        manager._discovered = {}  # nothing discovered
        from langharness_plugin.registry import PluginDescriptor

        gone = PluginDescriptor(
            name="gone", version="1.0.0", module="gone.module",
            factory="gone-factory", specification="test.spec",
            description=DESCRIPTION,
        )
        store = InMemoryRuntimeStateStore()
        store.save(
            RuntimeStateSnapshot(
                0, (),
                (PersistedDescriptorRecord(gone, "discovered"),),
                (), (),
            ),
            expected_version=0,
        )
        manager.bind_state(store, InMemoryPluginHistoryStore())
        manager.restore()
        assert manager.show_plugin("gone-factory").status == "missing"
