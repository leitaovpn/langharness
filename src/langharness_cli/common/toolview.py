"""Turning a declared template and a call's arguments into transcript text.

Free of prompt_toolkit and rich on purpose: this is the part with rules worth
testing (what to do with a missing argument, a long value, a broken
template), and none of it needs a terminal to be checked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from typing import Any

#: A substituted value is collapsed to one line and cut here, so a template
#: cannot paste a whole file into the tool line.
VALUE_MAX = 60
#: An output line longer than this is cut; the rest is what ctrl+o is for.
OUTPUT_MAX = 100
#: How many lines of output the summary line reports before it stops reading.
COUNT_MAX = 999


@dataclass(frozen=True)
class OutputLine:
    """The text that follows ``⎿``, and whether more is being withheld."""

    text: str
    truncated: bool


def _collapse(value: Any) -> str:
    text = " ".join(str(value).split())
    return text[:VALUE_MAX] + "…" if len(text) > VALUE_MAX else text


def _fields(template: str) -> set[str] | None:
    try:
        return {
            name for _, name, _, _ in Formatter().parse(template) if name is not None
        }
    except ValueError:
        # Unbalanced braces and the like: refuse rather than guess.
        return None


def render_headline(template: str, args: Mapping[str, Any]) -> str | None:
    """Fill ``template`` from ``args``, or return ``None`` if it cannot be.

    Declining is deliberate. Substituting what is available would print
    ``install_plugin(real.echo → )`` or a bare ``None``; the caller's
    fallback -- the plain tool name -- reads better than either, and the full
    arguments are one ctrl+o away.
    """
    if not template:
        return None
    fields = _fields(template)
    if fields is None:
        return None
    if not fields:
        return template
    values = {key: _collapse(value) for key, value in args.items() if value is not None}
    if not fields <= values.keys():
        return None
    try:
        return template.format_map(values)
    except (KeyError, IndexError, ValueError):
        return None


def render_output(output: str) -> OutputLine:
    """Summarize a tool's output as the single line the transcript shows."""
    stripped = output.rstrip("\n")
    if not stripped:
        return OutputLine("", False)
    lines = stripped.split("\n")
    if len(lines) == 1 and len(lines[0]) <= OUTPUT_MAX:
        return OutputLine(lines[0], False)
    head = lines[0][:OUTPUT_MAX]
    if len(lines[0]) > OUTPUT_MAX:
        head += "…"
    if len(lines) > 1:
        count = min(len(lines), COUNT_MAX)
        head += f" … ({count} lines)"
    return OutputLine(head, True)
