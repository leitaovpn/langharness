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
        message
        for message in source
        if any(marker in str(message.get("content", "")).lower() for marker in PRESERVED_MARKERS)
    ]
    result: list[dict[str, Any]] = []
    used = 0
    for message in [*marked, *reversed(source)]:
        if message in result:
            continue
        size = _size(message)
        if used + size > budget_bytes:
            continue
        result.append(message)
        used += size
    return list(reversed(result))
