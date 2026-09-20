"""Canonical scope identifiers and scope-related plugin metadata keys."""

from __future__ import annotations

from langharness_scope import ROOT_SCOPE_ID, ScopeId

UI_SCOPE_ID = ScopeId("ui")
SERVER_SCOPE_ID = ScopeId("server")
AGENT_SCOPE_ID = ScopeId("agent")

PLUGIN_SCOPE_ID = "plugin.scope_id"
PLUGIN_SCOPE_CHAIN = "plugin.scope_chain"
PLUGIN_KEY = "plugin.key"
PLUGIN_INSTANCE_ID = "plugin.instance_id"

# The fixed runtime topology: every process seeds these on PluginManager start.
BUILTIN_SCOPES: tuple[tuple[ScopeId, str, ScopeId], ...] = (
    (UI_SCOPE_ID, "UI", ROOT_SCOPE_ID),
    (SERVER_SCOPE_ID, "Server", ROOT_SCOPE_ID),
    (AGENT_SCOPE_ID, "Agent", ROOT_SCOPE_ID),
)


def agent_instance_scope_id(agent_id: str) -> ScopeId:
    return ScopeId(f"agent:{agent_id}")


__all__ = [
    "AGENT_SCOPE_ID",
    "BUILTIN_SCOPES",
    "PLUGIN_INSTANCE_ID",
    "PLUGIN_KEY",
    "PLUGIN_SCOPE_CHAIN",
    "PLUGIN_SCOPE_ID",
    "ROOT_SCOPE_ID",
    "SERVER_SCOPE_ID",
    "UI_SCOPE_ID",
    "agent_instance_scope_id",
]
