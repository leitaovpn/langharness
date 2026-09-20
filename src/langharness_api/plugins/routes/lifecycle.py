"""Client lifecycle WebSocket route plugin."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_api.contracts import RouteProvider


@ComponentFactory("api-lifecycle-route-factory")
@Provides(RouteProvider)
@Property("_plugin_name", "plugin.name", "lifecycle")
@Property("_plugin_version", "plugin.version", "1.0.0")
class LifecycleRoutePlugin:
    """Serves the client lifeline websocket used for auto-shutdown."""

    def __init__(self) -> None:
        self._plugin_name = "lifecycle"
        self._plugin_version = "1.0.0"

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.websocket("/clients/attach")
        async def attach(websocket: WebSocket) -> None:
            registry = getattr(websocket.app.state, "client_registry", None)
            await websocket.accept()
            if registry is not None:
                registry.attach(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                if registry is not None:
                    registry.detach(websocket)

        return router

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
