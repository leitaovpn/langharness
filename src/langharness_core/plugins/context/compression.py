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


def estimate_tokens(value: Any) -> int:
    """Estimate tokens conservatively without requiring a model tokenizer.

    Four UTF-8 bytes is a deliberately conservative approximation for ordinary
    text.  The estimate is only used to enforce an upper bound; providers may
    report a smaller or larger exact count independently.
    """
    if isinstance(value, (str, bytes)):
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
    else:
        raw = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    return max(1, (len(raw) + 3) // 4)


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


def _is_control_evidence(message: Any) -> bool:
    """Recognize structured audit/goal evidence before legacy text markers."""
    kind = _field(message, "type")
    status = _field(message, "status")
    if isinstance(kind, str) and kind in {
        "goal",
        "goal_status",
        "verification",
        "verification_report",
        "audit",
        "approval_audit",
    }:
        return True
    if isinstance(status, str) and status in {
        "unfinished",
        "required",
        "decision_submitted",
        "decision_applied",
        "verified",
    }:
        return True
    return any(
        marker in _content(message).lower() for marker in PRESERVED_MARKERS
    )


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
    orphan_results = [
        index
        for index, message in enumerate(source)
        if isinstance(_field(message, "tool_call_id"), str)
        and _field(message, "tool_call_id") not in owners
    ]
    if orphan_results:
        raise ValueError("context contains a tool result without a matching call")
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
        if any(_is_control_evidence(source[index]) for index in group)
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


def compress_messages_tokens(
    messages: Iterable[Any], *, budget_tokens: int
) -> list[Any]:
    """Compress messages under a dependency-free approximate token budget."""
    if budget_tokens < 1:
        raise ValueError("budget_tokens must be positive")
    source = list(messages)
    # Reuse the byte-budget algorithm while accounting with the same
    # conservative four-bytes-per-token bound used by ``estimate_tokens``.
    return compress_messages(source, budget_bytes=budget_tokens * 4)


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
