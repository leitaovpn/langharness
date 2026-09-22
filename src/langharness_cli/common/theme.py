"""Styling for the interactive CLI.

One definition for the prompt, the toolbar and the palette menu, so the
palette does not drift from the rest of the shell. Colours are chosen to
stay legible on both light and dark terminals: the menu paints its own
background rather than inheriting the terminal's.

Rules are given as ``(classname, style)`` tuples rather than through
``Style.from_dict``. Descendant selectors such as ``"a.b c.d"`` are not
expressible as dict keys -- ``from_dict`` splits the key on whitespace and
tries to parse the tail as a colour. This mirrors how prompt_toolkit's own
``styles/defaults.py`` declares its rules.
"""

from __future__ import annotations

from prompt_toolkit.styles import Style

MENU_BACKGROUND = "bg:#1c1c1c"
MENU_FOREGROUND = "#c8c8c8"
SELECTED_BACKGROUND = "bg:#005faf"
SELECTED_FOREGROUND = "#ffffff"
META_FOREGROUND = "#808080"
MATCH_FOREGROUND = "#5fd7ff"

#: Menu row styles, and the descendants that mark fuzzy-matched characters.
#: prompt_toolkit resolves these by walking the row's inheritance chain, so
#: each combination has to be listed explicitly.
_ROWS: dict[str, str] = {
    "completion-menu": f"{MENU_BACKGROUND} {MENU_FOREGROUND}",
    "completion-menu.completion": f"{MENU_BACKGROUND} {MENU_FOREGROUND}",
    # `noreverse` is not decoration: prompt_toolkit's own default for the
    # current row is "fg:#888888 bg:#ffffff reverse", and style merging is
    # per-attribute, so an unmentioned `reverse` stays on and inverts the
    # colours we just chose.
    "completion-menu.completion.current": (
        f"{SELECTED_BACKGROUND} {SELECTED_FOREGROUND} bold noreverse"
    ),
    "completion-menu.meta.completion": f"{MENU_BACKGROUND} {META_FOREGROUND}",
    "completion-menu.meta.completion.current": (
        f"{SELECTED_BACKGROUND} {SELECTED_FOREGROUND} noreverse"
    ),
}

_FUZZY_SUFFIXES = {
    "fuzzymatch.inside": "bold",
    "fuzzymatch.inside.character": "bold underline",
}


def build_palette_style() -> Style:
    """Return the style used by the interactive shell and its palette."""
    rules: list[tuple[str, str]] = [
        ("prompt", "bold ansicyan"),
        ("bottom-toolbar", "bg:#222222 #aaaaaa"),
    ]
    rules.extend(_ROWS.items())
    for row, outside, inside in (
        ("completion-menu.completion", MENU_FOREGROUND, MATCH_FOREGROUND),
        # On the selected row both halves sit on the selection background,
        # so the matched characters are marked by weight rather than hue.
        ("completion-menu.completion.current", SELECTED_FOREGROUND, SELECTED_FOREGROUND),
    ):
        rules.append((f"{row} fuzzymatch.outside", outside))
        rules.extend(
            (f"{row} {suffix}", f"{inside} {decorations}")
            for suffix, decorations in _FUZZY_SUFFIXES.items()
        )
    return Style(rules)
