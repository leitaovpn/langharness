"""Deterministic ordering, budgeting and joining of prompt sections."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from langharness_core.common.text import digest, truncate_bytes
from langharness_core.contracts import PromptSection

LOGGER = logging.getLogger("langharness.prompt")

DEFAULT_TOTAL_BUDGET = 65_536
SEPARATOR = "\n\n"
SEPARATOR_BYTES = len(SEPARATOR.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class SectionTrace:
    """What one section contributed to one assembled prompt.

    ``sha256`` digests the text the provider produced, so it changes only when
    that provider's content changes; ``bytes`` and ``omitted_bytes`` describe
    what actually reached the model after budgeting.
    """

    name: str
    order: int
    source: str
    bytes: int
    sha256: str
    omitted_bytes: int = 0
    shadowed: bool = False
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class AssembledPrompt:
    """The system prompt for one model step, plus its provenance."""

    text: str
    traces: tuple[SectionTrace, ...]

    @property
    def empty(self) -> bool:
        return not self.text


def _partition(
    sections: Iterable[PromptSection],
) -> tuple[list[PromptSection], list[PromptSection]]:
    """Return (kept, shadowed) after sorting and deduplicating by name."""
    ordered = sorted(sections, key=lambda item: (item.order, item.name, item.source))
    kept: list[PromptSection] = []
    shadowed: list[PromptSection] = []
    seen: set[str] = set()
    for item in ordered:
        if item.name in seen:
            shadowed.append(item)
            LOGGER.warning(
                "prompt section %r from %s is shadowed by an earlier section "
                "with the same name",
                item.name,
                item.source,
            )
            continue
        seen.add(item.name)
        kept.append(item)
    return kept, shadowed


def assemble_sections(
    sections: Iterable[PromptSection],
    *,
    total_budget: int = DEFAULT_TOTAL_BUDGET,
) -> AssembledPrompt:
    """Order, deduplicate, budget and join sections into one system prompt.

    ``total_budget`` bounds the section text.  Sections that do not fit are
    dropped whole, and a single trailing note names them so the model can tell
    a missing instruction from one that never existed.
    """
    kept, shadowed = _partition(sections)
    parts: list[str] = []
    traces: list[SectionTrace] = []
    dropped: list[str] = []
    used = 0
    for item in kept:
        if not item.text:
            # An empty contribution is not a budget casualty; naming it in the
            # drop notice would make the notice a lie about why it is there.
            continue
        separator = SEPARATOR_BYTES if parts else 0
        allowance = total_budget - used - separator
        if item.max_bytes is not None:
            allowance = min(allowance, item.max_bytes)
        if allowance > 0:
            text, omitted = truncate_bytes(item.text, allowance)
        else:
            text, omitted = "", len(item.text.encode("utf-8"))
        if not text:
            dropped.append(item.name)
            traces.append(
                SectionTrace(
                    name=item.name,
                    order=item.order,
                    source=item.source,
                    bytes=0,
                    sha256=digest(item.text),
                    omitted_bytes=len(item.text.encode("utf-8")),
                    dropped=True,
                )
            )
            continue
        parts.append(text)
        used += separator + len(text.encode("utf-8"))
        traces.append(
            SectionTrace(
                name=item.name,
                order=item.order,
                source=item.source,
                bytes=len(text.encode("utf-8")),
                sha256=digest(item.text),
                omitted_bytes=omitted,
            )
        )
    traces.extend(
        SectionTrace(
            name=item.name,
            order=item.order,
            source=item.source,
            bytes=0,
            sha256=digest(item.text),
            omitted_bytes=len(item.text.encode("utf-8")),
            shadowed=True,
        )
        for item in shadowed
    )
    text = SEPARATOR.join(parts)
    if dropped:
        note = f"[prompt budget exhausted: dropped sections: {', '.join(dropped)}]"
        text = f"{text}{SEPARATOR}{note}" if text else note
    return AssembledPrompt(text=text, traces=tuple(traces))
