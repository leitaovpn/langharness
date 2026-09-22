"""prompt_toolkit adapter for the interactive command palette.

This is the only module that knows about prompt_toolkit's completion API.
Matching and ordering live in :mod:`langharness_cli.common.palette`.

The highlighter reuses prompt_toolkit's built-in ``fuzzymatch.*`` style
classes, whose default rules are written against the enclosing
``completion-menu`` row style, so no extra styling is needed here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText

from langharness_cli.common.palette import Candidate, Scored, rank
from langharness_cli.contracts import InteractiveCommandSpec

#: Supplies candidates for one command's argument slot. Receives the
#: argument words already typed before the cursor and the word being typed,
#: so a command with a nested grammar can tell which slot it is in.
ArgumentSource = Callable[[Sequence[str], str], Iterable[str]]


def _highlight(scored: Scored) -> FormattedText | str:
    """Render a candidate name with its matched characters marked."""
    name = scored.candidate.name
    if not scored.hits:
        return name
    matched = set(scored.hits)
    return FormattedText(
        [
            (
                "class:fuzzymatch.inside.character"
                if index in matched
                else "class:fuzzymatch.outside",
                character,
            )
            for index, character in enumerate(name)
        ]
    )


class PaletteCompleter(Completer):
    """Completes ``/command`` names and, after them, their first argument."""

    def __init__(
        self,
        commands: Mapping[str, InteractiveCommandSpec],
        argument_sources: Mapping[str, ArgumentSource] | None = None,
    ) -> None:
        self._candidates = [
            Candidate(name=f"/{name}", description=spec.help)
            for name, spec in commands.items()
        ]
        self._argument_sources = dict(argument_sources or {})

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        del complete_event  # The palette ranks synchronously in-process.
        head = document.text_before_cursor
        if not head.startswith("/"):
            return
        body = head[1:]
        if " " not in body:
            yield from self._command_completions(head)
            return
        name, _, rest = body.partition(" ")
        source = self._argument_sources.get(name)
        if source is None:
            return
        # A trailing space means the next slot is starting, so the prefix is
        # empty rather than missing.
        words = rest.split(" ")
        prefix = words.pop()
        # Argument candidates are filtered by plain prefix rather than the
        # fuzzy matcher used for commands: values like provider names or
        # paths are an open set where a scattered match is more often wrong
        # than helpful.
        wanted = prefix.lower()
        for value in source(words, prefix):
            if value.lower().startswith(wanted):
                yield Completion(value, start_position=-len(prefix))

    def _command_completions(self, head: str) -> Iterable[Completion]:
        # A lone "/" is the trigger, not a filter: ranking everything against
        # it would order the list by name length, which reads as arbitrary.
        query = "" if head == "/" else head
        for scored in rank(query, self._candidates):
            yield Completion(
                scored.candidate.name,
                start_position=-len(head),
                display=_highlight(scored),
                display_meta=scored.candidate.description,
            )
