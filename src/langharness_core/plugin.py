"""Descriptors and assembly helpers for the built-in core plugins."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langharness_core.contracts import (
    SPEC_AGENT_DIRECTORY,
    SPEC_AGENT_LOOP,
    SPEC_AGENT_REGISTRY,
    SPEC_AGENT_SERVER,
    SPEC_CACHE,
    SPEC_CHECKPOINTER,
    SPEC_CONTEXT_SCHEMA,
    SPEC_DEBUG,
    SPEC_INTERRUPT_AFTER,
    SPEC_INTERRUPT_BEFORE,
    SPEC_LLM,
    SPEC_MIDDLEWARE,
    SPEC_NAME,
    SPEC_RESPONSE_FORMAT,
    SPEC_SESSION_INDEX,
    SPEC_STATE_SCHEMA,
    SPEC_STORE,
    SPEC_SYSTEM_PROMPT,
    SPEC_TOOL,
    SPEC_TRANSFORMERS,
)
from langharness_core.plugins.agents.export import AGENT_TOOL_EXPORTS
from langharness_plugin.contracts import SPEC_TOOL_EXPORT_TARGET
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import PluginDescriptor

AGENT_PLUGIN_CATALOG: dict[str, tuple[str, str, str]] = {
    "llm": (
        "langharness_core.plugins.llm.llm",
        "llm-plugin-factory",
        SPEC_LLM,
    ),
    "tools": (
        "langharness_core.plugins.tools.workspace",
        "workspace-tools-plugin-factory",
        SPEC_TOOL,
    ),
    "name": (
        "langharness_core.plugins.name.template_name",
        "agent-name-plugin-factory",
        SPEC_NAME,
    ),
}

DEFAULT_AGENT_PLUGINS: tuple[str, ...] = ("tools", "name")

DYNAMIC_PLUGIN_CATALOG: dict[str, tuple[str, str, str]] = {
    "cache-plugin": (
        "langharness_core.plugins.cache.template_cache",
        "cache-plugin-factory",
        SPEC_CACHE,
    ),
    "checkpointer-plugin": (
        "langharness_core.plugins.checkpointer.template_checkpointer",
        "checkpointer-plugin-factory",
        SPEC_CHECKPOINTER,
    ),
    "context-schema-plugin": (
        "langharness_core.plugins.context_schema.template_context_schema",
        "context-schema-plugin-factory",
        SPEC_CONTEXT_SCHEMA,
    ),
    "debug-plugin": (
        "langharness_core.plugins.debug.template_debug",
        "debug-plugin-factory",
        SPEC_DEBUG,
    ),
    "interrupt-after-plugin": (
        "langharness_core.plugins.interrupt_after.template_interrupt_after",
        "interrupt-after-plugin-factory",
        SPEC_INTERRUPT_AFTER,
    ),
    "interrupt-before-plugin": (
        "langharness_core.plugins.interrupt_before.template_interrupt_before",
        "interrupt-before-plugin-factory",
        SPEC_INTERRUPT_BEFORE,
    ),
    "management-tools-plugin": (
        "langharness_core.plugins.tools.management",
        "management-tools-plugin-factory",
        SPEC_TOOL,
    ),
    "middleware-plugin": (
        "langharness_core.plugins.middleware.template_middleware",
        "middleware-plugin-factory",
        SPEC_MIDDLEWARE,
    ),
    "human-approval-plugin": (
        "langharness_core.plugins.middleware.human_approval",
        "human-approval-plugin-factory",
        SPEC_MIDDLEWARE,
    ),
    "response-format-plugin": (
        "langharness_core.plugins.response_format.template_response_format",
        "response-format-plugin-factory",
        SPEC_RESPONSE_FORMAT,
    ),
    "state-schema-plugin": (
        "langharness_core.plugins.state_schema.template_state_schema",
        "state-schema-plugin-factory",
        SPEC_STATE_SCHEMA,
    ),
    "store-plugin": (
        "langharness_core.plugins.store.template_store",
        "store-plugin-factory",
        SPEC_STORE,
    ),
    "system-prompt-plugin": (
        "langharness_core.plugins.system_prompt.template_system_prompt",
        "system-prompt-plugin-factory",
        SPEC_SYSTEM_PROMPT,
    ),
    "tools-plugin": (
        "langharness_core.plugins.tools.tools",
        "tools-plugin-factory",
        SPEC_TOOL,
    ),
    "transformers-plugin": (
        "langharness_core.plugins.transformers.template_transformers",
        "transformers-plugin-factory",
        SPEC_TRANSFORMERS,
    ),
}

AGENT_LOOP_MODULE = "langharness_core.plugins.loop.agent_loop"
TOOL_EXPORT_ADAPTER_MODULE = "langharness_core.plugins.tools.export_adapter"

AGENT_PLUGIN_SETTINGS: dict[str, dict[str, Any]] = {
    "tools": {"plugin.tools.root_dir": "."},
}

AGENT_LOOP_FIELDS: dict[str, str] = {
    SPEC_LLM: "_llm_provider",
    SPEC_TOOL: "_tool_providers",
    SPEC_SYSTEM_PROMPT: "_system_prompt_providers",
    SPEC_MIDDLEWARE: "_middleware_providers",
    SPEC_NAME: "_name_provider",
    SPEC_RESPONSE_FORMAT: "_response_format_provider",
    SPEC_CACHE: "_cache_provider",
    SPEC_TRANSFORMERS: "_transformers_providers",
    SPEC_INTERRUPT_BEFORE: "_interrupt_before_providers",
    SPEC_INTERRUPT_AFTER: "_interrupt_after_providers",
}

AGENT_PLUGIN_DESCRIPTIONS: dict[str, str] = {
    "llm": (
        "Provides the LLM service consumed by an agent loop. Implements "
        "agent.plugin.llm. Use it whenever an agent must call a model; skip it "
        "when the agent should inherit the scope default. Properties: "
        "plugin.model.name, plugin.model.instance, plugin.model.api_key, "
        "plugin.model.base_url, plugin.model.protocol, plugin.agent_id. "
        "Requires a restart for property changes. Uninstall when the agent "
        "is removed."
    ),
    "tools": (
        "Provides tool services for one agent scope. Implements agent.plugin.tools. "
        "Properties: plugin.tools.root_dir, plugin.agent_id. Requires a restart "
        "for property changes. Uninstall when the agent is removed."
    ),
    "name": (
        "Names the agent in UI and prompts. Implements agent.plugin.name. "
        "Properties: plugin.agent_name, plugin.agent_id. Requires a restart "
        "for property changes. Uninstall when the agent is removed."
    ),
    "agent-loop": (
        "Runs the agent loop for one agent scope. Implements "
        "agent.plugin.agent_loop. Properties: plugin.agent_id and "
        "requires.filters for scoped dependency fields. Requires a restart "
        "for property changes. Uninstall when the agent is removed."
    ),
    "tool-export-adapter": (
        "Adapts a tool export target into scoped tool services. Implements "
        "agent.plugin.tools. Properties: plugin.tool_export.target and "
        "plugin.tool_export.exports. Managed by the dynamic plugin coordinator."
    ),
    "agent-directory": (
        "Materializes agent plugin sets from stored agent configurations. "
        "Implements agent.plugin.agent_directory. No properties. Do not "
        "uninstall while the server manages agents."
    ),
    "agent-server": (
        "Serves the agent management API for the server process. Implements "
        "agent.plugin.agent_server. No properties. Uninstall to disable "
        "agent management endpoints."
    ),
    "agent-registry": (
        "Persists agent configurations. Implements agent.registry. "
        "Properties: plugin.agents.path. Requires a restart for property "
        "changes. Uninstall when agent management is disabled."
    ),
    "session-index": (
        "Indexes sessions in a local SQLite database. Implements session.index. "
        "Properties: plugin.sessions.path. Requires a restart for property "
        "changes. Uninstall when session indexing is disabled."
    ),
    "sqlite-checkpointer": (
        "Persists agent checkpoints in a local SQLite database. Implements "
        "agent.plugin.checkpointer. Properties: plugin.checkpoint.path. "
        "Requires a restart for property changes. Uninstall when checkpointing "
        "is disabled."
    ),
    "agent-operations-export": (
        "Exports agent management operations as agent tools. Implements "
        "plugin.tool_export.target. No properties. Uninstall to remove the "
        "agent operations tools."
    ),
}


def _dynamic_description(plugin: str, specification: str) -> str:
    return (
        f"Agent plugin capability: {plugin}. Implements {specification}. "
        "Install it to make this capability available to agent scopes; "
        "materialize it per agent with an agent_instance install. Requires "
        "a restart for property changes. Uninstall when no agent uses it."
    )


def agent_scoped_specifications() -> list[str]:
    """Every specification the directory may materialize per agent."""
    return list(dict.fromkeys(spec for _, _, spec in AGENT_PLUGIN_CATALOG.values()))


def agent_required_modules() -> list[str]:
    """Bundles the directory needs before it can materialize an agent."""
    modules = [AGENT_PLUGIN_CATALOG[plugin][0] for plugin in DEFAULT_AGENT_PLUGINS]
    modules.append(AGENT_LOOP_MODULE)
    return list(dict.fromkeys(modules))


def agent_filter(agent_id: str) -> str:
    """Build the LDAP filter selecting one agent's plugin instances."""
    return f"(plugin.agent_id={agent_id})"


