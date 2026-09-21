"""Tool plugin implementation."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.tools import tool as langchain_tool
from pelix.ipopo.decorators import ComponentFactory, HiddenProperty, Property, Provides

from langharness_core.contracts import ToolProvider


@langchain_tool
def add(a: int, b: int) -> int:
    """Add two integers together."""
    return a + b


@ComponentFactory("tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "tools-plugin")
@Property("_plugin_version", "plugin.version", "1.0.0")
@HiddenProperty("_tool_functions", "plugin.tools.functions", None)
class ToolPlugin:
    """Provides one or more LangChain tools to the agent loop."""

    def __init__(self) -> None:
        self._plugin_name = "tools-plugin"
        self._plugin_version = "1.0.0"
        self._tool_functions: Any = None

    def get_tools(self) -> list[Any]:
        if self._tool_functions is None:
            return [add]

        tools: list[Any] = []
        for function in self._tool_functions:
            if isinstance(function, (BaseTool, StructuredTool)):
                tools.append(function)
            else:
                tools.append(langchain_tool(function))
        return tools

    def get_tool_presentations(self) -> dict[str, str]:
        return {"add": "add({a}, {b})"}

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
