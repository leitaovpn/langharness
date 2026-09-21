"""Agent registry route plugin."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel

from langharness_api.contracts import RouteProvider
from langharness_core.contracts import (
    AgentDirectoryProvider,
    AgentRegistryProvider,
)
from langharness_plugin.validation import ContractGuard


class AgentCreateRequest(BaseModel):
    id: str
    name: str = ""
    description: str = ""


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None


@ComponentFactory("api-agents-route-factory")
@Provides(RouteProvider)
@Property("_plugin_name", "plugin.name", "agents")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest(
    "_agent_registry", AgentRegistryProvider, optional=True, immediate_rebind=True
)
@RequiresBest(
    "_agent_directory", AgentDirectoryProvider, optional=True, immediate_rebind=True
)
class AgentsRoutePlugin:
    def __init__(self) -> None:
        self._plugin_name = "agents"
        self._plugin_version = "1.0.0"
        self._agent_registry: Any = None
        self._agent_directory: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_agent_registry": ContractGuard(
                self, "_agent_registry", AgentRegistryProvider
            ),
            "_agent_directory": ContractGuard(
                self, "_agent_directory", AgentDirectoryProvider
            ),
        }

    @BindField("_agent_registry", if_valid=True)
    def _on_agent_registry_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_agent_registry")
    def _on_agent_registry_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        self._guards[field].release(service)

    @BindField("_agent_directory", if_valid=True)
    def _on_agent_directory_bind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        self._guards[field].admit(service)

    @UnbindField("_agent_directory")
    def _on_agent_directory_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        self._guards[field].release(service)

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/agents")
        def list_agents() -> dict[str, Any]:
            if self._agent_directory is not None:
                return {"agents": self._agent_directory.list_agents()}
            if self._agent_registry is None:
                raise HTTPException(
                    status_code=503, detail="Agent registry unavailable"
                )
            return {"agents": self._agent_registry.list_agents()}

        @router.get("/agents/{agent_id}/tools")
        def list_agent_tools(agent_id: str) -> dict[str, Any]:
            """Tool names with the template each call reads as.

            Clients render the transcript from this; a tool missing from
            ``tool_headlines`` simply shows as its bare name.
            """
            if self._agent_directory is None:
                raise HTTPException(
                    status_code=503, detail="Agent directory unavailable"
                )
            loop = self._agent_directory.get_loop(agent_id)
            if loop is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown agent: {agent_id}"
                )
            described = loop.describe()
            headlines = described.get("tool_headlines") or {}
            return {
                "tools": [
                    {"name": name, "headline": headlines.get(name, "")}
                    for name in described.get("tools") or []
                ]
            }

        @router.post("/agents")
        def create_agent(payload: AgentCreateRequest = Body(...)) -> dict[str, Any]:
            registry = self._require_registry()
            try:
                agent = registry.create_agent(
                    payload.id, payload.name or payload.id, payload.description
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            self._reload(payload.id)
            return {"agent": agent}

        @router.put("/agents/{agent_id}")
        def update_agent(
            agent_id: str, payload: AgentUpdateRequest = Body(...)
        ) -> dict[str, Any]:
            registry = self._require_registry()
            try:
                agent = registry.update_agent(
                    agent_id,
                    name=payload.name,
                    description=payload.description,
                    enabled=payload.enabled,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            self._reload(agent_id)
            return {"agent": agent}

        @router.delete("/agents/{agent_id}")
        def delete_agent(agent_id: str) -> dict[str, Any]:
            registry = self._require_registry()
            try:
                registry.delete_agent(agent_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if self._agent_directory is not None:
                self._agent_directory.remove_agent(agent_id)
            return {"agent_id": agent_id, "deleted": True}

        return router

    def _require_registry(self) -> Any:
        if self._agent_registry is None:
            raise HTTPException(status_code=503, detail="Agent registry unavailable")
        return self._agent_registry

    def _reload(self, agent_id: str) -> None:
        if self._agent_directory is not None:
            self._agent_directory.reload(agent_id)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
