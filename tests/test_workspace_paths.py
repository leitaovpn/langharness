"""Tests for the shared workspace path guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from langharness_core.plugins.workspace.paths import (
    WorkspacePath,
    WorkspacePathError,
    resolve_workspace_path,
    to_tool_error,
)


def test_relative_path_resolves_inside_the_workspace(tmp_path: Path) -> None:
    result = resolve_workspace_path(tmp_path, "src/a.py")
    assert isinstance(result, WorkspacePath)
    assert result.relative == "src/a.py"
    assert result.absolute == (tmp_path / "src" / "a.py").resolve()


def test_the_workspace_root_itself_resolves_to_dot(tmp_path: Path) -> None:
    assert resolve_workspace_path(tmp_path, ".").relative == "."
    assert resolve_workspace_path(tmp_path, "").relative == "."


def test_absolute_path_inside_the_workspace_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    assert resolve_workspace_path(tmp_path, str(target)).relative == "a.py"


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspacePathError) as caught:
        resolve_workspace_path(tmp_path, "../outside.py")
    assert caught.value.code == "path_outside_workspace"


def test_absolute_path_outside_the_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspacePathError) as caught:
        resolve_workspace_path(tmp_path, "/etc/passwd")
    assert caught.value.code == "path_outside_workspace"


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspacePathError) as caught:
        resolve_workspace_path(tmp_path, "link/secret.py")
    assert caught.value.code == "path_outside_workspace"


def test_symlink_staying_inside_the_workspace_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    assert resolve_workspace_path(tmp_path, "link/a.py").relative == "real/a.py"


def test_missing_file_still_resolves(tmp_path: Path) -> None:
    result = resolve_workspace_path(tmp_path, "does/not/exist.py")
    assert result.relative == "does/not/exist.py"
    assert result.is_file is False


def test_is_dir_and_is_file_report_the_target(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "f").write_text("x", encoding="utf-8")
    assert resolve_workspace_path(tmp_path, "d").is_dir is True
    assert resolve_workspace_path(tmp_path, "f").is_file is True


def test_error_maps_to_the_shared_tool_error_shape() -> None:
    error = to_tool_error(WorkspacePathError("path_not_found", "gone"))
    assert error["error"]["code"] == "path_not_found"
    assert error["error"]["message"] == "gone"
