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


def _content(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("content", ""))
    return str(getattr(message, "content", ""))


def _size(message: Any) -> int:
    if isinstance(message, Mapping):
        value: Any = dict(message)
    else:
        value = getattr(message, "model_dump", lambda: str(message))()
    return len(json.dumps(value, ensure_ascii=False, default=str).encode())


def _field(message: Any, name: str) -> Any:
    if isinstance(message, Mapping):
        return message.get(name)
    return getattr(message, name, None)


def _tool_groups(source: list[Any]) -> list[list[int]]:
    """Group an assistant call and all of its matching tool results atomically."""
    parent = list(range(len(source)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owners: dict[str, int] = {}
    for index, message in enumerate(source):
        calls = _field(message, "tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            call_id = _field(call, "id")
            if isinstance(call_id, str) and call_id:
                owners[call_id] = index
    for index, message in enumerate(source):
        call_id = _field(message, "tool_call_id")
        if isinstance(call_id, str) and call_id in owners:
            parent[root(index)] = root(owners[call_id])

    groups: dict[int, list[int]] = {}
    for index in range(len(source)):
        groups.setdefault(root(index), []).append(index)
    return list(groups.values())


def compress_messages(
    messages: Iterable[Any], *, budget_bytes: int
) -> list[Any]:
    """Keep newest conversation plus marked control evidence under a byte budget."""
    if budget_bytes < 1:
        raise ValueError("budget_bytes must be positive")
    source = list(messages)
    groups = _tool_groups(source)
    marked = [
        group
        for group in groups
        if any(
            marker in _content(source[index]).lower()
            for index in group
            for marker in PRESERVED_MARKERS
        )
    ]
    selected: set[int] = set()
    used = 0
    required_bytes = sum(_size(source[index]) for group in marked for index in group)
    if required_bytes > budget_bytes:
        raise ValueError("control evidence exceeds context budget")
    for group in [*marked, *sorted(groups, key=lambda item: item[-1], reverse=True)]:
        if group[0] in selected:
            continue
        size = sum(_size(source[index]) for index in group)
        if used + size > budget_bytes:
            continue
        selected.update(group)
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
