"""FastAPI server component assembled from plugin services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Provides,
    Requires,
    RequiresBest,
    UnbindField,
)

from langharness_api.contracts import (
    APIServerProvider,
    AuthProvider,
    DBProvider,
    RateLimitProvider,
    RouteProvider,
)
from langharness_config.contracts import Configs
from langharness_core.common.dependencies import get_db_session
from langharness_logging.contracts import LogProvider
from langharness_plugin.validation import ContractGuard


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    registry = getattr(app.state, "client_registry", None)
    if registry is not None:
        registry.mark_ready()
    yield


@ComponentFactory("api-server-factory")
@Provides(APIServerProvider)
@Requires("_route_providers", RouteProvider, aggregate=True, optional=True)
@RequiresBest("_auth_provider", AuthProvider, optional=True, immediate_rebind=True)
@RequiresBest(
    "_rate_limit_provider", RateLimitProvider, optional=True, immediate_rebind=True
)
@RequiresBest("_db_provider", DBProvider, optional=True, immediate_rebind=True)
@RequiresBest("_configs", Configs, optional=True, immediate_rebind=True)
@RequiresBest("_log_provider", LogProvider, optional=True, immediate_rebind=True)
class APIServerService:
    """Builds a FastAPI app from the currently injected plugins."""

    def __init__(self) -> None:
        self._route_providers: list[Any] = []
        self._auth_provider: Any = None
        self._rate_limit_provider: Any = None
        self._db_provider: Any = None
        self._configs: Any = None
        self._log_provider: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_route_providers": ContractGuard(self, "_route_providers", RouteProvider),
            "_auth_provider": ContractGuard(self, "_auth_provider", AuthProvider),
            "_rate_limit_provider": ContractGuard(
                self, "_rate_limit_provider", RateLimitProvider
            ),
            "_db_provider": ContractGuard(self, "_db_provider", DBProvider),
            "_configs": ContractGuard(self, "_configs", Configs),
            "_log_provider": ContractGuard(self, "_log_provider", LogProvider),
        }

    @BindField("_route_providers", if_valid=True)
    def _on_route_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._current_app = self.build_app()

    @UnbindField("_route_providers", if_valid=True)
    def _on_route_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._current_app = self.build_app()

    @BindField("_auth_provider", if_valid=True)
    def _on_auth_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._current_app = self.build_app()

    @UnbindField("_auth_provider")
    def _on_auth_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._current_app = self.build_app()

    @BindField("_rate_limit_provider", if_valid=True)
    def _on_rate_limit_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._current_app = self.build_app()

    @UnbindField("_rate_limit_provider")
    def _on_rate_limit_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._current_app = self.build_app()

    @BindField("_db_provider", if_valid=True)
    def _on_db_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._current_app = self.build_app()

    @UnbindField("_db_provider")
    def _on_db_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._current_app = self.build_app()

    @BindField("_configs", if_valid=True)
    def _on_configs_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._current_app = self.build_app()

    @UnbindField("_configs")
    def _on_configs_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._current_app = self.build_app()

    @BindField("_log_provider", if_valid=True)
    def _on_log_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._current_app = self.build_app()

    @UnbindField("_log_provider")
    def _on_log_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._current_app = self.build_app()

    def build_app(self) -> FastAPI:
        app = FastAPI(
            title="langharness_api", version="0.1.0", lifespan=_lifespan
        )
        app.state.configs = self._configs
        app.state.log = self._log_provider
        if self._log_provider is not None:
            self._log_provider.get_logger().info("API server app built")

        if self._db_provider is not None:
            app.dependency_overrides[get_db_session] = (
                self._db_provider.get_session_dependency()
            )

        dependencies: list[Any] = []
        if self._auth_provider is not None:
            dependencies.append(Depends(self._auth_provider.get_auth_dependency()))
        if self._rate_limit_provider is not None:
            dependencies.append(
                Depends(self._rate_limit_provider.get_rate_limit_dependency())
            )

        for provider in self._route_providers or []:
            app.include_router(provider.get_router(), dependencies=dependencies)

        return app
