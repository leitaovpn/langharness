"""Bearer token authentication plugin."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, WebSocket
from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_api.contracts import AuthProvider


@ComponentFactory("api-auth-plugin-factory")
@Provides(AuthProvider)
@Property("_plugin_name", "plugin.name", "auth")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_token", "plugin.token", "secret")
class AuthPlugin:
    def __init__(self) -> None:
        self._plugin_name = "auth"
        self._plugin_version = "1.0.0"
        self._token = "secret"

    def get_auth_dependency(self) -> Callable[[Request], Any]:
        def dependency(request: Request) -> str:
            authorization = request.headers.get("authorization", "")
            if authorization != f"Bearer {self._token}":
                raise HTTPException(status_code=401, detail="Unauthorized")
            return self._token

        return dependency

    def get_websocket_dependency(self) -> Callable[[WebSocket], Any]:
        def dependency(websocket: WebSocket) -> str:
            authorization = websocket.headers.get("authorization", "")
            if authorization != f"Bearer {self._token}":
                raise HTTPException(status_code=401, detail="Unauthorized")
            return self._token

        return dependency

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
