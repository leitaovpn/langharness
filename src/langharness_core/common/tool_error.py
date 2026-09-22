"""The single error shape every code tool returns.

A tool that fails must hand the model something it can act on: a stable code,
a readable message, whether retrying could help, and the structured details.
A Python traceback is never a tool result — it is a bug in the tool.
"""

from __future__ import annotations

from typing import Any, Literal

ToolErrorCode = Literal[
    "invalid_arguments",
    "workspace_unavailable",
    "path_outside_workspace",
    "path_not_found",
    "not_a_file",
    "not_a_directory",
    "patch_invalid",
    "patch_conflict",
    "no_active_goal",
    "goal_revision_mismatch",
    "completion_rejected",
    "blocker_not_established",
    "internal_error",
]


def tool_error(
    code: ToolErrorCode,
    message: str,
    *,
    retryable: bool = False,
    **details: Any,
) -> dict[str, Any]:
    """Build the ``{"error": {code, message, retryable, details}}`` result."""
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details,
        }
    }
