"""Contract enforcement tests: install-time rejection and bind-time quarantine."""

from __future__ import annotations

from typing import Any, Protocol
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langharness_api.plugins.routes.stream import StreamRoutePlugin
from langharness_api.plugins.server.app import APIServerService
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.validation import (
    ContractViolationError,
    Violation,
    service_contract,
)
from langharness_scope import ROOT_SCOPE_ID


@service_contract("test.enforce.tool")
class ToolLike(Protocol):
    def get_tools(self) -> list[Any]: ...


class Conforming:
    def get_tools(self) -> list[Any]:
        return []


class NonConforming:
    def get_tools(self, root: str) -> list[Any]:
        return []


def _descriptor(specification: str) -> PluginDescriptor:
    return PluginDescriptor(
        name="probe",
        version="1.0.0",
        module="module.probe",
        factory="probe-factory",
        specification=specification,
        description=(
            "Contract enforcement probe. Implements a test specification. "
            "No properties. Uninstall after tests."
        ),
    )


def _manager_with_instance(instance: Any) -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager._framework = Mock()
    manager._context = Mock()
    manager._ipopo = Mock()
    manager._ipopo.instantiate.return_value = instance
    manager._context.install_bundle.return_value = Mock()
    return manager


def test_create_instance_rejects_non_conforming_component() -> None:
    manager = _manager_with_instance(NonConforming())
    manager.install_descriptor(_descriptor("test.enforce.tool"))

    with pytest.raises(ContractViolationError) as excinfo:
        manager.create_instance("probe-factory", "module.probe", ROOT_SCOPE_ID)

    assert excinfo.value.plugin == "probe"
    assert "PARAM_EXTRA_REQUIRED" in str(excinfo.value)
    manager._ipopo.kill.assert_called_once()
    assert manager._instances == {}
    assert manager._ipopo.instantiate.call_count == 1


def test_create_instance_accepts_conforming_component() -> None:
    manager = _manager_with_instance(Conforming())
    manager.install_descriptor(_descriptor("test.enforce.tool"))

    snapshot = manager.create_instance("probe-factory", "module.probe", ROOT_SCOPE_ID)

    assert manager.get_instance(snapshot.instance).status == "active"
    manager._ipopo.kill.assert_not_called()


def test_create_instance_skips_unpinned_specification() -> None:
    manager = _manager_with_instance(NonConforming())
    manager.install_descriptor(_descriptor("test.enforce.unpinned"))

    snapshot = manager.create_instance("probe-factory", "module.probe", ROOT_SCOPE_ID)

    assert snapshot.status == "active"
    manager._ipopo.kill.assert_not_called()


class _BadRoute:
    def get_router(self, prefix: str) -> None:
        return None

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": "bad-route"}


def test_api_server_quarantines_bad_route_provider() -> None:
    component = APIServerService()
    bad = _BadRoute()
    component._route_providers.append(bad)

    assert component._guards["_route_providers"].admit(bad) is False
    assert component._route_providers == []
    assert len(component._guards["_route_providers"].rejected()) == 1


def test_stream_route_reports_contract_violation_as_400() -> None:
    violation = ContractViolationError(
        plugin="llm@simple_agent",
        specification="agent.plugin.llm",
        protocol="LLMProvider",
        violations=(
            Violation(
                "agent.plugin.llm",
                "LLMProvider",
                "get_model",
                "MISSING_METHOD",
                "LLMPlugin.get_model is not implemented",
            ),
        ),
    )

    class _BadDirectory:
        def list_agents(self) -> list[Any]:
            return []

        def get_loop(self, agent_id: str) -> Any:
            return None

        def ensure_plugin_instance(
            self, agent_id: str, plugin: str, properties: dict[str, Any]
        ) -> None:
            raise violation

        def reload(self, agent_id: str | None = None) -> None:
            return None

    plugin = StreamRoutePlugin()
    plugin._agent_directory = _BadDirectory()
    plugin._session_index = type(
        "Index", (), {"touch": lambda self, *args: None}
    )()
    app = FastAPI()
    app.include_router(plugin.get_router())

    response = TestClient(app).post(
        "/stream",
        json={
            "input": "hi",
            "model": "m",
            "api_key": "k",
            "base_url": "http://localhost",
            "session_id": "s",
        },
    )

    assert response.status_code == 400
    assert "MISSING_METHOD" in response.json()["detail"]
