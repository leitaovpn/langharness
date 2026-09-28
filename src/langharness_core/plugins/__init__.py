"""Concrete plugin implementations, resolved lazily."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AgentRegistryPlugin": ("agents.registry", "AgentRegistryPlugin"),
    "LLMPlugin": ("llm.llm", "LLMPlugin"),
    "PluginAgentLoop": ("loop.agent_loop", "PluginAgentLoop"),
    "SQLiteCheckpointerPlugin": ("checkpointer.sqlite", "SQLiteCheckpointerPlugin"),
    "SQLiteSessionIndexPlugin": ("sessions.sqlite", "SQLiteSessionIndexPlugin"),
    "TemplateAgentNamePlugin": ("name.template_name", "TemplateAgentNamePlugin"),
    "TemplateCachePlugin": ("cache.template_cache", "TemplateCachePlugin"),
    "TemplateCheckpointerPlugin": ("checkpointer.template_checkpointer", "TemplateCheckpointerPlugin"),
    "TemplateContextSchemaPlugin": ("context_schema.template_context_schema", "TemplateContextSchemaPlugin"),
    "TemplateDebugPlugin": ("debug.template_debug", "TemplateDebugPlugin"),
    "TemplateInterruptAfterPlugin": ("interrupt_after.template_interrupt_after", "TemplateInterruptAfterPlugin"),
    "TemplateInterruptBeforePlugin": ("interrupt_before.template_interrupt_before", "TemplateInterruptBeforePlugin"),
    "TemplateMiddlewarePlugin": ("middleware.template_middleware", "TemplateMiddlewarePlugin"),
    "TemplateResponseFormatPlugin": ("response_format.template_response_format", "TemplateResponseFormatPlugin"),
    "TemplateStateSchemaPlugin": ("state_schema.template_state_schema", "TemplateStateSchemaPlugin"),
    "TemplateStorePlugin": ("store.template_store", "TemplateStorePlugin"),
    "TemplateSystemPromptPlugin": ("system_prompt.template_system_prompt", "TemplateSystemPromptPlugin"),
    "ToolPlugin": ("tools.tools", "ToolPlugin"),
    "WorkspaceToolsPlugin": ("tools.workspace", "WorkspaceToolsPlugin"),
    "TemplateTransformersPlugin": ("transformers.template_transformers", "TemplateTransformersPlugin"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


"""Legacy eager imports removed; names above preserve the public API."""
'''DISABLED
from langharness_core.plugins.agents.registry import AgentRegistryPlugin
from langharness_core.plugins.cache.template_cache import TemplateCachePlugin
from langharness_core.plugins.checkpointer.sqlite import SQLiteCheckpointerPlugin
from langharness_core.plugins.checkpointer.template_checkpointer import (
    TemplateCheckpointerPlugin,
)
from langharness_core.plugins.context_schema.template_context_schema import (
    TemplateContextSchemaPlugin,
)
from langharness_core.plugins.debug.template_debug import TemplateDebugPlugin
from langharness_core.plugins.interrupt_after.template_interrupt_after import (
    TemplateInterruptAfterPlugin,
)
from langharness_core.plugins.interrupt_before.template_interrupt_before import (
    TemplateInterruptBeforePlugin,
)
from langharness_core.plugins.llm.llm import LLMPlugin
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.middleware.template_middleware import (
    TemplateMiddlewarePlugin,
)
from langharness_core.plugins.name.template_name import TemplateAgentNamePlugin
from langharness_core.plugins.response_format.template_response_format import (
    TemplateResponseFormatPlugin,
)
from langharness_core.plugins.sessions.sqlite import SQLiteSessionIndexPlugin
from langharness_core.plugins.state_schema.template_state_schema import (
    TemplateStateSchemaPlugin,
)
from langharness_core.plugins.store.template_store import TemplateStorePlugin
from langharness_core.plugins.system_prompt.template_system_prompt import (
    TemplateSystemPromptPlugin,
)
from langharness_core.plugins.tools.tools import ToolPlugin
from langharness_core.plugins.tools.workspace import WorkspaceToolsPlugin
from langharness_core.plugins.transformers.template_transformers import (
    TemplateTransformersPlugin,
)
DISABLED'''

__all__ = [
    "AgentRegistryPlugin",
    "LLMPlugin",
    "PluginAgentLoop",
    "SQLiteCheckpointerPlugin",
    "SQLiteSessionIndexPlugin",
    "TemplateAgentNamePlugin",
    "TemplateCachePlugin",
    "TemplateCheckpointerPlugin",
    "TemplateContextSchemaPlugin",
    "TemplateDebugPlugin",
    "TemplateInterruptAfterPlugin",
    "TemplateInterruptBeforePlugin",
    "TemplateMiddlewarePlugin",
    "TemplateResponseFormatPlugin",
    "TemplateStateSchemaPlugin",
    "TemplateStorePlugin",
    "TemplateSystemPromptPlugin",
    "ToolPlugin",
    "WorkspaceToolsPlugin",
    "TemplateTransformersPlugin",
]