def agent_plugin_descriptor(plugin: str) -> PluginDescriptor:
    """Static definition of one agent plugin from the catalog."""
    module, factory, specification = AGENT_PLUGIN_CATALOG[plugin]
    return PluginDescriptor(
        name=plugin,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=AGENT_PLUGIN_DESCRIPTIONS[plugin],
    )


def agent_binding_properties(
    agent_id: str, plugin: str, properties: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Effective user properties for one agent plugin instance."""
    merged = dict(AGENT_PLUGIN_SETTINGS.get(plugin, {}))
    merged.update(properties or {})
    merged["plugin.agent_id"] = agent_id
    return merged


def agent_plugin_template_descriptor(plugin: str) -> PluginDescriptor:
    """Static definition used to pre-install a catalog bundle."""
    descriptor = agent_plugin_descriptor(plugin)
    return PluginDescriptor(
        name=f"{plugin}-template",
        version=descriptor.version,
        module=descriptor.module,
        factory=descriptor.factory,
        specification=descriptor.specification,
        description=descriptor.description,
        swap_policy=descriptor.swap_policy,
    )


def default_llm_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """User properties for the agent-scope fallback LLM instance."""
    return dict(properties)


def agent_loop_descriptor() -> PluginDescriptor:
    """Static definition of the agent loop component."""
    return PluginDescriptor(
        name="agent-loop",
        version="1.0.0",
        module=AGENT_LOOP_MODULE,
        factory="agent-loop-factory",
        specification=SPEC_AGENT_LOOP,
        description=AGENT_PLUGIN_DESCRIPTIONS["agent-loop"],
    )


def agent_loop_properties(
    agent_id: str,
    scoped_specifications: Iterable[str],
    *,
    visibility_filter: str | None = None,
) -> dict[str, Any]:
    """User properties for one agent's loop instance.

    The manager AND-merges its scope filter over the fields the loop class
    declares via ``@ScopedDependencies``.
    """
    scoped_specifications = tuple(scoped_specifications)
    filters = {
        AGENT_LOOP_FIELDS[specification]: visibility_filter or agent_filter(agent_id)
        for specification in scoped_specifications
        if specification in AGENT_LOOP_FIELDS
    }
    if SPEC_LLM in scoped_specifications:
        filters["_scoped_llm_providers"] = visibility_filter or agent_filter(agent_id)
    return {"plugin.agent_id": agent_id, "requires.filters": filters}


def agent_loop_template_descriptor() -> PluginDescriptor:
    """Static definition used to pre-install the loop bundle."""
    descriptor = agent_loop_descriptor()
    return PluginDescriptor(
        name="agent-loop-template",
        version=descriptor.version,
        module=descriptor.module,
        factory=descriptor.factory,
        specification=descriptor.specification,
        description=descriptor.description,
        swap_policy=descriptor.swap_policy,
    )


def tool_export_adapter_template_descriptor() -> PluginDescriptor:
    """Static definition used to pre-install the tool export adapter bundle."""
    return PluginDescriptor(
        name="tool-export-adapter-template",
        version="1.0.0",
        module=TOOL_EXPORT_ADAPTER_MODULE,
        factory="tool-export-adapter-factory",
        specification=SPEC_TOOL,
        description=AGENT_PLUGIN_DESCRIPTIONS["tool-export-adapter"],
    )


def sqlite_checkpointer_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="sqlite-checkpointer",
        version="1.0.0",
        module="langharness_core.plugins.checkpointer.sqlite",
        factory="sqlite-checkpointer-plugin-factory",
        specification=SPEC_CHECKPOINTER,
        description=AGENT_PLUGIN_DESCRIPTIONS["sqlite-checkpointer"],
    )


def sqlite_checkpointer_properties(directory: str) -> dict[str, Any]:
    return {
        "plugin.checkpoint.path": str(
            Path(directory) / "langharness_checkpoints.sqlite3"
        )
    }


def session_index_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="session-index",
        version="1.0.0",
        module="langharness_core.plugins.sessions.sqlite",
        factory="session-index-plugin-factory",
        specification=SPEC_SESSION_INDEX,
        description=AGENT_PLUGIN_DESCRIPTIONS["session-index"],
    )


def session_index_properties(directory: str) -> dict[str, Any]:
    return {"plugin.sessions.path": str(Path(directory) / "sessions.sqlite3")}


def agent_registry_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="agent-registry",
        version="1.0.0",
        module="langharness_core.plugins.agents.registry",
        factory="agent-registry-plugin-factory",
        specification=SPEC_AGENT_REGISTRY,
        description=AGENT_PLUGIN_DESCRIPTIONS["agent-registry"],
    )


def agent_registry_properties(directory: str) -> dict[str, Any]:
    return {"plugin.agents.path": str(Path(directory) / "agents.json")}


def agent_directory_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="agent-directory",
        version="1.0.0",
        module="langharness_core.plugins.agents.directory",
        factory="agent-directory-plugin-factory",
        specification=SPEC_AGENT_DIRECTORY,
        description=AGENT_PLUGIN_DESCRIPTIONS["agent-directory"],
    )


def agent_server_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="agent-server",
        version="1.0.0",
        module="langharness_core.plugins.agents.server",
        factory="agent-server-factory",
        specification=SPEC_AGENT_SERVER,
        description=AGENT_PLUGIN_DESCRIPTIONS["agent-server"],
    )


def builtin_package() -> PluginPackage:
    """Describe server-scoped core plugins and agent plugin templates."""
    return PluginPackage(
        id="builtin.core",
        version="1.0.0",
        contributions=(
            PluginContribution(
                "sqlite-checkpointer", "server", sqlite_checkpointer_descriptor()
            ),
            PluginContribution(
                "session-index", "server", session_index_descriptor()
            ),
            PluginContribution(
                "agent-registry", "server", agent_registry_descriptor()
            ),
            PluginContribution(
                "agent-directory", "server", agent_directory_descriptor()
            ),
            PluginContribution("agent-server", "server", agent_server_descriptor()),
            PluginContribution(
                "llm-template", "agent", agent_plugin_template_descriptor("llm")
            ),
            PluginContribution(
                "tools-template", "agent", agent_plugin_template_descriptor("tools")
            ),
            PluginContribution(
                "name-template", "agent", agent_plugin_template_descriptor("name")
            ),
            PluginContribution(
                "agent-loop-template", "agent", agent_loop_template_descriptor()
            ),
            PluginContribution(
                "tool-export-adapter-template",
                "agent",
                tool_export_adapter_template_descriptor(),
            ),
        ),
    )


def dynamic_template_descriptor(plugin: str) -> PluginDescriptor:
    """Static definition of a dynamically installable core plugin."""
    module, factory, specification = DYNAMIC_PLUGIN_CATALOG[plugin]
    return PluginDescriptor(
        name=f"{plugin}-template",
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=_dynamic_description(plugin, specification),
    )


def dynamic_package() -> PluginPackage:
    """Describe the core plugins installable at runtime, beyond the built-in set."""
    contributions = [
        PluginContribution(
            f"{plugin}-template", "agent", dynamic_template_descriptor(plugin)
        )
        for plugin in DYNAMIC_PLUGIN_CATALOG
    ]
    # Middleware that participates in an agent loop must be materialized as an
    # agent instance so the loop's ``plugin.agent_id`` filter can see it.
    human_index = next(
        index
        for index, contribution in enumerate(contributions)
        if contribution.id == "human-approval-plugin-template"
    )
    contributions[human_index] = PluginContribution(
        "human-approval-plugin-template",
        "agent_instance",
        contributions[human_index].descriptor,
    )
    # Management tools are likewise installed per agent; the contribution
    # shares the template's definition and is installed at agent:<id> scopes.
    management_index = next(
        index
        for index, contribution in enumerate(contributions)
        if contribution.id == "management-tools-plugin-template"
    )
    template = contributions[management_index].descriptor
    contributions.append(
        PluginContribution(
            "management-tools-plugin-instance",
            "agent_instance",
            PluginDescriptor(
                name="management-tools-plugin-agent",
                version=template.version,
                module=template.module,
                factory=template.factory,
                specification=template.specification,
                description=template.description,
                swap_policy=template.swap_policy,
            ),
        )
    )
    return PluginPackage(
        id="dynamic.core",
        version="1.0.0",
        contributions=tuple(contributions),
    )


def agent_tools_package() -> PluginPackage:
    """Describe the agent management tools installed via dynamic discovery."""
    return PluginPackage(
        id="agent.tools",
        version="1.0.0",
        contributions=(
            PluginContribution(
                "agent-operations",
                "agent",
                PluginDescriptor(
                    name="agent-operations-export",
                    version="1.0.0",
                    module="langharness_core.plugins.agents.export",
                    factory="agent-operations-export-factory",
                    specification=SPEC_TOOL_EXPORT_TARGET,
                    description=AGENT_PLUGIN_DESCRIPTIONS["agent-operations-export"],
                ),
                tool_exports=AGENT_TOOL_EXPORTS,
            ),
        ),
    )
