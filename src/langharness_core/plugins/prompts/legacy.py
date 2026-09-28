"""Adapter that keeps ``SystemPromptProvider`` plugins working.

Legacy providers are joined into a single section at order 1000 -- after the
authored persona sections but before tool guidance.  They are joined with a
single newline, which is exactly what the pre-assembler loop did, so an agent
whose only prompt source is a legacy provider produces a byte-identical system
prompt.  Giving each provider its own section instead would insert the
assembler's blank-line separator and change that prompt.

``source`` lists every contributing provider so a trace still answers "who put
this text there"; per-provider traces return once a plugin migrates to
``PromptSectionProvider``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langharness_core.contracts import PromptSection

LEGACY_ORDER = 1000
LEGACY_JOIN = "\n"


def legacy_sections(providers: Iterable[Any] | None) -> list[PromptSection]:
    """Merge every legacy provider's string into one ordered section."""
    parts: list[str] = []
    sources: list[str] = []
    for provider in providers or ():
        text = provider.get_system_prompt()
        if not text:
            continue
        info = provider.get_plugin_info()
        name = str(info.get("name") or "system-prompt")
        version = str(info.get("version") or "")
        parts.append(text)
        sources.append(f"{name}@{version}" if version else name)
    if not parts:
        return []
    return [
        PromptSection(
            name="legacy",
            order=LEGACY_ORDER,
            text=LEGACY_JOIN.join(parts),
            source=", ".join(sources),
        )
    ]
