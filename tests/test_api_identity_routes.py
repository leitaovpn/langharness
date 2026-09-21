"""Tests for the session and agent route plugins."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from langharness_api.plugins.routes.agents import AgentsRoutePlugin
from langharness_api.plugins.routes.sessions import SessionsRoutePlugin

SESSION = {
    "user_id": "local_user",
    "session_id": "s1",
    "created_at": "2026-09-15T00:00:00+00:00",
    "last_used_at": "2026-09-15T00:00:00+00:00",
    "turns": 2,
    "last_agent_id": "simple_agent",
    "agents_used": ["simple_agent"],
}


class FakeSessionIndex:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def new_session(self, user_id: str) -> str:
        return "generated"

    async def touch(self, user_id: str, session_id: str, agent_id: str) -> None:
        return None

    async def get_session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return SESSION if session_id == "s1" else None

    async def list_sessions(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self.calls.append((user_id, limit))
        return [SESSION] if user_id == "local_user" else []


def make_client(plugin: Any) -> TestClient:
    app = FastAPI()
    app.include_router(plugin.get_router())
    return TestClient(app)


def test_sessions_route_lists_user_sessions() -> None:
    plugin = SessionsRoutePlugin()
    index = FakeSessionIndex()
    plugin._session_index = index

    response = make_client(plugin).get("/sessions", params={"user_id": "local_user"})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "local_user"
    assert body["sessions"] == [SESSION]
    assert index.calls == [("local_user", 50)]


def test_sessions_route_forwards_limit() -> None:
    plugin = SessionsRoutePlugin()
    index = FakeSessionIndex()
    plugin._session_index = index

    response = make_client(plugin).get(
        "/sessions", params={"user_id": "local_user", "limit": 5}
    )

    assert response.status_code == 200
    assert index.calls == [("local_user", 5)]


def test_sessions_route_rejects_invalid_user() -> None:
    plugin = SessionsRoutePlugin()
    plugin._session_index = FakeSessionIndex()

    response = make_client(plugin).get("/sessions", params={"user_id": "bad user"})

    assert response.status_code == 400
    assert "user_id" in response.json()["detail"]


def test_sessions_route_requires_user_id() -> None:
    plugin = SessionsRoutePlugin()
    plugin._session_index = FakeSessionIndex()
    assert make_client(plugin).get("/sessions").status_code == 422


def test_sessions_route_reports_missing_index() -> None:
    response = make_client(SessionsRoutePlugin()).get(
        "/sessions", params={"user_id": "local_user"}
    )
    assert response.status_code == 503


def test_sessions_route_plugin_info() -> None:
    plugin = SessionsRoutePlugin()
    assert plugin.get_plugin_info() == {"name": "sessions", "version": "1.0.0"}


class FakeAgentRegistry:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "simple_agent",
                "name": "Simple Agent",
                "description": "Built-in agent.",
                "enabled": True,
                "created_at": "2026-09-15T00:00:00+00:00",
                "updated_at": "2026-09-15T00:00:00+00:00",
            }
        ]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self.list_agents()[0] if agent_id == "simple_agent" else None

    def create_agent(self, agent_id: str, name: str, description: str) -> dict[str, Any]:
        if agent_id == "simple_agent":
            raise ValueError(f"Agent already exists: {agent_id}")
        self.created.append((agent_id, name, description))
        return {"id": agent_id, "name": name, "description": description}

    def update_agent(self, agent_id: str, **fields: Any) -> dict[str, Any]:
        if agent_id != "simple_agent":
            raise KeyError(f"Unknown agent: {agent_id}")
        self.updated.append((agent_id, fields))
        return {"id": agent_id, **fields}

    def delete_agent(self, agent_id: str) -> None:
        if agent_id != "simple_agent":
            raise KeyError(f"Unknown agent: {agent_id}")
        self.deleted.append(agent_id)


def test_agents_route_lists_agents() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_registry = FakeAgentRegistry()

    response = make_client(plugin).get("/agents")

    assert response.status_code == 200
    agents = response.json()["agents"]
    assert [agent["id"] for agent in agents] == ["simple_agent"]
    assert agents[0]["description"]


class FakeDirectory:
    def __init__(self) -> None:
        self.reloaded: list[str] = []
        self.removed: list[str] = []

    def list_agents(self) -> list[dict[str, Any]]:
        entry = FakeAgentRegistry().list_agents()[0]
        entry["materialized"] = True
        entry["plugins"] = ["name", "tools"]
        return [entry]

    def get_loop(self, agent_id: str) -> Any:
        return None

    def ensure_plugin_instance(
        self, agent_id: str, plugin: str, properties: dict[str, Any]
    ) -> None:
        return None

    def reload(self, agent_id: str | None = None) -> None:
        if agent_id is not None:
            self.reloaded.append(agent_id)

    def apply_agent_config(self, agent_id: str, plugins: dict[str, Any]) -> None:
        return None

    def binding_properties(self, agent_id: str, plugin: str) -> dict[str, Any]:
        return {}

    def remove_agent(self, agent_id: str) -> None:
        self.removed.append(agent_id)


def test_agents_route_prefers_directory_when_available() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_registry = FakeAgentRegistry()
    plugin._agent_directory = FakeDirectory()

    agents = make_client(plugin).get("/agents").json()["agents"]

    assert agents[0]["materialized"] is True
    assert agents[0]["plugins"] == ["name", "tools"]


def test_agents_route_creates_and_reloads_agent() -> None:
    plugin = AgentsRoutePlugin()
    registry = FakeAgentRegistry()
    directory = FakeDirectory()
    plugin._agent_registry = registry
    plugin._agent_directory = directory

    response = make_client(plugin).post(
        "/agents",
        json={"id": "researcher", "name": "Researcher", "description": "Reads"},
    )

    assert response.status_code == 200
    assert response.json()["agent"]["id"] == "researcher"
    assert registry.created == [("researcher", "Researcher", "Reads")]
    assert directory.reloaded == ["researcher"]


def test_agents_route_rejects_duplicate_agent() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_registry = FakeAgentRegistry()
    plugin._agent_directory = FakeDirectory()

    response = make_client(plugin).post(
        "/agents", json={"id": "simple_agent", "name": "Again", "description": ""}
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_agents_route_updates_agent() -> None:
    plugin = AgentsRoutePlugin()
    registry = FakeAgentRegistry()
    directory = FakeDirectory()
    plugin._agent_registry = registry
    plugin._agent_directory = directory

    response = make_client(plugin).put(
        "/agents/simple_agent", json={"enabled": False}
    )

    assert response.status_code == 200
    assert registry.updated == [
        ("simple_agent", {"name": None, "description": None, "enabled": False})
    ]
    assert directory.reloaded == ["simple_agent"]


def test_agents_route_update_reports_unknown_agent() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_registry = FakeAgentRegistry()
    plugin._agent_directory = FakeDirectory()
    response = make_client(plugin).put("/agents/ghost", json={"enabled": False})
    assert response.status_code == 404


def test_agents_route_deletes_agent() -> None:
    plugin = AgentsRoutePlugin()
    registry = FakeAgentRegistry()
    directory = FakeDirectory()
    plugin._agent_registry = registry
    plugin._agent_directory = directory

    response = make_client(plugin).delete("/agents/simple_agent")

    assert response.status_code == 200
    assert registry.deleted == ["simple_agent"]
    assert directory.removed == ["simple_agent"]


def test_agents_route_requires_registry_for_crud() -> None:
    plugin = AgentsRoutePlugin()
    assert (
        make_client(plugin).post(
            "/agents", json={"id": "x", "name": "x", "description": ""}
        ).status_code
        == 503
    )


def test_agents_route_reports_missing_registry() -> None:
    assert make_client(AgentsRoutePlugin()).get("/agents").status_code == 503


def test_agents_route_plugin_info() -> None:
    plugin = AgentsRoutePlugin()
    assert plugin.get_plugin_info() == {"name": "agents", "version": "1.0.0"}


class FakeLoop:
    def describe(self) -> dict[str, Any]:
        return {"tools": ["bash"], "tool_headlines": {"bash": "Bash({commands})"}}


class FakeLoopDirectory(FakeDirectory):
    def get_loop(self, agent_id: str) -> Any:
        return FakeLoop() if agent_id == "simple_agent" else None


def test_agents_route_lists_tool_templates() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_directory = FakeLoopDirectory()

    response = make_client(plugin).get("/agents/simple_agent/tools")

    assert response.status_code == 200
    assert response.json()["tools"] == [
        {"name": "bash", "headline": "Bash({commands})"}
    ]


def test_agents_route_reports_an_unknown_agent() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_directory = FakeLoopDirectory()

    response = make_client(plugin).get("/agents/nobody/tools")

    # The detail distinguishes this from the framework's own 404 for a route
    # that does not exist, which is what an unregistered path would give.
    assert response.status_code == 404
    assert "nobody" in response.json()["detail"]


def test_agents_route_reports_a_missing_directory() -> None:
    plugin = AgentsRoutePlugin()

    assert make_client(plugin).get("/agents/simple_agent/tools").status_code == 503
