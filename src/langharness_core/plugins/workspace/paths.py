"""Workspace path resolution shared by every path-taking tool.

Containment is decided after ``Path.resolve()``, so a symlink pointing out of
the workspace is rejected exactly like a ``..`` traversal.  Checking the input
string instead would let either one through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langharness_core.common.tool_error import ToolErrorCode, tool_error

OUTSIDE_WORKSPACE: ToolErrorCode = "path_outside_workspace"
PATH_NOT_FOUND: ToolErrorCode = "path_not_found"


class WorkspacePathError(Exception):
    """Raised when a path cannot be resolved inside the workspace."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        self.code: ToolErrorCode = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    """A path proven to live inside the workspace."""

    root: Path
    absolute: Path
    relative: str

    @property
    def is_dir(self) -> bool:
        return self.absolute.is_dir()

    @property
    def is_file(self) -> bool:
        return self.absolute.is_file()


def resolve_workspace_path(root: str | Path, raw: str | None) -> WorkspacePath:
    """Resolve ``raw`` inside ``root``, rejecting any escape.

    A blank path means the workspace root, which is what a directory listing
    without arguments should show.
    """
    text = (raw or "").strip()
    try:
        # The root is resolved here too: a tool must never turn an I/O failure
        # into a traceback, whichever end of the join caused it.
        base = Path(root).resolve()
        candidate = Path(text) if text else base
        absolute = candidate if candidate.is_absolute() else base / candidate
        resolved = absolute.resolve()
    except OSError as exc:
        raise WorkspacePathError(
            PATH_NOT_FOUND, f"cannot resolve {text!r}: {exc}"
        ) from exc
    try:
        relative = resolved.relative_to(base)
    except ValueError as exc:
        raise WorkspacePathError(
            OUTSIDE_WORKSPACE, f"{text or '.'!r} resolves outside the workspace"
        ) from exc
    return WorkspacePath(
        root=base,
        absolute=resolved,
        relative=relative.as_posix() if relative.parts else ".",
    )


def to_tool_error(exc: WorkspacePathError) -> dict[str, Any]:
    """Render a :class:`WorkspacePathError` as the shared tool error shape."""
    return tool_error(exc.code, exc.message)
