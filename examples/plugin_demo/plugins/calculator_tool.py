"""Tool plugin: contributes the ``add`` tool to the agent loop."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pelix.ipopo.decorators import (
    ComponentFactory,
    Instantiate,
    Property,
    Provides,
)

from plugin_demo.contracts import ToolProvider


@tool
def add(a: int, b: int) -> int:
    """Add two integers together."""
    return a + b


@ComponentFactory("calculator-tool-factory")
@Instantiate("calculator-tool")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "calculator-tool")
@Property("_plugin_version", "plugin.version", "1.0.0")
class CalculatorToolPlugin:
    """Exposes one or more LangChain tools."""

    def get_tools(self) -> list[Any]:
        return [add]

    def get_tool_presentations(self) -> dict[str, str]:
        return {"add": "add({a}, {b})"}

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
