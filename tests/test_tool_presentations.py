"""Every tool a provider offers must be able to say how it reads."""

from __future__ import annotations

from typing import Any

from langharness_core.plugins.tools.tools import ToolPlugin
from langharness_core.plugins.tools.workspace import WorkspaceToolsPlugin


def assert_covers_its_tools(plugin: Any) -> None:
    offered = {tool.name for tool in plugin.get_tools()}
    declared = set(plugin.get_tool_presentations())

    assert offered == declared, f"missing: {sorted(offered - declared)}"


def test_workspace_provider_titles_every_tool_it_offers() -> None:
    assert_covers_its_tools(WorkspaceToolsPlugin())


def test_tool_plugin_titles_every_tool_it_offers() -> None:
    assert_covers_its_tools(ToolPlugin())


def test_workspace_titles_read_as_commands() -> None:
    presentations = WorkspaceToolsPlugin().get_tool_presentations()

    assert presentations["bash"] == "Bash({commands})"
    assert presentations["read_file"] == "Read({file_path})"
    assert presentations["list_directory"] == "list_directory({dir_path})"
