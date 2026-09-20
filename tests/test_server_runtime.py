"""Unit tests for the server process service shutdown wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
