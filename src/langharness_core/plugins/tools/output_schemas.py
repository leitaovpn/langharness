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


class _ExecResult(BaseModel):
    command: list[str]
    cwd: str
    exit_code: int
    output: str
    truncated: bool
    duration_ms: int


class _StartedJob(BaseModel):
    job_id: str
    state: str
    command: list[str]
    cwd: str


class _WaitedJob(BaseModel):
    job_id: str
    state: str
    exit_code: int | None
    output: str
    truncated: bool
    new_output: bool


class _WrittenStdin(BaseModel):
    job_id: str
    state: str
    written: int


class _StoppedJob(BaseModel):
    job_id: str
    state: str
    exit_code: int | None


class _Criterion(BaseModel):
    id: str
    text: str
    evidence: str | None


class _Goal(BaseModel):
    id: str
    revision: int
    objective: str
    status: str
    rounds_started: int
    max_rounds: int
    acceptance_criteria: list[_Criterion]
    blocker_history: list[str]


class _GoalResult(BaseModel):
    goal: _Goal | None


class _GoalAction(BaseModel):
    status: Literal["complete", "blocked"]
    goal_id: str
    revision: int
    summary: str
    report_id: str | None


class _VerificationCheck(BaseModel):
    name: str
    exit_code: int
    duration_ms: int


class _VerificationResult(BaseModel):
    report_id: str
    goal_revision: int
    mutation_generation: int
    passed: bool
    checks: list[_VerificationCheck]
    would_complete: bool
    blockers: list[str]


class _AskUserResult(BaseModel):
    status: Literal["awaiting_user"]
    goal_id: str
    question: str
    note: str


OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_directory": TypeAdapter(_ListResult | _Failure).json_schema(),
    "read_file": TypeAdapter(_ReadResult | _Failure).json_schema(),
    "search_text": TypeAdapter(_SearchResult | _Failure).json_schema(),
    "apply_patch": TypeAdapter(_PatchResult | _Failure).json_schema(),
    "workspace_status": _StatusResult.model_json_schema(),
    "exec_command": TypeAdapter(_ExecResult | _StartedJob | _Failure).json_schema(),
    "wait_job": TypeAdapter(_WaitedJob | _Failure).json_schema(),
    "write_stdin": TypeAdapter(_WrittenStdin | _Failure).json_schema(),
    "stop_job": TypeAdapter(_StoppedJob | _Failure).json_schema(),
    "get_goal": _GoalResult.model_json_schema(),
    "finish_goal": TypeAdapter(_GoalAction | _Failure).json_schema(),
    "verify": TypeAdapter(_VerificationResult | _Failure).json_schema(),
    "ask_user": TypeAdapter(_AskUserResult | _Failure).json_schema(),
}
