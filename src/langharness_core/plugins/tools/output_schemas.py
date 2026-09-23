"""Declared JSON return shapes for the code workspace tools.

The declarations describe both success and structured tool errors. They are
SDK metadata only; the tools still return their canonical Python dictionaries.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter


class _ToolError(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any]


class _Failure(BaseModel):
    error: _ToolError


class _DirectoryEntry(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory", "symlink", "other"]
    size: int


class _ListResult(BaseModel):
    path: str
    entries: list[_DirectoryEntry]
    truncated: bool


class _ReadResult(BaseModel):
    path: str
    text: str
    start_line: int
    end_line: int
    total_lines: int
    sha256: str
    truncated: bool


class _SearchMatch(BaseModel):
    path: str
    line: int
    column: int
    text: str


class _SearchResult(BaseModel):
    query: str
    matches: list[_SearchMatch]
    files_scanned: int
    truncated: bool
    engine: Literal["python", "ripgrep"]


class _PatchChange(BaseModel):
    path: str
    change: Literal["add", "update", "delete"]
    before_sha256: str | None
    after_sha256: str | None


class _PatchResult(BaseModel):
    changed: list[_PatchChange]
    generation: int


class _DirtyPath(BaseModel):
    path: str
    status: str
    origin: Literal["agent", "pre_existing"]


class _StatusResult(BaseModel):
    is_git_repo: bool
    branch: str | None
    head: str | None
    generation: int
    dirty: list[_DirtyPath]
    truncated: bool


OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_directory": TypeAdapter(_ListResult | _Failure).json_schema(),
    "read_file": TypeAdapter(_ReadResult | _Failure).json_schema(),
    "search_text": TypeAdapter(_SearchResult | _Failure).json_schema(),
    "apply_patch": TypeAdapter(_PatchResult | _Failure).json_schema(),
    "workspace_status": _StatusResult.model_json_schema(),
}
