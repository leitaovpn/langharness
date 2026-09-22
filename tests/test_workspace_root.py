"""Tests for the workspace-root provider plugin."""
# mypy: ignore-errors

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import SPEC_WORKSPACE, WorkspaceProvider
from langharness_core.plugin import (
    AGENT_PLUGIN_CATALOG,
    agent_plugin_descriptor,
    builtin_package,
)
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_plugin.validation import validate


def test_workspace_is_the_resolved_root_dir(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    plugin = WorkspaceRootPlugin()
    plugin._root = str(tmp_path / "nested" / "..")
    assert plugin.get_workspace() == str(tmp_path.resolve())


def test_workspace_follows_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    plugin = WorkspaceRootPlugin()
    plugin._root = str(link)
    assert plugin.get_workspace() == str(real.resolve())


def test_workspace_defaults_to_the_process_directory() -> None:
    plugin = WorkspaceRootPlugin()
    assert plugin.get_workspace() == str(Path(".").expanduser().resolve())


def test_workspace_plugin_conforms_to_its_contract() -> None:
    assert validate(WorkspaceRootPlugin(), WorkspaceProvider) == ()


def test_catalog_entry_points_at_the_component() -> None:
    assert AGENT_PLUGIN_CATALOG["workspace"] == (
        "langharness_core.plugins.workspace.root",
        "workspace-root-plugin-factory",
        SPEC_WORKSPACE,
    )
    descriptor = agent_plugin_descriptor("workspace")
    assert descriptor.specification == SPEC_WORKSPACE
    assert descriptor.description.strip()


def test_builtin_package_ships_the_workspace_template() -> None:
    contributions = {item.id: item for item in builtin_package().contributions}
    assert "workspace-template" in contributions
    assert contributions["workspace-template"].target == "agent"


def test_plugin_info_reports_the_component_identity() -> None:
    assert WorkspaceRootPlugin().get_plugin_info() == {
        "name": "workspace-root",
        "version": "1.0.0",
    }
