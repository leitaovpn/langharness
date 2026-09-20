"""Configuration-first assembly for UI and server process modes."""

from __future__ import annotations

import logging
import os
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, cast

from langharness.api_guard import APIGuard
from langharness_api.contracts import SPEC_SERVER_SERVER, ServerServerProvider
from langharness_cli.contracts import SPEC_UI_SERVER, UIServerProvider
from langharness_config.contracts import SPEC_CONFIGS, Configs
from langharness_plugin.config_store import load_overrides, merge_overrides
from langharness_plugin.contracts import DynamicPluginManager
from langharness_plugin.coordinator import RuntimeMutationCoordinator
from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.package import PluginPackage
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import (
    AGENT_SCOPE_ID,
    SERVER_SCOPE_ID,
    UI_SCOPE_ID,
)
from langharness_plugin.state_store import (
    SqlitePluginHistoryStore,
    SqliteRuntimeStateStore,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

LOGGER = logging.getLogger("langharness.bootstrap")
CONFIG_ENTRY_POINT_GROUP = "langharness.config"
DEFAULT_CONFIG_DIR = str(Path.home() / ".langharness")
DEFAULT_PACKAGE_PATHS = {
    "config": "langharness_config.plugin:builtin_package",
    "ui": "langharness_cli.plugin:builtin_package",
    "sdk": "langharness_api.sdk:package",
    "server": "langharness_api.plugin:builtin_package",
    "agent": "langharness_core.plugin:builtin_package",
    "log": "langharness_logging.plugin:builtin_package",
}

TARGET_SCOPES = {
    "root": ROOT_SCOPE_ID,
    "ui": UI_SCOPE_ID,
    "server": SERVER_SCOPE_ID,
    "agent": AGENT_SCOPE_ID,
}

UI_LOCALE_NAMES = {
    "ui-server",
    "cli-health",
    "cli-model",
    "cli-rich-renderer",
    "cli-session",
    "cli-plugins",
    "cli-scope",
    "cli-shell",
}

LOG_NAMES = {"server-log", "cli-log", "api-log"}

PATH_PROPERTIES = {
    "sqlite-checkpointer": (
        "plugin.checkpoint.path",
        "langharness_checkpoints.sqlite3",
    ),
    "session-index": ("plugin.sessions.path", "sessions.sqlite3"),
    "agent-registry": ("plugin.agents.path", "agents.json"),
}

API_DEFAULTS: dict[str, dict[str, Any]] = {
    "api-auth": {"plugin.token": "secret"},
    "api-rate-limit": {"plugin.limit": 100},
}


class BootstrapError(RuntimeError):
    """Raised when configured module assembly cannot be loaded."""


@dataclass(frozen=True, slots=True)
class AssemblyRequest:
    """One builtin definition to install plus one instance to reconcile."""

    name: str  # contribution id: stable identity for config keys
    descriptor: PluginDescriptor
    scope_id: ScopeId
    properties: dict[str, Any]
    enabled: bool = True


def parse_options(argv: list[str]) -> tuple[Namespace, list[str]]:
    parser = ArgumentParser(description="LangHarmess")
    parser.add_argument("--mode", choices=("ui", "all", "server"), default="all")
    parser.add_argument("--server-ip", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=11534)
    parser.add_argument(
        "--config-dir",
        "--config_dir",
        "--dir",
        dest="config_dir",
        default=os.environ.get("LANG_HARNESS_DIR", DEFAULT_CONFIG_DIR),
    )
    return parser.parse_known_args(argv)


def load_package(path: str, *, config_dir: str, section: str) -> PluginPackage:
    """Load and validate one configured ``module:factory`` package path."""
    try:
        module_name, separator, attribute = path.partition(":")
        if not separator or not module_name or not attribute:
            raise ValueError("expected module:attr")
        factory = getattr(import_module(module_name), attribute)
        if not callable(factory):
            raise TypeError("target is not callable")
        package = factory()
        if not isinstance(package, PluginPackage):
            raise TypeError("factory did not return PluginPackage")
        return package
    except Exception as exc:
        raise BootstrapError(
            f"Cannot load {section} from {path!r} using config-dir "
            f"{config_dir!r}: {exc}"
        ) from exc


def selected_package_paths(configs: Configs, *, mode: str) -> dict[str, str]:
    roles = ("ui", "sdk", "log") if mode in {"ui", "all"} else (
        "server",
        "agent",
        "log",
    )
    selected: dict[str, str] = {}
    for role in roles:
        section_role = "ui" if role == "sdk" else role
        section_name = f"plugins.{section_role}"
        key = "sdk_package" if role == "sdk" else "builtin_package"
        section = configs.get_section(section_name)
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            selected[role] = value
        else:
            selected[role] = DEFAULT_PACKAGE_PATHS[role]
            LOGGER.info("%s.%s is missing; using %s", section_name, key, selected[role])
    return selected


def _config_entry_points() -> Iterable[EntryPoint]:
    return entry_points(group=CONFIG_ENTRY_POINT_GROUP)


def _discover_config_package(config_dir: str) -> PluginPackage:
    discovered = list(_config_entry_points())
    if not discovered:
        return load_package(
            DEFAULT_PACKAGE_PATHS["config"],
            config_dir=config_dir,
            section="plugins.config",
        )
    entry_point = next((item for item in discovered if item.name == "config"), discovered[0])
    try:
        factory = entry_point.load()
        package = factory()
    except Exception as exc:
        raise BootstrapError(
            f"Cannot load {CONFIG_ENTRY_POINT_GROUP} entry point "
            f"{entry_point.name!r} for config-dir {config_dir!r}: {exc}"
        ) from exc
    if not isinstance(package, PluginPackage):
        raise BootstrapError(
            f"{CONFIG_ENTRY_POINT_GROUP} entry point {entry_point.name!r} "
            "did not return PluginPackage"
        )
    return package


def _default_properties(
    descriptor: PluginDescriptor,
    *,
    name: str,
    config_dir: str,
    locale: str,
) -> dict[str, Any]:
    """Code-built default properties for one builtin assembly request."""
    properties: dict[str, Any] = {}
    if name == "config-toml":
        properties["plugin.config.path"] = str(Path(config_dir) / "langharness.toml")
    if name == "api-plugins":
        properties["plugin.config_dir"] = config_dir
    if name in LOG_NAMES:
        from langharness_logging.plugin import log_properties

        properties.update(log_properties(name.split("-", 1)[0], config_dir))
    if name in UI_LOCALE_NAMES:
        properties["plugin.ui.locale"] = locale
    if name in PATH_PROPERTIES:
        key, filename = PATH_PROPERTIES[name]
        properties[key] = str(Path(config_dir) / filename)
    if name in API_DEFAULTS:
        properties.update(API_DEFAULTS[name])
    return properties


def base_url_for(options: Namespace) -> str:
    """Connect URL built from the launcher's server options.

    ``--server-ip`` is a bind address; wildcard binds map to loopback when
    connecting.
    """
    host = (
        "127.0.0.1"
        if options.server_ip in {"0.0.0.0", "::", ""}
        else options.server_ip
    )
    return f"http://{host}:{options.server_port}"


def _assembly_requests(
    packages: Iterable[PluginPackage],
    *,
    config_dir: str,
    locale: str,
    override_scope: str,
    base_url: str,
) -> list[AssemblyRequest]:
    overrides = load_overrides(config_dir, override_scope)
    requests: list[AssemblyRequest] = []
    seen: set[str] = set()
    for package in packages:
        for contribution in package.contributions:
            descriptor = contribution.descriptor
            name = contribution.id  # stable identity of the assembly request
            properties = _default_properties(
                descriptor, name=name, config_dir=config_dir, locale=locale
            )
            enabled = True
            if name in overrides:
                enabled, properties = merge_overrides(properties, overrides[name])
            if (
                override_scope == "cli"
                and name == "server-log"
                or override_scope == "api"
                and name == "cli-log"
            ):
                continue
            if name in seen:
                continue
            seen.add(name)
            if name in {"cli-health", "cli-plugins"}:
                # The launcher owns the connect address; rewrite after stored
                # overrides so --server-ip/--server-port always win.
                properties["plugin.base_url"] = base_url
            scope_id = TARGET_SCOPES.get(contribution.target, ROOT_SCOPE_ID)
            requests.append(
                AssemblyRequest(name, descriptor, scope_id, properties, enabled)
            )
    return requests


def _select_packages(config_package: PluginPackage, options: Namespace) -> list[PluginPackage]:
    requests = _assembly_requests(
        [config_package],
        config_dir=options.config_dir,
        locale="en",
        override_scope="cli" if options.mode in {"ui", "all"} else "api",
        base_url=base_url_for(options),
    )
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        installed: set[tuple[str, str]] = set()
        for request in requests:
            key = (request.descriptor.module, request.descriptor.factory)
            if key not in installed:
                manager.install_descriptor(request.descriptor)
                installed.add(key)
        for request in requests:
            manager.create_instance(
                request.descriptor.factory,
                request.descriptor.module,
                request.scope_id,
                properties=request.properties,
                enabled=request.enabled,
            )
        configs = cast(Configs | None, manager.get_service(SPEC_CONFIGS))
        if configs is None:
            raise BootstrapError("Configuration package did not provide configs")
        paths = selected_package_paths(configs, mode=options.mode)
    finally:
        manager.stop()
    return [
        load_package(path, config_dir=options.config_dir, section=f"plugins.{role}")
        for role, path in paths.items()
    ]


def _run(options: Namespace, remainder: list[str]) -> int:
    config_package = _discover_config_package(options.config_dir)
    packages = [config_package, *_select_packages(config_package, options)]
    locale = "en"
    base_url = base_url_for(options)
    requests = _assembly_requests(
        packages,
        config_dir=options.config_dir,
        locale=locale,
        override_scope="cli" if options.mode in {"ui", "all"} else "api",
        base_url=base_url,
    )
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        coordinator: RuntimeMutationCoordinator | None = None
        if options.mode == "server":
            state_path = Path(options.config_dir) / "runtime_state.sqlite3"
            manager.bind_state(
                SqliteRuntimeStateStore(state_path),
                SqlitePluginHistoryStore(state_path),
            )
            coordinator = RuntimeMutationCoordinator(
                manager, discovery=PluginDiscovery()
            )
            manager.register_runtime_service(DynamicPluginManager, coordinator)
        installed: set[tuple[str, str]] = set()
        if coordinator is not None:
            manager.discover()
            coordinator.rescan()
            coordinator.restore()
            from langharness_api.common.server import _apply_agent_configs

            _apply_agent_configs(manager, options.config_dir)
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
        if options.mode == "server":
            server_service = cast(
                ServerServerProvider | None, manager.get_service(SPEC_SERVER_SERVER)
            )
            if server_service is None:
                raise BootstrapError("Server package did not provide server.server")
            server_service.server(options.server_ip, options.server_port)
            return 0

        if options.mode == "all":
            APIGuard(base_url, config_dir=options.config_dir).ensure_api_server()

        ui_service = cast(UIServerProvider | None, manager.get_service(SPEC_UI_SERVER))
        if ui_service is None:
            raise BootstrapError("UI package did not provide ui.server")
        return ui_service.run(
            {
                "argv": remainder,
                "base_url": base_url,
                "config_dir": options.config_dir,
                "descriptors": [request.descriptor for request in requests],
                "manager": manager,
            }
        )
    finally:
        manager.stop()


def main(argv: list[str] | None = None) -> int:
    options, remainder = parse_options(sys.argv[1:] if argv is None else argv)
    options.config_dir = str(Path(options.config_dir).expanduser().resolve())
    inherited = os.environ.get("LANG_HARNESS_DIR")
    os.environ["LANG_HARNESS_DIR"] = options.config_dir
    try:
        return _run(options, remainder)
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if inherited is None:
            os.environ.pop("LANG_HARNESS_DIR", None)
        else:
            os.environ["LANG_HARNESS_DIR"] = inherited
