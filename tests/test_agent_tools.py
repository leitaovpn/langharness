"""Unit tests for the agent operations export service and schemas."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from langharness_core.plugins.agents.export import (
    AGENT_TOOL_EXPORTS,
    AgentOperationsExport,
    CreateAgentArgs,
    GetAgentArgs,
    UpdateAgentArgs,
)


class FakeRegistry:
    def __init__(self) -> None:
        self.agents = [
            {"id": "a1", "name": "A1", "description": "", "enabled": True}
        ]
        self.created: list[tuple[str, str, str]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    def list_agents(self) -> list[dict[str, Any]]:
        return [dict(agent) for agent in self.agents]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        for agent in self.agents:
            if agent["id"] == agent_id:
                return dict(agent)
        return None

    def create_agent(
        self, agent_id: str, name: str, description: str
    ) -> dict[str, Any]:
        self.created.append((agent_id, name, description))
        agent = {
            "id": agent_id,
            "name": name or agent_id,
            "description": description,
            "enabled": True,
        }
        self.agents.append(agent)
        return dict(agent)

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        self.updated.append(
            (agent_id, {"name": name, "description": description, "enabled": enabled})
        )
        for agent in self.agents:
            if agent["id"] == agent_id:
                if name is not None:
                    agent["name"] = name
                if description is not None:
                    agent["description"] = description
                if enabled is not None:
                    agent["enabled"] = enabled
                return dict(agent)
        raise ValueError(f"Agent not found: {agent_id!r}")


def _service(registry: Any = None) -> AgentOperationsExport:
    service = AgentOperationsExport()
    service._registry = registry
    return service


def test_list_agents_returns_registry_view() -> None:
    result = _service(FakeRegistry()).invoke_export("list_agents", {})
    assert result == {"agents": [{"id": "a1", "name": "A1", "description": "", "enabled": True}]}


def test_get_agent_found_and_missing() -> None:
    service = _service(FakeRegistry())
    assert service.invoke_export("get_agent", {"agent_id": "a1"})["id"] == "a1"
    assert service.invoke_export("get_agent", {"agent_id": "nope"}) == {
        "error": "Agent not found: 'nope'"
    }


def test_create_agent_dispatches_and_defaults_name() -> None:
    registry = FakeRegistry()
    result = _service(registry).invoke_export(
        "create_agent", {"agent_id": "billing", "name": "", "description": "bills"}
    )
    assert registry.created == [("billing", "", "bills")]
    assert result["id"] == "billing"
    assert result["name"] == "billing"


def test_update_agent_passes_only_present_fields() -> None:
    registry = FakeRegistry()
    result = _service(registry).invoke_export(
        "update_agent", {"agent_id": "a1", "description": "new"}
    )
    assert registry.updated == [
        ("a1", {"name": None, "description": "new", "enabled": None})
    ]
    assert result["description"] == "new"


def test_update_agent_value_error_maps_to_error_dict() -> None:
    result = _service(FakeRegistry()).invoke_export(
        "update_agent", {"agent_id": "nope"}
    )
    assert result == {"error": "Agent not found: 'nope'"}


def test_unknown_operation_maps_to_error_dict() -> None:
    result = _service(FakeRegistry()).invoke_export("delete_agent", {"agent_id": "a1"})
    assert result == {"error": "Unknown operation: delete_agent"}


def test_registry_unavailable_maps_to_error_dict() -> None:
    assert _service(None).invoke_export("list_agents", {}) == {
        "error": "Agent registry unavailable"
    }


def test_tool_exports_declare_four_operations() -> None:
    by_name = {export.name: export for export in AGENT_TOOL_EXPORTS}
    assert set(by_name) == {
        "list_agents",
        "get_agent",
        "create_agent",
        "update_agent",
    }
    assert by_name["update_agent"].destructive is True
    assert all(export.target_scope == "agent" for export in AGENT_TOOL_EXPORTS)
    assert by_name["get_agent"].args_schema is GetAgentArgs
    assert by_name["create_agent"].args_schema is CreateAgentArgs


def test_update_agent_schema_requires_confirm_to_disable() -> None:
    with pytest.raises(ValidationError):
        UpdateAgentArgs(agent_id="a1", enabled=False)
    with pytest.raises(ValidationError):
        UpdateAgentArgs.model_validate(
            {"agent_id": "a1", "enabled": False, "confirm": "DISABLE-IT"}
        )
    model = UpdateAgentArgs(agent_id="a1", enabled=False, confirm="DISABLE")
    assert model.enabled is False


def test_schemas_reject_invalid_agent_ids() -> None:
    with pytest.raises(ValidationError):
        GetAgentArgs(agent_id="bad id")
    with pytest.raises(ValidationError):
        CreateAgentArgs(agent_id="x" * 65)
    assert GetAgentArgs(agent_id="web-1.billing").agent_id == "web-1.billing"


def test_agent_tools_package_declares_discoverable_contribution() -> None:
    from langharness_core.plugin import agent_tools_package
    from langharness_plugin.contracts import SPEC_TOOL_EXPORT_TARGET

    package = agent_tools_package()
    assert package.id == "agent.tools"
    assert package.version == "1.0.0"
    assert [item.id for item in package.contributions] == ["agent-operations"]
    contribution = package.contributions[0]
    assert contribution.target == "agent"
    assert contribution.tool_exports == AGENT_TOOL_EXPORTS
    descriptor = contribution.descriptor
    assert descriptor.name == "agent-operations-export"
    assert descriptor.module == "langharness_core.plugins.agents.export"
    assert descriptor.factory == "agent-operations-export-factory"
    assert descriptor.specification == SPEC_TOOL_EXPORT_TARGET
    assert descriptor.description.strip()
    assert not hasattr(descriptor, "instance")
    assert not hasattr(descriptor, "scope")
