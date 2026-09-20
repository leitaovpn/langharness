"""Tests for the four module-boundary services."""
# mypy: ignore-errors

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from langharness_api.contracts import (
    SPEC_SERVER_SERVER,
    SPEC_UI_SDK,
    ServerServerProvider,
    UISdkProvider,
)
from langharness_api.plugins.sdk.http import HttpUISdk
from langharness_api.plugins.server.runtime import ServerServerService
from langharness_api.sdk import package as sdk_package
from langharness_cli.contracts import SPEC_UI_SERVER, UIServerProvider
from langharness_cli.plugins.server import UIServerService
from langharness_core.contracts import SPEC_AGENT_SERVER, AgentServerProvider
from langharness_core.plugins.agents.server import AgentServerService


def test_module_specifications_and_runtime_protocols() -> None:
    assert SPEC_UI_SERVER == "ui.server"
    assert SPEC_SERVER_SERVER == "server.server"
    assert SPEC_AGENT_SERVER == "agent.server"
    assert SPEC_UI_SDK == "ui.server_sdk"
    assert isinstance(UIServerService(), UIServerProvider)
    assert isinstance(ServerServerService(), ServerServerProvider)
    assert isinstance(AgentServerService(), AgentServerProvider)
    assert isinstance(HttpUISdk(), UISdkProvider)
    assert sdk_package().id == "builtin.api.sdk"


def test_ui_server_forwards_unified_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import langharness_cli.common.cli as cli_module

    captured = {}
    monkeypatch.setattr(
        cli_module,
        "main",
        lambda argv, descriptors=None, manager=None, base_url=None: captured.update(
            argv=argv,
            descriptors=descriptors,
            manager=manager,
            base_url=base_url,
        )
        or 7,
    )
    descriptors = [object()]

    result = UIServerService().run(
        {
            "argv": ["interactive"],
            "config_dir": "/tmp/demo",
            "base_url": "http://127.0.0.1:11534",
            "descriptors": descriptors,
        }
    )

    assert result == 7
    assert captured == {
        "argv": ["--dir", "/tmp/demo", "interactive"],
        "descriptors": descriptors,
        "manager": None,
        "base_url": "http://127.0.0.1:11534",
    }

    captured.clear()
    assert UIServerService().run(
        {
            "argv": ["shell"],
            "config_dir": "/tmp/demo",
            "base_url": "http://127.0.0.1:11534",
        }
    ) == 7
    assert "--base-url" not in captured["argv"]
    assert captured["base_url"] == "http://127.0.0.1:11534"


def test_server_service_builds_app_and_exposes_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    import langharness_api.plugins.server.runtime as runtime_module

    app = FastAPI()
    configs = []
    ran = []

    class FakeUvicornServer:
        def __init__(self, config) -> None:
            self.should_exit = False

        def run(self) -> None:
            ran.append(1)

    monkeypatch.setattr(
        runtime_module.uvicorn,
        "Config",
        lambda target, **kwargs: configs.append((target, kwargs)),
    )
    monkeypatch.setattr(
        runtime_module.uvicorn,
        "Server",
        lambda config: FakeUvicornServer(config),
    )

    service = ServerServerService()
    service._api = SimpleNamespace(build_app=lambda: app)
    agent = object()
    service.set_agent(agent)

    service.server("0.0.0.0", 9123)

    assert app.state.agent is agent
    target, kwargs = configs[0]
    assert target is app
    assert kwargs == {
        "host": "0.0.0.0",
        "port": 9123,
        "log_config": None,
        "timeout_graceful_shutdown": 10,
    }
    assert app.state.client_registry is not None
    assert ran == [1]
    service._api = None
    with pytest.raises(RuntimeError, match="unavailable"):
        service.server("127.0.0.1", 1)


def test_agent_server_delegates_and_reloads_after_replacement() -> None:
    calls = []
    loop = object()
    directory = SimpleNamespace(
        list_agents=lambda: [{"id": "a"}],
        get_loop=lambda agent_id: loop if agent_id == "a" else None,
        reload=lambda agent_id=None: calls.append(("reload", agent_id)),
    )
    plugins = SimpleNamespace(
        install=lambda package_id, contribution_id: calls.append(
            ("install", package_id, contribution_id)
        )
    )
    service = AgentServerService()
    service._directory = directory
    service._plugins = plugins

    assert service.list_agents() == [{"id": "a"}]
    assert service.get_loop("a") is loop
    service.reload("a")
    service.replace_loop_package("demo", "loop")
    assert calls == [
        ("reload", "a"),
        ("install", "demo", "loop"),
        ("reload", None),
    ]

    service._directory = None
    with pytest.raises(RuntimeError, match="directory"):
        service.list_agents()
    service._directory = directory
    service._plugins = None
    with pytest.raises(RuntimeError, match="plugin manager"):
        service.replace_loop_package("demo", "loop")


def test_http_sdk_health_get_and_put(monkeypatch: pytest.MonkeyPatch) -> None:
    import langharness_api.plugins.sdk.http as sdk_module

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    calls = []
    monkeypatch.setattr(
        sdk_module.httpx,
        "get",
        lambda url, **kwargs: calls.append(("get", url, kwargs)) or Response(),
    )
    monkeypatch.setattr(
        sdk_module.httpx,
        "put",
        lambda url, **kwargs: calls.append(("put", url, kwargs)) or Response(),
    )
    sdk = HttpUISdk()
    assert sdk.health() is True
    assert sdk.get("sessions") == {"ok": True}
    assert sdk.put("plugins", {"enabled": True}) == {"ok": True}
    assert calls[1][2]["headers"] == {"Authorization": "Bearer secret"}

    monkeypatch.setattr(
        sdk_module.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("no")),
    )
    assert sdk.health() is False


@pytest.mark.asyncio
async def test_http_sdk_streams_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    import langharness_api.plugins.sdk.http as sdk_module

    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in ('{"type":"assistant"}', ""):
                yield line

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return StreamResponse()

    monkeypatch.setattr(sdk_module.httpx, "AsyncClient", Client)
    events = [event async for event in HttpUISdk().stream({"input": "hi"})]
    assert events == [{"type": "assistant"}]
