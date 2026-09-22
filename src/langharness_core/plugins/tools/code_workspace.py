"""Code-agent workspace tools: bounded reads, search and patch editing."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
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
from langharness_core.plugins.tools import search as search_module
from langharness_core.plugins.tools.patch import (
    PatchError,
    PatchPlan,
    apply_to_text,
    parse_patch,
)
from langharness_core.plugins.workspace.paths import (
    WorkspacePath,
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


class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


class PatchArgs(BaseModel):
    patch: str = Field(
        min_length=1,
        description=(
            "Patch document starting with '*** Begin Patch' and ending with "
            "'*** End Patch'."
        ),
    )


@dataclass(frozen=True, slots=True)
class _StagedWrite:
    """One fully-computed file outcome, before anything is written.

    ``new_text`` is ``None`` for a deletion; ``remove_original`` is the old
    path of a move, unlinked only after the destination is on disk.
    """

    destination: WorkspacePath
    remove_original: WorkspacePath | None
    original: str | None
    new_text: str | None
    change: str


class SearchTextArgs(BaseModel):
    query: str = Field(min_length=1, description="Python regular expression to find.")
    glob: str | None = Field(
        default=None, description="Optional filename glob, e.g. '*.py'."
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


def _git(root: Path, *arguments: str) -> str | None:
    """Run one git query in the workspace; None when git or the repo is absent.

    Only the trailing newline is removed. ``str.strip()`` would eat the leading
    space of a porcelain line's two-letter status, shifting the path by one
    character and misattributing the very change this feeds.
    """
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.rstrip("\n")


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
        return [
            self._list_tool(),
            self._read_tool(),
            self._search_tool(),
            self._patch_tool(),
            self._status_tool(),
        ]

    def get_tool_presentations(self) -> dict[str, str]:
        """Headlines for the CLI transcript.

        ``apply_patch`` is deliberately missing: its argument is a whole patch
        document, and the renderer's absent-means-bare-name rule gives a better
        transcript than inlining it.
        """
        return {
            "list_directory": "list_directory({path})",
            "read_file": "read_file({path})",
            "search_text": "search_text({query})",
            "workspace_status": "workspace_status()",
        }

    def _root(self) -> Path:
        return Path(self._workspace.get_workspace())

    def _resolve(self, raw: str | None) -> WorkspacePath:
        return resolve_workspace_path(self._root(), raw)

    def _read_text(self, target: WorkspacePath) -> str | None:
        """Current text of a file, or None when it is absent or unreadable."""
        if not target.is_file:
            return None
        try:
            return target.absolute.read_text(encoding="utf-8")
        except OSError:
            return None

    def _stage(self, plan: PatchPlan) -> list[_StagedWrite]:
        """Compute every file's outcome; raise before writing anything."""
        staged: list[_StagedWrite] = []
        for file_patch in plan.files:
            target = self._resolve(file_patch.path)
            original = self._read_text(target)
            if file_patch.kind == "add":
                if original is not None:
                    raise PatchError(
                        "patch_conflict", f"{file_patch.path!r} already exists"
                    )
                staged.append(
                    _StagedWrite(
                        destination=target,
                        remove_original=None,
                        original=None,
                        new_text=apply_to_text("", file_patch),
                        change="add",
                    )
                )
                continue
            if original is None:
                # A precise code beats a generic conflict: the model can fix a
                # wrong path, but not a message that only says "conflict".
                raise WorkspacePathError(
                    "path_not_found", f"{file_patch.path!r} does not exist"
                )
            if file_patch.kind == "delete":
                staged.append(
                    _StagedWrite(
                        destination=target,
                        remove_original=None,
                        original=original,
                        new_text=None,
                        change="delete",
                    )
                )
                continue
            destination = (
                self._resolve(file_patch.move_to) if file_patch.move_to else target
            )
            moved = destination.absolute != target.absolute
            staged.append(
                _StagedWrite(
                    destination=destination,
                    remove_original=target if moved else None,
                    original=original,
                    new_text=apply_to_text(original, file_patch),
                    change="update",
                )
            )
        return staged

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

    def _search_tool(self) -> StructuredTool:
        plugin = self

        def search_text(query: str, glob: str | None = None) -> dict[str, Any]:
            try:
                outcome = search_module.search_workspace(
                    plugin._root(), query, glob=glob
                )
            except search_module.SearchPatternError as exc:
                return tool_error("invalid_arguments", str(exc))
            return {
                "query": query,
                "matches": [
                    {
                        "path": item.path,
                        "line": item.line,
                        "column": item.column,
                        "text": item.text,
                    }
                    for item in outcome.matches
                ],
                "files_scanned": outcome.files_scanned,
                "truncated": outcome.truncated,
                "engine": outcome.engine,
            }

        return StructuredTool.from_function(
            func=search_text,
            name="search_text",
            description=(
                "Search file contents across the workspace with a Python "
                "regular expression. Use it to locate call sites, definitions "
                "and configuration instead of walking directories by hand. "
                "Common vendored and cache directories (.git, .venv, "
                "node_modules, __pycache__, dist, build) are skipped, as are "
                "binary and oversized files, so a zero-result search means "
                "'not found in searchable files', not 'not anywhere'. Results "
                'are capped; check \'truncated\'. Returns {"query": str, '
                '"matches": [{"path": str, "line": int, "column": int, '
                '"text": str}], "files_scanned": int, "truncated": bool, '
                '"engine": \'python\'|\'ripgrep\'}, or {"error": {code, '
                "message, retryable, details}} when the query is not a valid "
                "regular expression."
            ),
            args_schema=SearchTextArgs,
        )

    def _patch_tool(self) -> StructuredTool:
        plugin = self

        def apply_patch(patch: str) -> dict[str, Any]:
            try:
                plan = parse_patch(patch)
                staged = plugin._stage(plan)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            except PatchError as exc:
                return tool_error(exc.code, exc.message)
            # Every file's outcome is known; only now does anything change.
            changed: list[dict[str, Any]] = []
            for item in staged:
                if item.new_text is None:
                    item.destination.absolute.unlink(missing_ok=True)
                else:
                    item.destination.absolute.parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    item.destination.absolute.write_text(
                        item.new_text, encoding="utf-8"
                    )
                if item.remove_original is not None:
                    # A move keeps the old copy until the new one is on disk,
                    # so a crash in between never loses both.
                    item.remove_original.absolute.unlink(missing_ok=True)
                before = digest(item.original) if item.original is not None else None
                after = digest(item.new_text) if item.new_text is not None else None
                changed.append(
                    {
                        "path": item.destination.relative,
                        "change": item.change,
                        "before_sha256": before,
                        "after_sha256": after,
                    }
                )
                if plugin._state is not None:
                    plugin._state.record_mutation(
                        item.destination.relative,
                        "apply_patch",
                        before_sha256=before,
                        after_sha256=after,
                    )
            generation = plugin._state.get_generation() if plugin._state else 0
            return {"changed": changed, "generation": generation}

        return StructuredTool.from_function(
            func=apply_patch,
            name="apply_patch",
            description=(
                "Apply a patch document to the workspace. This is the way to "
                "modify files: it reports the before/after sha256 of every file "
                "it touched, which later verification is tied to. The patch is "
                "applied atomically — if any hunk fails to match, nothing is "
                "written at all — so a single call either lands completely or "
                "not at all. Format: '*** Begin Patch', then one or more "
                "sections, then '*** End Patch'. Section headers are "
                "'*** Update File: <path>' (with optional '*** Move to: <path>' "
                "to rename), '*** Add File: <path>' and '*** Delete File: "
                "<path>'. Within an update, lines are prefixed with a space "
                "(context), '-' (remove) or '+' (add); an '@@' line starts a "
                "new hunk and its trailing text anchors where the hunk "
                "applies. Context must match the current file exactly, so read "
                "the file first. Returns {\"changed\": [{\"path\": str, "
                "\"change\": 'update'|'add'|'delete', \"before_sha256\": "
                "str|null, \"after_sha256\": str|null}], \"generation\": int}. "
                "On {\"error\": {code, message, retryable, details}} the "
                "workspace is left untouched."
            ),
            args_schema=PatchArgs,
        )

    def _status_tool(self) -> StructuredTool:
        plugin = self

        def workspace_status() -> dict[str, Any]:
            root = plugin._root()
            touched = (
                {record.path for record in plugin._state.mutations()}
                if plugin._state is not None
                else set()
            )
            generation = plugin._state.get_generation() if plugin._state else 0
            branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
            if branch is None:
                return {
                    "is_git_repo": False,
                    "branch": None,
                    "head": None,
                    "generation": generation,
                    "dirty": [],
                    "truncated": False,
                }
            head = _git(root, "rev-parse", "HEAD") or ""
            porcelain = _git(root, "status", "--porcelain") or ""
            dirty: list[dict[str, str]] = []
            truncated = False
            for raw in porcelain.splitlines():
                if len(dirty) >= plugin._max_entries:
                    truncated = True
                    break
                status, raw_path = raw[:2], raw[3:]
                path = raw_path.strip().strip('"')
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                dirty.append(
                    {
                        "path": path,
                        "status": status,
                        "origin": "agent" if path in touched else "pre_existing",
                    }
                )
            return {
                "is_git_repo": True,
                "branch": branch,
                "head": head,
                "generation": generation,
                "dirty": dirty,
                "truncated": truncated,
            }

        return StructuredTool.from_function(
            func=workspace_status,
            name="workspace_status",
            description=(
                "Show the workspace's git state and which changes this agent "
                "made versus which were already there. Check it before claiming "
                "a clean tree, and before reverting anything: an entry marked "
                "'pre_existing' is the user's work and must not be touched. "
                "'generation' is the workspace mutation counter — it increases "
                "on every write, and a verification report is only valid for "
                "the generation it was produced at. Returns {\"is_git_repo\": "
                "bool, \"branch\": str|null, \"head\": str|null, "
                "\"generation\": int, \"dirty\": [{\"path\": str, \"status\": "
                "str (two-letter git porcelain code), \"origin\": "
                "'agent'|'pre_existing'}], \"truncated\": bool}."
            ),
            args_schema=NoArgs,
        )
