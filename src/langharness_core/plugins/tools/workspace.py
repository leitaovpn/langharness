"""LangChain file-management and Bash tool plugin."""

from __future__ import annotations

from langchain_community.agent_toolkits import FileManagementToolkit
from langchain_community.tools import ShellTool
from langchain_core.tools import BaseTool
from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import ToolProvider


@ComponentFactory("workspace-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "workspace-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_root_dir", "plugin.tools.root_dir", ".")
class WorkspaceToolsPlugin:
    """Provides sandboxed file tools plus a Bash command tool."""

    def __init__(self) -> None:
        self._plugin_name = "workspace-tools"
        self._plugin_version = "1.0.0"
        self._root_dir = "."

    def get_tools(self) -> list[BaseTool]:
        file_tools = FileManagementToolkit(root_dir=self._root_dir).get_tools()
        bash = ShellTool(
            name="bash",
            description=(
                "Run a Bash command in the server workspace and return stdout/stderr."
            ),
        )
        return [*file_tools, bash]

    def get_tool_presentations(self) -> dict[str, str]:
        """How each call reads in the CLI transcript.

        The file tools come from LangChain's toolkit and use snake_case
        argument names, so their templates name those exact keys.
        """
        return {
            "read_file": "Read({file_path})",
            "write_file": "write_file({file_path})",
            "list_directory": "list_directory({dir_path})",
            "file_search": "file_search({pattern})",
            "move_file": "move_file({source_path} → {destination_path})",
            "copy_file": "copy_file({source_path} → {destination_path})",
            "file_delete": "file_delete({file_path})",
            "bash": "Bash({commands})",
        }

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
