"""Contract and plugin tests for per-agent tool presentation mode."""

from __future__ import annotations

import pytest

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_TOOL_PRESENTATION,
    ToolPresentationProvider,
)
from langharness_core.plugin import agent_plugin_descriptor, builtin_package
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.presentation.mode import ToolPresentationPlugin
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id
from langharness_plugin.validation import contract_for, validate


def test_presentation_service_contract_is_pinned() -> None:
    assert contract_for(SPEC_TOOL_PRESENTATION) is ToolPresentationProvider
    assert SPEC_TOOL_PRESENTATION in ALL_PLUGIN_SPECS
    assert validate(ToolPresentationPlugin(), ToolPresentationProvider) == ()


def test_mode_defaults_to_native_and_rejects_unknown_values() -> None:
    plugin = ToolPresentationPlugin()
    assert plugin.get_mode() == "native"
    for mode in ("ptc", "both"):
        plugin._mode = mode
        assert plugin.get_mode() == mode
    plugin._mode = "other"
    with pytest.raises(ValueError, match="Unknown tool presentation mode"):
        plugin.get_mode()


def test_mode_is_a_catalog_plugin_with_an_agent_template() -> None:
    descriptor = agent_plugin_descriptor("tool-presentation")
    assert descriptor.specification == SPEC_TOOL_PRESENTATION
    assert descriptor.factory == "tool-presentation-plugin-factory"
    contributions = {item.id: item for item in builtin_package().contributions}
    assert contributions["tool-presentation-template"].target == "agent"


def test_two_agent_scopes_can_select_different_modes() -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        descriptor = agent_plugin_descriptor("tool-presentation")
        manager.install_descriptor(descriptor)
        for agent_id, mode in (("alpha", "native"), ("beta", "ptc")):
            scope = agent_instance_scope_id(agent_id)
            manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
            manager.create_instance(
                descriptor.factory,
                descriptor.module,
                scope,
                properties={
                    "plugin.agent_id": agent_id,
                    "plugin.tools.presentation": mode,
                },
            )
        alpha = manager.find_service(
            SPEC_TOOL_PRESENTATION, filter="(plugin.scope_id=agent:alpha)"
        )
        beta = manager.find_service(
            SPEC_TOOL_PRESENTATION, filter="(plugin.scope_id=agent:beta)"
        )
        assert alpha is not None
        assert beta is not None
        assert alpha.get_mode() == "native"
        assert beta.get_mode() == "ptc"
        assert alpha is not beta
    finally:
        manager.stop()


@pytest.mark.parametrize("mode", ("ptc", "both"))
def test_loop_refuses_programmatic_mode_until_runtime_is_available(
    mode: str,
) -> None:
    loop = PluginAgentLoop()
    presentation = ToolPresentationPlugin()
    presentation._mode = mode
    loop._presentation_provider = presentation

    with pytest.raises(RuntimeError, match="PTC runtime is not available"):
        loop.invoke("hello")
    assert loop.describe()["tools"] == []
