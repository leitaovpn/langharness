"""Code-agent workspace tools: bounded reads, search and patch editing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field

from langharness_core.common.text import digest, truncate_bytes
from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    ToolProvider,
    WorkspaceProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
    to_tool_error,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

DEFAULT_MAX_READ_BYTES = 131_072
DEFAULT_MAX_ENTRIES = 500
BINARY_SNIFF_BYTES = 8_192


class ListDirectoryArgs(BaseModel):
    path: str = Field(
        default=".",
        description="Workspace-relative directory. Defaults to the workspace root.",
    )


class ReadFileArgs(BaseModel):
    path: str = Field(description="Workspace-relative file path.")
    offset: int = Field(default=1, ge=1, description="1-based first line to return.")
    limit: int = Field(
        default=0,
        ge=0,
        description="Maximum lines to return. 0 means every line from offset.",
    )


def _entry_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _looks_binary(raw: bytes) -> bool:
    """A NUL byte in the first block is the cheap, reliable binary tell."""
    return b"\x00" in raw[:BINARY_SNIFF_BYTES]


@ComponentFactory("code-workspace-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "code-workspace-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_max_read_bytes", "plugin.tools.max_read_bytes", DEFAULT_MAX_READ_BYTES)
@Property("_max_entries", "plugin.tools.max_entries", DEFAULT_MAX_ENTRIES)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_workspace", "_state")
class CodeWorkspaceToolsPlugin:
    """Reads and edits files inside one pinned workspace.

    Without a workspace provider this plugin offers no tools at all: guessing a
    directory would mean editing whatever the process happens to sit in.
    """

    def __init__(self) -> None:
        self._plugin_name = "code-workspace-tools"
        self._plugin_version = "1.0.0"
        self._max_read_bytes = DEFAULT_MAX_READ_BYTES
        self._max_entries = DEFAULT_MAX_ENTRIES
        self._workspace: Any = None
        self._state: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_workspace": ContractGuard(self, "_workspace", WorkspaceProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

    @BindField("_workspace", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_workspace")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_state", if_valid=True)
    def _on_state_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_state")
    def _on_state_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._workspace is None:
            return []
        return [self._list_tool(), self._read_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        """Headlines for the CLI transcript.

        ``apply_patch`` is deliberately missing: its argument is a whole patch
        document, and the renderer's absent-means-bare-name rule gives a better
        transcript than inlining it.
        """
        return {
            "list_directory": "list_directory({path})",
            "read_file": "read_file({path})",
        }

    def _root(self) -> Path:
        return Path(self._workspace.get_workspace())

    def _resolve(self, raw: str | None) -> Any:
        return resolve_workspace_path(self._root(), raw)

    def _list_tool(self) -> StructuredTool:
        plugin = self

        def list_directory(path: str = ".") -> dict[str, Any]:
            try:
                target = plugin._resolve(path)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            if not target.is_dir:
                return tool_error(
                    "not_a_directory", f"{target.relative!r} is not a directory"
                )
            entries: list[dict[str, Any]] = []
            truncated = False
            for child in sorted(target.absolute.iterdir(), key=lambda item: item.name):
                if len(entries) >= plugin._max_entries:
                    truncated = True
                    break
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append(
                    {
                        "name": child.name,
                        "path": (
                            f"{target.relative}/{child.name}"
                            if target.relative != "."
                            else child.name
                        ),
                        "type": _entry_type(child),
                        "size": size,
                    }
                )
            return {"path": target.relative, "entries": entries, "truncated": truncated}

        return StructuredTool.from_function(
            func=list_directory,
            name="list_directory",
            description=(
                "List one workspace directory, one level deep. Use it to learn "
                "what exists before reading or editing, instead of guessing "
                "paths. Entries are sorted by name and capped, so check "
                "'truncated' before concluding a directory holds nothing else. "
                'Returns {"path": str, "entries": [{"name": str, "path": str '
                "(workspace-relative), \"type\": 'file'|'directory'|'symlink'|"
                "'other', \"size\": int}], \"truncated\": bool}. Returns "
                '{"error": {code, message, retryable, details}} when the path '
                "leaves the workspace or is not a directory."
            ),
            args_schema=ListDirectoryArgs,
        )

    def _read_tool(self) -> StructuredTool:
        plugin = self

        def read_file(path: str, offset: int = 1, limit: int = 0) -> dict[str, Any]:
            try:
                target = plugin._resolve(path)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            if not target.absolute.exists():
                return tool_error("path_not_found", f"{target.relative!r} does not exist")
            if not target.is_file:
                return tool_error("not_a_file", f"{target.relative!r} is not a file")
            try:
                raw = target.absolute.read_bytes()
            except OSError as exc:
                return tool_error(
                    "path_not_found", f"cannot read {target.relative!r}: {exc}"
                )
            if _looks_binary(raw):
                return tool_error(
                    "not_a_file", f"{target.relative!r} looks like binary content"
                )
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            start = max(offset, 1) - 1
            selected = lines[start:] if limit <= 0 else lines[start : start + limit]
            body, omitted = truncate_bytes(
                "".join(selected), plugin._max_read_bytes
            )
            return {
                "path": target.relative,
                "text": body,
                "start_line": start + 1,
                "end_line": start + len(selected),
                "total_lines": len(lines),
                "sha256": digest(text),
                "truncated": omitted > 0,
            }

        return StructuredTool.from_function(
            func=read_file,
            name="read_file",
            description=(
                "Read one workspace file as text. Read before you edit: the "
                "source of truth is the file's current content, not your "
                "recollection of it. Use offset/limit for large files, and "
                "quote the returned 'sha256' when reporting what you read so "
                "the observation is tied to a revision. Content is truncated "
                "at a byte budget; check 'truncated'. Binary files are "
                'rejected. Returns {"path": str, "text": str, "start_line": '
                'int, "end_line": int, "total_lines": int, "sha256": str, '
                '"truncated": bool}, or {"error": {code, message, retryable, '
                "details}}."
            ),
            args_schema=ReadFileArgs,
        )
