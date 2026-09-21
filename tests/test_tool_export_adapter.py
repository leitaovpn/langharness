"""Explicit service-to-tool adapter behavior."""

from pydantic import BaseModel

from langharness_core.plugins.tools.export_adapter import ToolExportAdapter
from langharness_plugin.package import ToolExport


class AddArgs(BaseModel):
    a: int
    b: int


class Target:
    def invoke_export(self, operation: str, arguments: dict[str, object]) -> object:
        assert operation == "add"
        assert isinstance(arguments["a"], int)
        assert isinstance(arguments["b"], int)
        return arguments["a"] + arguments["b"]


def test_explicit_export_is_wrapped_as_a_structured_tool() -> None:
    adapter = ToolExportAdapter()
    adapter._target = Target()
    adapter._exports = [ToolExport("add_numbers", "Add numbers", "add", AddArgs)]

    tools = adapter.get_tools()

    assert [tool.name for tool in tools] == ["add_numbers"]
    assert tools[0].invoke({"a": 2, "b": 3}) == 5
    assert adapter.get_plugin_info()["name"] == "tool-export-adapter"


def test_adapter_without_target_exports_no_tools() -> None:
    assert ToolExportAdapter().get_tools() == []


def test_exported_tools_carry_their_declared_headline() -> None:
    adapter = ToolExportAdapter()
    adapter._target = Target()
    adapter._exports = [
        ToolExport("add_numbers", "Add numbers", "add", AddArgs, headline="add({a}, {b})")
    ]

    assert adapter.get_tool_presentations() == {"add_numbers": "add({a}, {b})"}


def test_an_export_without_a_headline_is_simply_absent() -> None:
    adapter = ToolExportAdapter()
    adapter._target = Target()
    adapter._exports = [ToolExport("add_numbers", "Add numbers", "add", AddArgs)]

    assert adapter.get_tool_presentations() == {}
