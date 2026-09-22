"""Explicitly declared service operations exposed as LangChain tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import ComponentFactory, HiddenProperty, Property, Provides

from langharness_core.contracts import ToolProvider
from langharness_plugin.package import ToolExport


@ComponentFactory("tool-export-adapter-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "tool-export-adapter")
@Property("_plugin_version", "plugin.version", "1.0.0")
@HiddenProperty("_target", "plugin.tool_export.target", None)
@HiddenProperty("_exports", "plugin.tool_export.exports", None)
class ToolExportAdapter:
    def __init__(self) -> None:
        self._plugin_name = "tool-export-adapter"
        self._plugin_version = "1.0.0"
        self._target: Any = None
        self._exports: list[ToolExport] = []

    def get_tools(self) -> list[Any]:
        if self._target is None:
            return []
        return [self._make_tool(export) for export in self._exports or []]

    def get_tool_presentations(self) -> dict[str, str]:
        """Headlines for exported tools, as each declaration supplied them.

        An export that declared none is left out rather than given a
        generated default: absent is how the palette says "show the name".
        """
        return {
            export.name: export.headline
            for export in self._exports or []
            if export.headline
        }

    def _make_tool(self, export: ToolExport) -> StructuredTool:
        target = self._target

        def invoke(**arguments: Any) -> Any:
            return target.invoke_export(export.method, arguments)

        return StructuredTool.from_function(
            func=invoke,
            name=export.name,
            description=export.description,
            args_schema=export.args_schema,
        )

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
