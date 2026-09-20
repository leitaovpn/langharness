"""Unit tests for the server process service shutdown wiring."""
# mypy: ignore-errors

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

import langharness_api.plugins.server.runtime as runtime_module
from langharness_api.common.client_registry import ClientRegistry
from langharness_api.plugins.server.app import _lifespan
from langharness_api.plugins.server.runtime import ServerServerService


class FakeApi:
    def build_app(self) -> FastAPI:
        return FastAPI()


class FakeUvicornServer:
    def __init__(self, config: SimpleNamespace) -> None:
        self.config = config
        self.should_exit = False
        self.ran = False

    def run(self) -> None:
        self.ran = True


@pytest.fixture
def faked_uvicorn(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeUvicornServer, list]:
    fake_server = FakeUvicornServer(SimpleNamespace())
    configs: list = []

    def fake_config(app, **kwargs):
        configs.append((app, kwargs))
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(runtime_module.uvicorn, "Config", fake_config)
    monkeypatch.setattr(
        runtime_module.uvicorn, "Server", lambda config: fake_server
    )
    return fake_server, configs


@pytest.mark.asyncio
async def test_server_wires_auto_shutdown(
    faked_uvicorn: tuple[FakeUvicornServer, list],
) -> None:
    fake_server, configs = faked_uvicorn
    service = ServerServerService()
    service._api = FakeApi()

    service.server("127.0.0.1", 19000, auto_shutdown=True, grace=0.01)

    assert fake_server.ran is True
    app, kwargs = configs[0]
    assert isinstance(app, FastAPI)
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 19000,
        "log_config": None,
        "timeout_graceful_shutdown": 10,
    }
    registry = app.state.client_registry
    registry.mark_ready()
    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert fake_server.should_exit is True


@pytest.mark.asyncio
async def test_server_without_auto_shutdown_never_exits(
    faked_uvicorn: tuple[FakeUvicornServer, list],
) -> None:
    fake_server, configs = faked_uvicorn
    service = ServerServerService()
    service._api = FakeApi()

    service.server("127.0.0.1", 19000)

    app = configs[0][0]
    registry = app.state.client_registry
    registry.mark_ready()
    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert fake_server.should_exit is False


@pytest.mark.asyncio
async def test_lifespan_marks_registry_ready() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))

    app = FastAPI()
    app.state.client_registry = registry
    async with _lifespan(app):
        await asyncio.sleep(0.05)  # empty at mark_ready -> one shutdown
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_lifespan_without_registry_is_a_noop() -> None:
    async with _lifespan(FastAPI()):
        pass


@pytest.mark.asyncio
async def test_server_registers_auth_guarded_lifeline(
    faked_uvicorn: tuple[FakeUvicornServer, list],
) -> None:
    fake_server, configs = faked_uvicorn
    service = ServerServerService()
    service._api = FakeApi()
    service._auth = SimpleNamespace(
        get_websocket_dependency=lambda: (lambda websocket: None)
    )

    service.server("127.0.0.1", 19000)

    app = configs[0][0]
    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/clients/attach" in paths
    assert fake_server.ran is True


def test_auth_bind_and_unbind_guard_glue() -> None:
    from langharness_api.plugins.auth.auth import AuthPlugin

    service = ServerServerService()
    auth = AuthPlugin()
    service._on_auth_bind("_auth", auth, None)
    service._on_unbind("_auth", auth, None)


def test_agent_bind_rejects_nonconforming_service() -> None:
    service = ServerServerService()
    service._on_agent_bind("_agent", SimpleNamespace(), None)
    assert service._agent is None


def test_api_bind_glue_and_agent_bind_success() -> None:
    def list_agents() -> list[dict[str, Any]]:
        return []

    def get_loop(agent_id: str) -> Any | None:
        return None

    def reload(agent_id: str | None = None) -> None:
        pass

    def replace_loop_package(package_id: str, contribution_id: str) -> None:
        pass

    service = ServerServerService()
    service._on_bind("_api", FakeApi(), None)
    agent = SimpleNamespace(
        list_agents=list_agents,
        get_loop=get_loop,
        reload=reload,
        replace_loop_package=replace_loop_package,
    )
    service._on_agent_bind("_agent", agent, None)
    assert service._agent is agent
    service._on_unbind("_agent", agent, None)
    service._on_unbind("_api", None, None)


def test_server_requires_api_service() -> None:
    with pytest.raises(RuntimeError, match="unavailable"):
        ServerServerService().server("127.0.0.1", 1)
