"""Typed SDK generation from scoped LangChain tools."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from langharness_core.plugins.presentation.sdk import render_tool_sdk


class ReadArgs(BaseModel):
    path: str
    start_line: int = 1


class SearchOptions(BaseModel):
    case_sensitive: bool = False


class SearchArgs(BaseModel):
    pattern: str
    options: SearchOptions = Field(default_factory=SearchOptions)


def tool(
    name: str, schema: type[BaseModel], output: dict[str, Any] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        tool_call_schema=schema,
        metadata={"output_schema": output} if output else {},
    )


def test_sdk_is_stable_sorted_and_never_exposes_run_code() -> None:
    read = tool("read_file", ReadArgs)
    search = tool("search_text", SearchArgs)
    run_code = tool("run_code", ReadArgs)

    first = render_tool_sdk([search, run_code, read])
    second = render_tool_sdk([read, search])

    assert first == second
    assert '"run_code"' not in first
    assert first.index('"read_file"') < first.index('"search_text"')
    assert '"path": string' in first
    assert '"start_line"?: number' in first
    assert '"case_sensitive"?: boolean' in first
    assert '"read_file": unknown' in first


def test_sdk_uses_declared_output_schema_without_guessing() -> None:
    read = tool(
        "read_file",
        ReadArgs,
        {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string"},
                "truncated": {"type": "boolean"},
            },
        },
    )

    sdk = render_tool_sdk([read])

    assert '"read_file": { "content": string; "truncated"?: boolean; }' in sdk
    assert "Promise<ToolOutputMap[K]>" in sdk


def test_sdk_changes_with_scope_visible_tools() -> None:
    alpha = render_tool_sdk([tool("read_file", ReadArgs)])
    beta = render_tool_sdk([tool("search_text", SearchArgs)])

    assert '"read_file"' in alpha and '"search_text"' not in alpha
    assert '"search_text"' in beta and '"read_file"' not in beta


def test_nested_schema_and_union_render_without_losing_required_fields() -> None:
    sdk = render_tool_sdk([tool("search_text", SearchArgs)])
    assert '"pattern": string' in sdk
    assert '"options"?: { "case_sensitive"?: boolean; }' in sdk


def test_duplicate_names_are_rejected_before_a_schema_can_be_shadowed() -> None:
    with pytest.raises(ValueError, match="Duplicate SDK tool: read_file"):
        render_tool_sdk([tool("read_file", ReadArgs), tool("read_file", SearchArgs)])


def test_unsupported_output_schema_remains_unknown() -> None:
    sdk = render_tool_sdk(
        [tool("read_file", ReadArgs, {"$ref": "#/missing/definition"})]
    )
    assert '"read_file": unknown' in sdk
