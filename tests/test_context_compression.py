"""Tests for deterministic bounded context compression."""

import json
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from langharness_core.plugins.context.compression import (
    ContextCompressionTransformer,
    compress_messages,
    compress_messages_tokens,
    estimate_tokens,
)


def test_compression_preserves_control_evidence_and_newest_messages() -> None:
    messages = [
        {"role": "user", "content": "old " + "x" * 100},
        {"role": "tool", "content": "goal: finish report; audit ref=a1"},
        {"role": "assistant", "content": "new answer"},
        {"role": "tool", "content": "approval unfinished: waiting"},
    ]

    result = compress_messages(messages, budget_bytes=180)

    text = " ".join(str(item["content"]) for item in result)
    assert "goal: finish report" in text
    assert "approval unfinished" in text
    assert "new answer" in text
    assert sum(len(str(item).encode()) for item in result) <= 180


def test_compression_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError, match="positive"):
        compress_messages([], budget_bytes=0)


def test_token_budget_uses_conservative_estimate() -> None:
    messages = [{"role": "user", "content": "goal: preserve"}]
    assert estimate_tokens("abcd") == 1
    assert compress_messages_tokens(messages, budget_tokens=100) == messages
    with pytest.raises(ValueError, match="budget_tokens"):
        compress_messages_tokens(messages, budget_tokens=0)


def test_control_evidence_cannot_be_silently_dropped() -> None:
    messages = [{"role": "tool", "content": "approval unfinished: " + "x" * 200}]
    with pytest.raises(ValueError, match="control evidence"):
        compress_messages(messages, budget_bytes=50)


def test_compression_keeps_original_order_when_budget_allows() -> None:
    messages = [
        {"role": "user", "content": "goal: build"},
        {"role": "assistant", "content": "next"},
    ]
    assert compress_messages(messages, budget_bytes=1000) == messages


def test_compression_does_not_keep_tool_result_without_its_call() -> None:
    call = AIMessage(
        content="",
        tool_calls=[{"name": "lookup", "args": {"query": "x" * 100}, "id": "call-1"}],
    )
    result = ToolMessage(content="ok", tool_call_id="call-1")
    result_size = len(json.dumps(result.model_dump(), ensure_ascii=False).encode())

    assert compress_messages([call, result], budget_bytes=result_size) == []


def test_compression_rejects_orphan_tool_result() -> None:
    with pytest.raises(ValueError, match="without a matching call"):
        compress_messages(
            [{"role": "tool", "content": "stale", "tool_call_id": "missing"}],
            budget_bytes=1000,
        )


def test_marked_tool_result_requires_room_for_its_call() -> None:
    call = AIMessage(
        content="",
        tool_calls=[{"name": "verify", "args": {"path": "x" * 100}, "id": "call-1"}],
    )
    result = ToolMessage(content="verification: passed", tool_call_id="call-1")
    result_size = len(json.dumps(result.model_dump(), ensure_ascii=False).encode())

    with pytest.raises(ValueError, match="control evidence"):
        compress_messages([call, result], budget_bytes=result_size)


def test_multiple_results_from_one_call_are_preserved_together() -> None:
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call-1", "name": "first"},
            {"id": "call-2", "name": "second"},
        ],
    }
    first = {"role": "tool", "content": "first result", "tool_call_id": "call-1"}
    second = {"role": "tool", "content": "second result", "tool_call_id": "call-2"}
    full_size = sum(
        len(json.dumps(message, ensure_ascii=False).encode())
        for message in (call, first, second)
    )

    assert compress_messages([call, first, second], budget_bytes=full_size) == [
        call,
        first,
        second,
    ]
    assert compress_messages([call, first, second], budget_bytes=full_size - 1) == []


def test_transformer_round_trips_checkpoint_like_state() -> None:
    transformer = ContextCompressionTransformer(500)
    state = {
        "messages": [
            {"role": "user", "content": "goal: preserve this"},
            {"role": "assistant", "content": "answer"},
        ],
        "goal_id": "g1",
        "audit_refs": ["a1"],
    }

    compressed = transformer(state)

    assert compressed["goal_id"] == "g1"
    assert compressed["audit_refs"] == ["a1"]
    actual = cast(list[dict[str, Any]], compressed["messages"])
    expected = cast(list[dict[str, Any]], state["messages"])
    assert {str(item["content"]) for item in actual} == {
        str(item["content"]) for item in expected
    }
