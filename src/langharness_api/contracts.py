"""Public service specifications for the API server plugins."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, FastAPI, Request, WebSocket

from langharness_plugin.validation import service_contract

SPEC_API_SERVER = "api.server"
SPEC_AUTH = "api.plugin.auth"
SPEC_RATE_LIMIT = "api.plugin.rate_limit"
SPEC_DB = "api.plugin.db"
SPEC_ROUTE = "api.plugin.route"
SPEC_SERVER_SERVER = "server.server"
SPEC_UI_SDK = "ui.server_sdk"


@service_contract(SPEC_ROUTE)
@runtime_checkable
class RouteProvider(Protocol):
    def get_router(self) -> APIRouter: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_AUTH)
@runtime_checkable
class AuthProvider(Protocol):
    def get_auth_dependency(self) -> Callable[[Request], Any]: ...

    def get_websocket_dependency(self) -> Callable[[WebSocket], Any]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_RATE_LIMIT)
@runtime_checkable
class RateLimitProvider(Protocol):
    def get_rate_limit_dependency(self) -> Callable[[Request], None]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_DB)
@runtime_checkable
class DBProvider(Protocol):
    def get_session_dependency(self) -> Callable[[], Any]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_API_SERVER)
@runtime_checkable
class APIServerProvider(Protocol):
    """Contract implemented by every ``api.server`` service."""

    def build_app(self) -> FastAPI: ...


@service_contract(SPEC_SERVER_SERVER)
@runtime_checkable
class ServerServerProvider(Protocol):
    def set_agent(self, agent: Any) -> None: ...

    def server(
        self,
        host: str,
        port: int,
        auto_shutdown: bool = False,
        grace: float = 10.0,
    ) -> None: ...


@service_contract(SPEC_UI_SDK)
@runtime_checkable
class UISdkProvider(Protocol):
    def health(self) -> bool: ...

    def get(self, path: str) -> Mapping[str, Any]: ...

    def put(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def stream(self, payload: Mapping[str, Any]) -> AsyncIterator[Mapping[str, Any]]: ...
    def resume(self, payload: Mapping[str, Any]) -> AsyncIterator[Mapping[str, Any]]: ...
