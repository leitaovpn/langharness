"""Deterministic message compression for bounded checkpoints."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

PRESERVED_MARKERS = (
    "goal",
    "verification",
    "audit",
    "approval",
    "unfinished",
)


def _size(message: Mapping[str, Any]) -> int:
    return len(json.dumps(dict(message), ensure_ascii=False, default=str).encode())


def compress_messages(
    messages: Iterable[Mapping[str, Any]], *, budget_bytes: int
) -> list[dict[str, Any]]:
    """Keep newest conversation plus marked control evidence under a byte budget."""
    if budget_bytes < 1:
        raise ValueError("budget_bytes must be positive")
    source = [dict(message) for message in messages]
    marked = [
        index
        for index, message in enumerate(source)
        if any(marker in str(message.get("content", "")).lower() for marker in PRESERVED_MARKERS)
    ]
    selected: set[int] = set()
    used = 0
    for index in [*marked, *reversed(range(len(source)))]:
        if index in selected:
            continue
        size = _size(source[index])
        if used + size > budget_bytes:
            continue
        selected.add(index)
        used += size
    return [source[index] for index in sorted(selected)]


class ContextCompressionTransformer:
    """Pure state projection; not a LangGraph stream transformer factory."""

    def __init__(self, budget_bytes: int) -> None:
        if budget_bytes < 1:
            raise ValueError("budget_bytes must be positive")
        self.budget_bytes = budget_bytes

    def __call__(self, state: Mapping[str, Any]) -> dict[str, Any]:
        messages = state.get("messages")
        if not isinstance(messages, Iterable) or isinstance(messages, (str, bytes)):
            return dict(state)
        return {**state, "messages": compress_messages(messages, budget_bytes=self.budget_bytes)}
