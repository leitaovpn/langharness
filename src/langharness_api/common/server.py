"""Standalone API server factory used by uvicorn."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from langharness.bootstrap import (
    DEFAULT_PACKAGE_PATHS,
    _assembly_requests,
    load_package,
)
from langharness_api.contracts import SPEC_API_SERVER
from langharness_core.contracts import SPEC_AGENT_DIRECTORY
from langharness_plugin.config_store import agent_scope_configs
from langharness_plugin.contracts import DynamicPluginManager
from langharness_plugin.coordinator import RuntimeMutationCoordinator
from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.state_store import (
    SqlitePluginHistoryStore,
    SqliteRuntimeStateStore,
)

_MANAGER: PluginManager | None = None


def _apply_agent_configs(manager: PluginManager, directory: str) -> None:
    configs = agent_scope_configs(directory)
    if not configs:
        return
    agent_directory = manager.get_service(SPEC_AGENT_DIRECTORY)
    if agent_directory is None:
        return
    for agent_id, plugins in configs:
        agent_directory.apply_agent_config(agent_id, plugins)


def create_app() -> FastAPI:
    global _MANAGER

    if _MANAGER is None:
        directory = os.environ.get(
            "LANG_HARNESS_DIR", str(Path.home() / ".langharness")
        )
        packages = [
            load_package(
                path, config_dir=directory, section=f"plugins.{role}"
            )
            for role, path in (
                ("config", DEFAULT_PACKAGE_PATHS["config"]),
                ("server", DEFAULT_PACKAGE_PATHS["server"]),
                ("agent", DEFAULT_PACKAGE_PATHS["agent"]),
                ("log", DEFAULT_PACKAGE_PATHS["log"]),
            )
        ]
        requests = _assembly_requests(
            packages,
            config_dir=directory,
            locale="en",
            override_scope="api",
            base_url="http://127.0.0.1:11534",
        )
        manager = PluginManager(PluginRegistry())
        manager.start()
        state_path = Path(directory) / "runtime_state.sqlite3"
        manager.bind_state(
            SqliteRuntimeStateStore(state_path),
            SqlitePluginHistoryStore(state_path),
        )
        coordinator = RuntimeMutationCoordinator(manager, discovery=PluginDiscovery())
        manager.register_runtime_service(DynamicPluginManager, coordinator)
        manager.discover()
        coordinator.rescan()
        coordinator.restore()
        _apply_agent_configs(manager, directory)
        installed: set[tuple[str, str]] = set()
        for request in requests:
            key = (request.descriptor.module, request.descriptor.factory)
            if key not in installed:
                manager.install_descriptor(request.descriptor, source="assembly")
                installed.add(key)
        for request in requests:
            manager.ensure_instance(
                request.descriptor.factory,
                request.descriptor.module,
                request.scope_id,
                properties=request.properties,
                enabled=request.enabled,
            )
        _MANAGER = manager

    assert _MANAGER is not None
    api_server = _MANAGER.get_service(SPEC_API_SERVER)
    assert api_server is not None
    return cast(FastAPI, api_server.build_app())
