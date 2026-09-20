"""Descriptors and assembly helpers for the built-in API plugins."""

from __future__ import annotations

from langharness_api.contracts import (
    SPEC_API_SERVER,
    SPEC_AUTH,
    SPEC_DB,
    SPEC_RATE_LIMIT,
    SPEC_ROUTE,
    SPEC_SERVER_SERVER,
    SPEC_UI_SDK,
)
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import PluginDescriptor

DESCRIPTION_TEMPLATES = {
    "api-auth": (
        "Authenticates API requests. Implements api.plugin.auth. Properties: "
        "plugin.token. Hot-swappable. Uninstall to disable authentication."
    ),
    "api-rate-limit": (
        "Limits API request rates. Implements api.plugin.rate_limit. "
        "Properties: plugin.limit. Hot-swappable. Uninstall to disable "
        "rate limiting."
    ),
    "api-db": (
        "Provides the API database connection. Implements api.plugin.db. "
        "No properties. Uninstall to disable database access."
    ),
    "api-plugins": (
        "Serves plugin management routes. Implements api.plugin.route. "
        "Properties: plugin.config_dir. Requires a restart for property "
        "changes. Uninstall to remove the plugin management API."
    ),
    "api-server": (
        "Builds the FastAPI application. Implements api.plugin.api_server. "
        "No properties. Do not uninstall while the API server runs."
    ),
    "server-server": (
        "Runs the standalone server process. Implements api.plugin.server.server. "
        "No properties. Uninstall to stop the server."
    ),
    "ui-sdk": (
        "HTTP client SDK for the UI process. Implements api.plugin.ui_sdk. "
        "No properties. Uninstall when the UI no longer calls the API."
    ),
}

ROUTE_NAMES = {
    "api-health",
    "api-stream",
    "api-resume",
    "api-scopes",
    "api-sessions",
    "api-agents",
}

ROUTE_DESCRIPTION = (
    "Serves one API route group. Implements api.plugin.route. No properties. "
    "Requires a restart for property changes. Uninstall to remove the route."
)


def api_auth_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-auth",
        version="1.0.0",
        module="langharness_api.plugins.auth.auth",
        factory="api-auth-plugin-factory",
        specification=SPEC_AUTH,
        description=DESCRIPTION_TEMPLATES["api-auth"],
        swap_policy="hot",
    )


def api_rate_limit_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-rate-limit",
        version="1.0.0",
        module="langharness_api.plugins.rate_limit.rate_limit",
        factory="api-rate-limit-plugin-factory",
        specification=SPEC_RATE_LIMIT,
        description=DESCRIPTION_TEMPLATES["api-rate-limit"],
        swap_policy="hot",
    )


def api_db_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-db",
        version="1.0.0",
        module="langharness_api.plugins.db.db",
        factory="api-db-plugin-factory",
        specification=SPEC_DB,
        description=DESCRIPTION_TEMPLATES["api-db"],
    )


def api_health_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-health",
        version="1.0.0",
        module="langharness_api.plugins.routes.health",
        factory="api-health-plugin-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )


def api_stream_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-stream",
        version="1.0.0",
        module="langharness_api.plugins.routes.stream",
        factory="api-stream-route-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )


def api_resume_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-resume",
        version="1.0.0",
        module="langharness_api.plugins.routes.resume",
        factory="api-resume-route-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )


def api_plugins_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-plugins",
        version="1.0.0",
        module="langharness_api.plugins.routes.plugins",
        factory="api-plugins-route-factory",
        specification=SPEC_ROUTE,
        description=DESCRIPTION_TEMPLATES["api-plugins"],
    )


def api_scopes_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-scopes",
        version="1.0.0",
        module="langharness_api.plugins.routes.scopes",
        factory="api-scopes-route-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )


def api_sessions_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-sessions",
        version="1.0.0",
        module="langharness_api.plugins.routes.sessions",
        factory="api-sessions-route-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )


def api_agents_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-agents",
        version="1.0.0",
        module="langharness_api.plugins.routes.agents",
        factory="api-agents-route-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )


def api_server_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-server",
        version="1.0.0",
        module="langharness_api.plugins.server.app",
        factory="api-server-factory",
        specification=SPEC_API_SERVER,
        description=DESCRIPTION_TEMPLATES["api-server"],
    )


def server_server_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="server-server",
        version="1.0.0",
        module="langharness_api.plugins.server.runtime",
        factory="server-server-factory",
        specification=SPEC_SERVER_SERVER,
        description=DESCRIPTION_TEMPLATES["server-server"],
    )


def ui_sdk_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="ui-sdk",
        version="1.0.0",
        module="langharness_api.plugins.sdk.http",
        factory="http-ui-sdk-factory",
        specification=SPEC_UI_SDK,
        description=DESCRIPTION_TEMPLATES["ui-sdk"],
    )


def sdk_package() -> PluginPackage:
    return PluginPackage(
        id="builtin.api.sdk",
        version="1.0.0",
        contributions=(PluginContribution("ui-sdk", "ui", ui_sdk_descriptor()),),
    )


def builtin_package() -> PluginPackage:
    """Describe the API plugins already installed by the server assembly."""
    return PluginPackage(
        id="builtin.api",
        version="1.0.0",
        contributions=(
            PluginContribution("api-auth", "server", api_auth_descriptor()),
            PluginContribution("api-rate-limit", "server", api_rate_limit_descriptor()),
            PluginContribution("api-db", "root", api_db_descriptor()),
            PluginContribution("api-health", "server", api_health_descriptor()),
            PluginContribution("api-stream", "server", api_stream_descriptor()),
            PluginContribution("api-resume", "server", api_resume_descriptor()),
            PluginContribution("api-plugins", "server", api_plugins_descriptor()),
            PluginContribution("api-scopes", "server", api_scopes_descriptor()),
            PluginContribution("api-sessions", "server", api_sessions_descriptor()),
            PluginContribution("api-agents", "server", api_agents_descriptor()),
            PluginContribution("api-server", "server", api_server_descriptor()),
            PluginContribution("server-server", "server", server_server_descriptor()),
        ),
    )
