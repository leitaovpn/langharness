"""Unit tests for the scoped plugin registrar service."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from typing import Any, Protocol
from unittest.mock import Mock

import pytest

import langharness_plugin.plugin_manager as pm_module
from langharness_plugin.contracts import (
    SPEC_PLUGIN_SCOPE,
    PluginRegistrar,
    ScopedPluginRegistrar,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.validation import contract_for, service_contract
from langharness_scope import ROOT_SCOPE_ID

DESCRIPTION = (
    "Scoped test plugin. Implements test.scope.service. Properties: "
    "plugin.mode. Requires a restart for property changes. Uninstall "
    "when tests finish."
)


@service_contract("test.scope.service")
class ScopeLike(Protocol):
    def get_tools(self) -> list[Any]: ...


class _Conforming:
    def get_tools(self) -> list[Any]:
        return []


def descriptor(swap_policy: str = "restart") -> PluginDescriptor:
    return PluginDescriptor(
        name="scoped",
        version="1.0.0",
        module="module.scoped",
        factory="scoped-factory",
        specification="test.scope.service",
        description=DESCRIPTION,
        swap_policy=swap_policy,
    )


def make_manager(*, started: bool = True, policy: str = "restart") -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    manager._context.install_bundle.return_value = Mock()
    if started:
        manager._ipopo.instantiate.return_value = _Conforming()
        manager.install_descriptor(descriptor(swap_policy=policy))
        manager._ipopo.instantiate.reset_mock()
        manager._ipopo.instantiate.return_value = _Conforming()
    return manager


def make_scoped_instance(
    manager: PluginManager, **properties: Any
):
    return manager.create_instance(
        "scoped-factory", "module.scoped", ROOT_SCOPE_ID,
        properties=properties,
    )


def test_scoped_registrar_contract_is_pinned() -> None:
    assert SPEC_PLUGIN_SCOPE == "plugin.scope"
    assert contract_for(SPEC_PLUGIN_SCOPE) is ScopedPluginRegistrar


def test_manager_registers_itself_under_both_specifications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = Mock()
    framework = Mock()
    framework.get_bundle_context.return_value = context
    context.get_service_reference.return_value = Mock()
    context.get_service.return_value = Mock()
    monkeypatch.setattr(pm_module, "create_framework", lambda services: framework)

    manager = PluginManager(PluginRegistry())
    manager.start()

    registered = [call.args[0] for call in context.register_service.call_args_list]
    assert PluginRegistrar in registered
    assert ScopedPluginRegistrar in registered


def test_find_service_returns_none_without_match() -> None:
    manager = make_manager()
    manager._context.get_all_service_references.return_value = None
    assert manager.find_service("agent.plugin.llm") is None


def test_find_service_passes_the_filter_to_pelix() -> None:
    manager = make_manager()
    reference = Mock()
    reference.get_property.return_value = 0
    manager._context.get_all_service_references.return_value = [reference]

    manager.find_service("agent.plugin.llm", filter="(plugin.agent_id=ag1)")

    manager._context.get_all_service_references.assert_called_once_with(
        "agent.plugin.llm", "(plugin.agent_id=ag1)"
    )
    manager._context.get_service.assert_called_once_with(reference)


def test_find_service_requires_started_manager() -> None:
    manager = PluginManager(PluginRegistry())
    with pytest.raises(RuntimeError, match="not started"):
        manager.find_service("agent.plugin.llm")


def test_find_services_passes_scope_filter_and_returns_all_matches() -> None:
    manager = make_manager()
    first = Mock()
    second = Mock()
    manager._context.get_all_service_references.return_value = [first, second]
    manager._context.get_service.side_effect = ["first", "second"]

    assert manager.find_services("agent.plugin.tools", "(plugin.scope_id=root)") == [
        "first",
        "second",
    ]
    manager._context.get_all_service_references.assert_called_once_with(
        "agent.plugin.tools", "(plugin.scope_id=root)"
    )


@pytest.mark.parametrize(
    "bad_filter",
    ["(plugin.agent_id=ag1", "no parens at all", "(unbalanced))", ""],
)
def test_create_instance_rejects_malformed_filters(bad_filter: str) -> None:
    manager = make_manager()

    with pytest.raises(ValueError, match="filter"):
        manager.create_instance(
            "scoped-factory", "module.scoped", ROOT_SCOPE_ID,
            properties={"requires.filters": {"_llm_provider": bad_filter}},
        )

    manager._ipopo.instantiate.assert_not_called()


def test_create_instance_rejects_invalid_filter_shapes() -> None:
    manager = make_manager()

    with pytest.raises(ValueError, match="filters"):
        manager.create_instance(
            "scoped-factory", "module.scoped", ROOT_SCOPE_ID,
            properties={"requires.filters": "not-a-dict"},
        )


def test_create_instance_rejects_non_text_filter_values() -> None:
    manager = make_manager()

    with pytest.raises(ValueError, match="filter"):
        manager.create_instance(
            "scoped-factory", "module.scoped", ROOT_SCOPE_ID,
            properties={"requires.filters": {"_llm_provider": 42}},
        )


def test_create_instance_accepts_valid_filters() -> None:
    manager = make_manager()
    manager._ipopo.instantiate.return_value = _Conforming()

    manager.create_instance(
        "scoped-factory", "module.scoped", ROOT_SCOPE_ID,
        properties={
            "plugin.agent_id": "ag1",
            "requires.filters": {"_llm_provider": "(plugin.agent_id=ag1)"},
        },
    )

    manager._ipopo.instantiate.assert_called_once()


def test_apply_config_applies_property_changes_in_place() -> None:
    manager = make_manager(policy="hot")
    snapshot = make_scoped_instance(manager)
    manager._ipopo.reconfigure.return_value = None

    result = manager.apply_config(
        {"scoped": {"enabled": True, "properties": {"plugin.mode": "fast"}}}
    )

    assert result == {"applied": ["scoped"], "restart_required": []}
    assert manager.get_instance(snapshot.instance).properties["plugin.mode"] == "fast"
    # UUID preserved through config application.
    assert manager.get_instance(snapshot.instance).instance == snapshot.instance


def test_apply_config_restart_plugins_apply_immediately() -> None:
    manager = make_manager(policy="restart")
    snapshot = make_scoped_instance(manager)
    manager._ipopo.instantiate.reset_mock()
    manager._ipopo.instantiate.return_value = _Conforming()

    result = manager.apply_config(
        {"scoped": {"enabled": True, "properties": {"plugin.mode": "slow"}}}
    )

    assert result == {"applied": ["scoped"], "restart_required": []}
    assert manager.get_instance(snapshot.instance).properties["plugin.mode"] == "slow"
    assert manager.get_instance(snapshot.instance).instance == snapshot.instance


def test_apply_config_reports_unchanged_plugins_as_not_applied() -> None:
    manager = make_manager(policy="hot")
    make_scoped_instance(manager)
    manager._ipopo.reconfigure.return_value = None
    result = manager.apply_config({"scoped": {"enabled": True, "properties": {}}})
    assert result == {"applied": [], "restart_required": []}


def test_apply_config_reverts_removed_overrides() -> None:
    manager = make_manager(policy="hot")
    snapshot = make_scoped_instance(manager, **{"plugin.mode": "fast"})
    manager._ipopo.reconfigure.return_value = None

    result = manager.apply_config(
        {"scoped": {"enabled": True, "properties": {}}}
    )

    assert result == {"applied": ["scoped"], "restart_required": []}
    assert "plugin.mode" not in manager.get_instance(snapshot.instance).properties


def test_apply_config_rejects_unknown_plugins() -> None:
    manager = make_manager()
    with pytest.raises(ValueError, match="Unknown plugin: ghost"):
        manager.apply_config({"ghost": {"enabled": True}})


def test_apply_config_disables_hot_plugins() -> None:
    manager = make_manager(policy="hot")
    snapshot = make_scoped_instance(manager)

    result = manager.apply_config({"scoped": {"enabled": False}})

    assert result == {"applied": ["scoped"], "restart_required": []}
    assert manager.get_instance(snapshot.instance).enabled is False
    assert manager.get_instance(snapshot.instance).status == "disabled"
