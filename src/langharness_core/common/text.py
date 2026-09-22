"""Text helpers shared by prompt assembly and instruction discovery."""

from __future__ import annotations

import hashlib

TRUNCATION_NOTICE = "\n\n[truncated: {omitted} bytes omitted]"


def digest(text: str) -> str:
    """Return a stable, prefixed content digest for traces and cache keys."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def truncate_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes.

    The cut prefers a line boundary and appends a notice stating how many bytes
    were dropped, so the returned text never exceeds ``max_bytes``.  A budget
    too small to hold the notice yields an empty string instead of an
    over-budget one; the caller still learns the full byte count from the
    second element of the tuple.

    Room is reserved against a worst-case notice rather than the final one: the
    digit count of ``omitted`` is not known until after the cut, and reserving
    the shorter form would let the notice push the result past ``max_bytes``.
    """
    encoded = text.encode("utf-8")
    total = len(encoded)
    if total <= max_bytes:
        return text, 0
    reserve = TRUNCATION_NOTICE.format(omitted="9" * len(str(total)))
    room = max_bytes - len(reserve.encode("utf-8"))
    if room <= 0:
        return "", total
    # A multi-byte character may straddle the cut; dropping it keeps the
    # result decodable.
    head = encoded[:room].decode("utf-8", errors="ignore")
    if "\n" in head:
        head = head[: head.rindex("\n") + 1]
    omitted = total - len(head.encode("utf-8"))
    return head + TRUNCATION_NOTICE.format(omitted=omitted), omitted
