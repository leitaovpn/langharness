"""Workspace-root provider: the realpath every path-taking plugin resolves against."""

from __future__ import annotations

from pathlib import Path

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import WorkspaceProvider


@ComponentFactory("workspace-root-plugin-factory")
@Provides(WorkspaceProvider)
@Property("_plugin_name", "plugin.name", "workspace-root")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_root", "plugin.workspace.root_dir", ".")
class WorkspaceRootPlugin:
    """Pins the workspace every path-taking tool and prompt section resolves against.

    The path is resolved with symlinks followed and is never cached: a property
    update must take effect on the next read, and one ``Path.resolve`` per
    model step is cheap next to the model call it precedes.
    """

    def __init__(self) -> None:
        self._plugin_name = "workspace-root"
        self._plugin_version = "1.0.0"
        self._root = "."

    def get_workspace(self) -> str:
        return str(Path(self._root).expanduser().resolve())

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
