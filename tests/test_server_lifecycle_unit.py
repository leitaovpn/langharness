"""Unit tests for the client lifecycle websocket route."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from langharness_api.common.client_registry import ClientRegistry
from langharness_api.plugins.routes.lifecycle import LifecycleRoutePlugin


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_attach_websocket_registers_client() -> None:
    registry = ClientRegistry()
    app = FastAPI()
    app.state.client_registry = registry
    app.include_router(LifecycleRoutePlugin().get_router())
    with TestClient(app) as client:
        assert registry.active_count() == 0
        with client.websocket_connect("/clients/attach"):
            wait_until(lambda: registry.active_count() == 1)
        wait_until(lambda: registry.active_count() == 0)


def test_missing_registry_still_accepts_connection() -> None:
    app = FastAPI()
    app.include_router(LifecycleRoutePlugin().get_router())
    with TestClient(app) as client:
        with client.websocket_connect("/clients/attach"):
            pass
