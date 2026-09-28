"""Tests for the shared tool error shape."""

from __future__ import annotations

from langharness_core.common.tool_error import tool_error


def test_error_shape_is_stable() -> None:
    assert tool_error("path_not_found", "no such file") == {
        "error": {
            "code": "path_not_found",
            "message": "no such file",
            "retryable": False,
            "details": {},
        }
    }


def test_retryable_and_details_are_carried_through() -> None:
    result = tool_error(
        "patch_conflict", "context missing", retryable=True, path="src/a.py", line=12
    )
    assert result["error"]["retryable"] is True
    assert result["error"]["details"] == {"path": "src/a.py", "line": 12}
