"""Optional LangChain middleware for bounded conversation history."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import before_model
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import MiddlewareProvider
from langharness_core.plugins.context.compression import compress_messages


@ComponentFactory("context-compression-middleware-plugin-factory")
@Provides(MiddlewareProvider)
@Property("_plugin_name", "plugin.name", "context-compression-middleware")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_budget_bytes", "plugin.context.budget_bytes", 0)
class ContextCompressionMiddlewarePlugin:
    """Compress messages immediately before each model call when enabled."""

    def __init__(self) -> None:
        self._plugin_name = "context-compression-middleware"
        self._plugin_version = "1.0.0"
        self._budget_bytes = 0

    def get_middlewares(self) -> list[Any]:
        budget = int(self._budget_bytes)
        if budget <= 0:
            return []

        @before_model(name=self._plugin_name)
        def compress(state: Any, runtime: Any) -> dict[str, Any]:
            messages = state.get("messages", [])
            kept = compress_messages(messages, budget_bytes=budget)
            if len(kept) == len(messages):
                return {}
            return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept]}

        return [compress]

    def get_plugin_info(self) -> dict[str, str]:
        return {
            "name": self._plugin_name,
            "version": self._plugin_version,
            "enabled": str(int(self._budget_bytes) > 0).lower(),
        }
