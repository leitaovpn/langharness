"""Plugin configuration route: current config, history, and rollback."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel

from langharness_api.common.errors import http_error, operation_error
from langharness_api.contracts import RouteProvider
from langharness_core.contracts import AgentDirectoryProvider
from langharness_plugin.config_store import (
    PluginConfigStore,
    agent_scope_configs,
    scope_path,
)
from langharness_plugin.contracts import DynamicPluginManager, ScopedPluginRegistrar
from langharness_plugin.validation import ContractGuard, is_runtime_scope
from langharness_scope import ScopeId

KNOWN_SCOPES = ("api", "cli")


class PluginsRequest(BaseModel):
    plugins: dict[str, Any] | None = None
    actor: str = "api"


class RollbackRequest(BaseModel):
    seq: int
    actor: str = "api"


class DynamicInstallRequest(BaseModel):
    package_id: str
    contribution_id: str
    scope_id: str


class DynamicPropertiesRequest(BaseModel):
    properties: dict[str, Any]


@ComponentFactory("api-plugins-route-factory")
@Provides(RouteProvider)
@Property("_plugin_name", "plugin.name", "plugins")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_config_dir", "plugin.config_dir", "~/.langharness")
@RequiresBest("_scope", ScopedPluginRegistrar, optional=True, immediate_rebind=True)
@RequiresBest("_dynamic", DynamicPluginManager, optional=True, immediate_rebind=True)
@RequiresBest(
    "_directory", AgentDirectoryProvider, optional=True, immediate_rebind=True
)
class PluginsRoutePlugin:
    """Serves the versioned plugin config and applies changes at runtime."""

    def __init__(self) -> None:
        self._plugin_name = "plugins"
        self._plugin_version = "1.0.0"
        self._config_dir = "~/.langharness"
        self._scope: Any = None
        self._dynamic: Any = None
        self._directory: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_scope": ContractGuard(self, "_scope", ScopedPluginRegistrar),
            "_dynamic": ContractGuard(self, "_dynamic", DynamicPluginManager),
            "_directory": ContractGuard(self, "_directory", AgentDirectoryProvider),
        }

    @BindField("_scope", if_valid=True)
    def _on_scope_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_scope", if_valid=True)
    def _on_scope_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_dynamic", if_valid=True)
    def _on_dynamic_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_dynamic", if_valid=True)
    def _on_dynamic_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_directory", if_valid=True)
    def _on_directory_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_directory", if_valid=True)
    def _on_directory_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/plugins")
        def read_plugins(scope: str | None = Query(None)) -> dict[str, Any]:
            if scope is None:
                configs = []
                for item_scope in self._config_scopes():
                    store = self._store(item_scope)
                    configs.append(
                        {
                            "scope": item_scope,
                            "version": store.current_seq(),
                            "plugins": store.plugins(),
                        }
                    )
                return {"scopes": configs}
            store = self._store(scope)
            return {
                "scope": scope,
                "version": store.current_seq(),
                "plugins": store.plugins(),
            }

        @router.put("/plugins")
        def update_plugins(
            payload: PluginsRequest = Body(...), scope: str = Query(...)
        ) -> dict[str, Any]:
            if payload.plugins is None:
                raise http_error(
                    400,
                    "plugins payload is required",
                    code="VALIDATION_ERROR",
                    error_type="ValidationError",
                )
            store = self._store(scope)
            result = self._apply(scope, payload.plugins)
            version = store.update(payload.plugins, actor=payload.actor)
            return {"scope": scope, "version": version, **result}

        @router.get("/plugins/history")
        def read_history(scope: str = Query(...)) -> dict[str, Any]:
            store = self._store(scope)
            history = [
                {
                    "seq": entry.get("seq"),
                    "ts": entry.get("ts"),
                    "action": entry.get("action"),
                    "actor": entry.get("actor"),
                    "target_seq": entry.get("target_seq"),
                }
                for entry in store.history()
            ]
            return {"scope": scope, "version": store.current_seq(), "history": history}

        @router.post("/plugins/rollback")
        def rollback(
            payload: RollbackRequest = Body(...), scope: str = Query(...)
        ) -> dict[str, Any]:
            store = self._store(scope)
            try:
                target = store.config_of(payload.seq)
            except KeyError as exc:
                raise operation_error(exc, status_code=404) from exc
            result = self._apply(scope, target.get("plugins", {}))
            version = store.rollback(payload.seq, actor=payload.actor)
            return {"scope": scope, "version": version, **result}

        @router.get("/plugins/discovered")
        def discovered_plugins() -> dict[str, Any]:
            dynamic = self._require_dynamic()
            return {
                "packages": [self._package_payload(item) for item in dynamic.discovered()]
            }

        @router.post("/plugins/rescan")
        def rescan_plugins() -> dict[str, Any]:
            result = self._require_dynamic().rescan()
            return {
                "packages": [self._package_payload(item) for item in result.packages],
                "failures": [
                    {
                        "entry_point": item.entry_point,
                        "value": item.value,
                        "detail": item.detail,
                    }
                    for item in result.failures
                ],
            }

        @router.get("/plugins/runtime")
        def runtime_plugins(scope: str | None = Query(None)) -> dict[str, Any]:
            if scope is not None:
                self._validate_runtime_scope(scope)
            registrations = self._require_dynamic().registrations()
            if scope is not None:
                registrations = (
                    item for item in registrations if str(item.scope_id) == scope
                )
            return {
                "plugins": [
                    self._registration_payload(item)
                    for item in registrations
                ]
            }

        @router.post("/plugins/install")
        def install_plugin(payload: DynamicInstallRequest) -> dict[str, Any]:
            try:
                registration = self._require_dynamic().install(
                    payload.package_id,
                    payload.contribution_id,
                    scope_id=payload.scope_id,
                )
            except (KeyError, ValueError, RuntimeError) as exc:
                raise operation_error(exc) from exc
            return self._registration_payload(registration)

        @router.put("/plugins/runtime/{name}/enabled")
        def enable_plugin(
            name: str,
            enabled: bool = Body(..., embed=True),
            scope: str = Query(...),
        ) -> dict[str, Any]:
            self._validate_runtime_scope(scope)
            try:
                registration = self._require_dynamic().set_enabled(
                    name, enabled, scope_id=ScopeId(scope)
                )
            except KeyError as exc:
                raise operation_error(exc, status_code=404) from exc
            except (ValueError, RuntimeError) as exc:
                raise operation_error(exc) from exc
            return self._registration_payload(registration)

        @router.put("/plugins/runtime/{name}/properties")
        def update_runtime_properties(
            name: str,
            payload: DynamicPropertiesRequest,
            scope: str = Query(...),
        ) -> dict[str, Any]:
            self._validate_runtime_scope(scope)
            try:
                registration = self._require_dynamic().update_properties(
                    name, payload.properties, scope_id=ScopeId(scope)
                )
            except KeyError as exc:
                raise operation_error(exc, status_code=404) from exc
            except (ValueError, RuntimeError) as exc:
                raise operation_error(exc) from exc
            return self._registration_payload(registration)

        @router.delete("/plugins/runtime/{name}")
        def uninstall_plugin(name: str, scope: str = Query(...)) -> dict[str, bool]:
            self._validate_runtime_scope(scope)
            try:
                self._require_dynamic().uninstall(name, scope_id=ScopeId(scope))
            except KeyError as exc:
                raise operation_error(exc, status_code=404) from exc
            except (ValueError, RuntimeError) as exc:
                raise operation_error(exc) from exc
            return {"removed": True}

        @router.post("/plugins/runtime/{name}/upgrade")
        def upgrade_plugin(name: str, scope: str = Query(...)) -> dict[str, Any]:
            self._validate_runtime_scope(scope)
            try:
                registration = self._require_dynamic().upgrade(
                    name, scope_id=ScopeId(scope)
                )
            except KeyError as exc:
                raise operation_error(exc, status_code=404) from exc
            except (ValueError, RuntimeError) as exc:
                raise operation_error(exc) from exc
            return self._registration_payload(registration)

        return router

    def _require_dynamic(self) -> Any:
        if self._dynamic is None:
            raise http_error(
                503,
                "Dynamic plugin manager unavailable",
                code="SERVICE_UNAVAILABLE",
                error_type="ServiceUnavailable",
            )
        return self._dynamic

    @staticmethod
    def _package_payload(package: Any) -> dict[str, Any]:
        return {
            "id": package.id,
            "version": package.version,
            "source": "builtin" if package.id.startswith("builtin.") else "external",
            "contributions": [
                {
                    "id": item.id,
                    "name": item.descriptor.name,
                    "target": item.target,
                    "module": item.descriptor.module,
                    "factory": item.descriptor.factory,
                    "specification": item.descriptor.specification,
                    "description": item.descriptor.description,
                }
                for item in package.contributions
            ],
        }

    @staticmethod
    def _registration_payload(registration: Any) -> dict[str, Any]:
        return {
            "package_id": registration.package_id,
            "contribution_id": registration.contribution_id,
            "package_version": registration.package_version,
            "instance": registration.instance,
            "factory": registration.factory,
            "module": registration.module,
            "scope_id": str(registration.scope_id),
            "enabled": registration.enabled,
            "status": registration.status,
        }

    def _store(self, scope: str) -> PluginConfigStore:
        self._validate_scope(scope)
        return PluginConfigStore.load(scope_path(self._config_dir, scope), scope)

    def _config_scopes(self) -> tuple[str, ...]:
        agent_scopes = tuple(
            f"agent:{agent_id}" for agent_id, _ in agent_scope_configs(self._config_dir)
        )
        return ("api", "cli", *agent_scopes)

    def _validate_scope(self, scope: str) -> None:
        self._validate_known_scope(scope, KNOWN_SCOPES, "scope")

    def _validate_runtime_scope(self, scope: str) -> None:
        if is_runtime_scope(scope):
            return
        raise http_error(
            400,
            f"Unknown runtime scope: {scope}",
            code="VALIDATION_ERROR",
            error_type="ValidationError",
        )

    def _validate_known_scope(
        self, scope: str, known: tuple[str, ...], label: str
    ) -> None:
        if scope in known:
            return
        if scope.startswith("agent:") and len(scope) > len("agent:"):
            return
        raise http_error(
            400,
            f"Unknown {label}: {scope}",
            code="VALIDATION_ERROR",
            error_type="ValidationError",
        )

    def _apply(self, scope: str, plugins: dict[str, Any]) -> dict[str, list[str]]:
        if scope == "api":
            if self._scope is None:
                raise http_error(
                    503,
                    "Plugin scope service unavailable",
                    code="SERVICE_UNAVAILABLE",
                    error_type="ServiceUnavailable",
                )
            try:
                result: dict[str, list[str]] = self._scope.apply_config(plugins)
                return result
            except ValueError as exc:
                raise operation_error(exc) from exc
        if scope.startswith("agent:"):
            if self._directory is None:
                raise http_error(
                    503,
                    "Agent directory unavailable",
                    code="SERVICE_UNAVAILABLE",
                    error_type="ServiceUnavailable",
                )
            agent_id = scope.split(":", 1)[1]
            try:
                self._directory.apply_agent_config(agent_id, plugins)
            except ValueError as exc:
                raise operation_error(exc) from exc
            return {"applied": sorted(plugins), "restart_required": []}
        # The CLI process owns its plugin set, so it picks changes up on restart.
        return {"applied": [], "restart_required": sorted(plugins)}

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
