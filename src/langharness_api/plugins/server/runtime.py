"""Top-level server process service."""

from __future__ import annotations

from typing import Any

import uvicorn
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Provides,
    RequiresBest,
    UnbindField,
)

from langharness_api.common.client_registry import ClientRegistry
from langharness_api.contracts import APIServerProvider, ServerServerProvider
from langharness_core.contracts import AgentServerProvider
from langharness_plugin.validation import ContractGuard


@ComponentFactory("server-server-factory")
@Provides(ServerServerProvider)
@RequiresBest("_api", APIServerProvider, optional=True, immediate_rebind=True)
@RequiresBest("_agent", AgentServerProvider, optional=True, immediate_rebind=True)
class ServerServerService:
    def __init__(self) -> None:
        self._api: Any = None
        self._agent: Any = None
        self._guards = {
            "_api": ContractGuard(self, "_api", APIServerProvider),
            "_agent": ContractGuard(self, "_agent", AgentServerProvider),
        }

    @BindField("_api", if_valid=True)
    def _on_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @BindField("_agent", if_valid=True)
    def _on_agent_bind(self, field: str, service: Any, reference: Any) -> None:
        if self._guards[field].admit(service):
            self.set_agent(service)

    @UnbindField("_api")
    @UnbindField("_agent")
    def _on_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    def server(
        self,
        host: str,
        port: int,
        auto_shutdown: bool = False,
        grace: float = 10.0,
    ) -> None:
        if self._api is None:
            raise RuntimeError("API server service is unavailable")
        app = self._api.build_app()
        app.state.agent = self._agent
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=None,
            timeout_graceful_shutdown=10,
        )
        server = uvicorn.Server(config)
        registry = ClientRegistry(grace=grace)
        registry.set_enabled(auto_shutdown)
        registry.set_shutdown_callback(
            lambda: setattr(server, "should_exit", True)
        )
        app.state.client_registry = registry
        server.run()

