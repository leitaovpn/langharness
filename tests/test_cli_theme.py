"""The single source of truth for interactive CLI styling."""

from __future__ import annotations

from prompt_toolkit.styles import default_ui_style, merge_styles

from langharness_cli.common.theme import build_palette_style


def merged(style_str: str):  # type: ignore[no-untyped-def]
    """Resolve a class the way the running prompt does.

    prompt_toolkit merges the session style on top of its own defaults, and
    merging is per-attribute: an attribute the palette never mentions keeps
    its default value. Testing the palette in isolation hides that.
    """
    return merge_styles([default_ui_style(), build_palette_style()]).get_attrs_for_style_str(
        style_str
    )


def test_selected_row_is_not_inverted_by_the_default_style() -> None:
    attrs = merged("class:completion-menu.completion.current")

    assert attrs.reverse is False


def test_selected_completion_row_is_visually_distinct() -> None:
    style = build_palette_style()

    normal = style.get_attrs_for_style_str("class:completion-menu.completion")
    current = style.get_attrs_for_style_str(
        "class:completion-menu.completion.current"
    )

    assert current.bgcolor != normal.bgcolor


def test_matched_characters_stand_out_from_unmatched_ones() -> None:
    style = build_palette_style()

    inside = style.get_attrs_for_style_str(
        "class:completion-menu.completion class:fuzzymatch.inside.character"
    )
    outside = style.get_attrs_for_style_str(
        "class:completion-menu.completion class:fuzzymatch.outside"
    )

    assert (inside.color, inside.bold, inside.underline) != (
        outside.color,
        outside.bold,
        outside.underline,
    )


def test_prompt_and_toolbar_are_styled() -> None:
    style = build_palette_style()

    assert style.get_attrs_for_style_str("class:prompt").color is not None
    assert style.get_attrs_for_style_str("class:bottom-toolbar").bgcolor is not None
