"""Tests for scoped instance lifecycle on the rewritten PluginManager."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from unittest.mock import Mock

import pytest

from langharness_plugin.errors import (
    InstanceNotFoundError,
    PluginNotFoundError,
)
from langharness_plugin.plugin_manager import (
    FILTERS_PROPERTY,
    PLUGIN_INSTANCE_ID,
    PLUGIN_KEY,
    PLUGIN_SCOPE_CHAIN,
    PLUGIN_SCOPE_ID,
    PluginManager,
)
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Test plugin. Implements test.spec. Use in scoped tests only. "
    "Properties: plugin.mode. Uninstall when tests finish."
)


def descriptor(swap_policy="hot", **overrides) -> PluginDescriptor:
    fields = dict(
        name="test-plugin",
        version="1.0.0",
        module="langharness_core.test_module",
        factory="test-factory",
        specification="test.spec",
        description=DESCRIPTION,
        swap_policy=swap_policy,
    )
    fields.update(overrides)
    return PluginDescriptor(**fields)


def started_manager() -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    for scope_id in ("ui", "server", "agent"):
        manager.add_scope(ScopeId(scope_id), name=scope_id, parent_id=ROOT_SCOPE_ID)
    return manager


def installed_manager() -> PluginManager:
    manager = started_manager()
    manager._context.install_bundle.return_value = Mock()
    manager.install_descriptor(descriptor())
    manager._ipopo.instantiate.return_value = Mock()
    return manager


class TestCreateInstance:
    def test_create_generates_uuid_and_returns_snapshot(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        assert len(snapshot.instance) == 32
        assert snapshot.factory == "test-factory"
        assert snapshot.scope_id == ScopeId("server")
        assert snapshot.status == "active"
        assert manager.get_instance(snapshot.instance) is snapshot

    def test_create_defaults_to_root_scope(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance("test-factory", "langharness_core.test_module")
        assert snapshot.scope_id == ROOT_SCOPE_ID

    def test_create_rejects_unknown_scope(self) -> None:
        manager = installed_manager()
        with pytest.raises(Exception):
            manager.create_instance(
                "test-factory", "langharness_core.test_module", ScopeId("missing")
            )

    def test_create_rejects_uninstalled_definition(self) -> None:
        manager = started_manager()
        with pytest.raises(PluginNotFoundError):
            manager.create_instance("test-factory", "langharness_core.test_module")

    def test_injected_runtime_properties(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={"plugin.mode": "fast"}, ranking=2,
        )
        properties = dict(snapshot.properties)
        assert properties[PLUGIN_SCOPE_ID] == "server"
        assert properties[PLUGIN_KEY] == "test-factory"
        assert properties[PLUGIN_INSTANCE_ID] == snapshot.instance
        assert properties["plugin.ranking"] == 2
        assert properties["service.ranking"] > 0
        assert properties[PLUGIN_SCOPE_CHAIN] == ["server", "root"]
        assert properties["plugin.mode"] == "fast"

    def test_multiple_instances_same_key(self) -> None:
        manager = installed_manager()
        first = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        second = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        assert first.instance != second.instance
        assert len(manager.list_instance()) == 2

    def test_disabled_instance_not_instantiated(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            enabled=False,
        )
        manager._ipopo.instantiate.assert_not_called()
        assert snapshot.status == "disabled"
        assert snapshot.enabled is False

    def test_contract_violation_kills_component(self, monkeypatch) -> None:
        import langharness_plugin.plugin_manager as module

        manager = installed_manager()
        monkeypatch.setattr(module, "contract_for", lambda spec: Mock(__name__="P"))
        monkeypatch.setattr(
            module, "validate", lambda instance, protocol: ["missing method"]
        )
        with pytest.raises(Exception):
            manager.create_instance(
                "test-factory", "langharness_core.test_module", ScopeId("server")
            )
        manager._ipopo.kill.assert_called_once()


class TestUpdateInstance:
    def test_update_properties_hot_rebuilds_in_place_uuid(self) -> None:
        # Pelix has no in-place reconfigure, so both swap policies rebuild
        # the component under the same UUID.
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={"plugin.mode": "fast"},
        )
        manager._ipopo.instantiate.reset_mock()
        manager._ipopo.instantiate.return_value = Mock()
        updated = manager.update_instance(
            snapshot.instance, properties={"plugin.mode": "slow"}
        )
        manager._ipopo.kill.assert_called_once_with(snapshot.instance)
        manager._ipopo.instantiate.assert_called_once()
        assert updated.instance == snapshot.instance  # UUID preserved
        assert updated.properties["plugin.mode"] == "slow"

    def test_update_properties_restart_kills_and_recreates_same_uuid(self) -> None:
        manager = installed_manager()
        from langharness_plugin.registry import PluginDefinitionSnapshot

        manager._plugins[("langharness_core.test_module", "test-factory")] = (
            PluginDefinitionSnapshot(
                descriptor(swap_policy="restart"), True,
                "langharness_core.test_module", 0, (),
            )
        )
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        manager._ipopo.instantiate.reset_mock()
        updated = manager.update_instance(
            snapshot.instance, properties={"plugin.mode": "slow"}
        )
        manager._ipopo.kill.assert_called_once_with(snapshot.instance)
        manager._ipopo.instantiate.assert_called_once()
        assert updated.instance == snapshot.instance

    def test_update_rejects_unknown_instance(self) -> None:
        manager = installed_manager()
        with pytest.raises(InstanceNotFoundError):
            manager.update_instance("no-such-uuid", properties={})

    def test_update_failure_restores_old_state(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={"plugin.mode": "fast"},
        )
        # The rebuild fails; the old component must be restored.
        manager._ipopo.instantiate.return_value = Mock()
        manager._ipopo.instantiate.side_effect = [RuntimeError("boom"), Mock()]
        with pytest.raises(RuntimeError, match="boom"):
            manager.update_instance(
                snapshot.instance, properties={"plugin.mode": "slow"}
            )
        restored = manager.get_instance(snapshot.instance)
        assert restored.properties["plugin.mode"] == "fast"
        assert restored.instance == snapshot.instance

    def test_disable_then_enable_reuses_uuid(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        disabled = manager.update_instance(snapshot.instance, enabled=False)
        assert disabled.status == "disabled"
        manager._ipopo.kill.assert_called_with(snapshot.instance)
        enabled = manager.update_instance(snapshot.instance, enabled=True)
        assert enabled.instance == snapshot.instance
        assert enabled.status == "active"


class TestDeleteAndList:
    def test_delete_instance_removes_record_and_kills(self) -> None:
        manager = installed_manager()
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        manager.delete_instance(snapshot.instance)
        manager._ipopo.kill.assert_called_with(snapshot.instance)
        with pytest.raises(InstanceNotFoundError):
            manager.get_instance(snapshot.instance)

    def test_list_instance_filters(self) -> None:
        manager = installed_manager()
        first = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server")
        )
        manager.create_instance(
            "test-factory", "langharness_core.test_module", ROOT_SCOPE_ID
        )
        assert len(manager.list_instance(scope_id=ScopeId("server"))) == 1
        assert len(manager.list_instance(scope_id=ROOT_SCOPE_ID)) == 1
        assert (
            manager.list_instance(scope_id=ScopeId("server"))[0].instance
            == first.instance
        )

    def test_scoped_filter_merged_over_user_filter(self, monkeypatch) -> None:
        import langharness_plugin.plugin_manager as module

        manager = installed_manager()
        monkeypatch.setattr(
            module,
            "scoped_fields_from_module",
            lambda module_name: frozenset({"_llm_provider"}),
        )
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={
                FILTERS_PROPERTY: {
                    "_llm_provider": "(plugin.agent_id=a)",
                    "_custom": "(key=value)",
                }
            },
        )
        filters = snapshot.properties[FILTERS_PROPERTY]
        # Scoped field: scope filter AND user filter, scope clause first.
        assert filters["_llm_provider"] == (
            "(&(|(plugin.scope_id=server)(plugin.scope_id=root))"
            "(plugin.agent_id=a))"
        )
        # Unscoped field keeps its user filter untouched.
        assert filters["_custom"] == "(key=value)"

    def test_scoped_filter_without_user_filter(self, monkeypatch) -> None:
        import langharness_plugin.plugin_manager as module

        manager = installed_manager()
        monkeypatch.setattr(
            module,
            "scoped_fields_from_module",
            lambda module_name: frozenset({"_llm_provider"}),
        )
        snapshot = manager.create_instance(
            "test-factory", "langharness_core.test_module", ScopeId("server"),
            properties={},
        )
        assert snapshot.properties[FILTERS_PROPERTY]["_llm_provider"] == (
            "(|(plugin.scope_id=server)(plugin.scope_id=root))"
        )

    def test_invalid_filter_rejects_instance_creation(self) -> None:
        manager = installed_manager()
        with pytest.raises(ValueError, match="Invalid filter"):
            manager.create_instance(
                "test-factory", "langharness_core.test_module", ScopeId("server"),
                properties={FILTERS_PROPERTY: {"_custom": "(not-ldap"}},
            )
        manager._ipopo.instantiate.assert_not_called()
