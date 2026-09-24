"""Tests for deterministic bounded context compression."""

from typing import Any, cast

import pytest

from langharness_core.plugins.context.compression import (
    ContextCompressionTransformer,
    compress_messages,
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


def test_compression_keeps_original_order_when_budget_allows() -> None:
    messages = [
        {"role": "user", "content": "goal: build"},
        {"role": "assistant", "content": "next"},
    ]
    assert compress_messages(messages, budget_bytes=1000) == messages


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
