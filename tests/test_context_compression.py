"""Tests for deterministic bounded context compression."""

import pytest

from langharness_core.plugins.context.compression import compress_messages


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
