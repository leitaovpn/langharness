"""Unit tests for the client lifeline websocket and its auth."""

from __future__ import annotations

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from langharness_api.common.client_registry import ClientRegistry
from langharness_api.plugins.auth.auth import AuthPlugin
from langharness_api.plugins.server.runtime import lifeline


def make_client(
    registry: ClientRegistry | None, auth: AuthPlugin | None = None
) -> TestClient:
    app = FastAPI()
    if registry is not None:
        app.state.client_registry = registry
    dependencies = []
    if auth is not None:
        dependencies.append(Depends(auth.get_websocket_dependency()))
    app.add_api_websocket_route(
        "/clients/attach", lifeline, dependencies=dependencies
    )
    return TestClient(app)


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_attach_websocket_registers_client() -> None:
    registry = ClientRegistry()
    with make_client(registry) as client:
        assert registry.active_count() == 0
        with client.websocket_connect("/clients/attach"):
            wait_until(lambda: registry.active_count() == 1)
        wait_until(lambda: registry.active_count() == 0)


def test_missing_registry_still_accepts_connection() -> None:
    with make_client(None) as client:
        with client.websocket_connect("/clients/attach"):
            pass


def test_lifeline_accepts_valid_token() -> None:
    registry = ClientRegistry()
    auth = AuthPlugin()
    headers = {"authorization": f"Bearer {auth._token}"}
    with make_client(registry, auth) as client:
        with client.websocket_connect("/clients/attach", headers=headers):
            wait_until(lambda: registry.active_count() == 1)
        wait_until(lambda: registry.active_count() == 0)


def test_lifeline_rejects_invalid_token() -> None:
    auth = AuthPlugin()
    with make_client(ClientRegistry(), auth) as client:
        with pytest.raises(WebSocketDenialResponse):
            with client.websocket_connect(
                "/clients/attach",
                headers={"authorization": "Bearer wrong"},
            ):
                pass
