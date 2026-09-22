"""Unit tests for deterministic prompt-section assembly."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from langharness_core.common.text import digest, truncate_bytes
from langharness_core.contracts import PromptSection
from langharness_core.plugins.prompts.sections import (
    DEFAULT_TOTAL_BUDGET,
    AssembledPrompt,
    assemble_sections,
)


def section(
    name: str, order: int, text: str, source: str = "s", **kwargs: Any
) -> PromptSection:
    return PromptSection(name=name, order=order, text=text, source=source, **kwargs)


def test_digest_is_a_prefixed_sha256() -> None:
    assert digest("abc") == (
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_truncate_leaves_short_text_untouched() -> None:
    assert truncate_bytes("abc", 10) == ("abc", 0)


def test_truncate_cuts_on_a_line_boundary_and_reports_omitted_bytes() -> None:
    text = "line one\n" * 10  # 90 bytes
    result, omitted = truncate_bytes(text, 45)
    assert result == "line one\n\n\n[truncated: 81 bytes omitted]"
    assert len(result.encode("utf-8")) <= 45
    assert omitted == 81


def test_truncate_keeps_utf8_valid_when_a_character_straddles_the_cut() -> None:
    text = "中文内容" * 20  # 240 bytes, 3 bytes per character
    result, omitted = truncate_bytes(text, 63)
    assert result.startswith("中文内容")
    assert len(result.encode("utf-8")) <= 63
    assert omitted == 210


def test_truncate_returns_empty_when_the_budget_cannot_hold_the_notice() -> None:
    assert truncate_bytes("abcdef", 3) == ("", 6)


def test_sections_are_ordered_by_order_then_name_then_source() -> None:
    assembled = assemble_sections(
        [section("b", 200, "B"), section("a", 100, "A"), section("c", 100, "C")]
    )
    assert assembled.text == "A\n\nC\n\nB"
    assert [trace.name for trace in assembled.traces] == ["a", "c", "b"]


def test_duplicate_names_keep_the_first_and_record_the_shadowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="langharness.prompt"):
        assembled = assemble_sections(
            [
                section("same", 10, "first", source="a"),
                section("same", 10, "second", source="b"),
            ]
        )
    assert assembled.text == "first"
    assert [(trace.name, trace.shadowed) for trace in assembled.traces] == [
        ("same", False),
        ("same", True),
    ]
    assert "same" in caplog.text


def test_per_section_budget_truncates_only_that_section() -> None:
    assembled = assemble_sections(
        [section("a", 0, "x" * 100, max_bytes=40), section("b", 1, "keep me")]
    )
    assert assembled.text.endswith("keep me")
    assert assembled.traces[0].omitted_bytes > 0
    assert assembled.traces[1].bytes == len("keep me")


def test_trace_hash_is_of_the_original_text_not_the_truncated_one() -> None:
    assembled = assemble_sections([section("a", 0, "x" * 100, max_bytes=40)])
    assert assembled.traces[0].sha256 == digest("x" * 100)
    assert assembled.traces[0].bytes < 100


def test_total_budget_drops_sections_that_do_not_fit() -> None:
    assembled = assemble_sections(
        [section("a", 0, "x" * 40), section("b", 1, "y" * 40)], total_budget=50
    )
    assert assembled.traces[0].dropped is False
    assert assembled.traces[1].dropped is True
    assert assembled.traces[1].bytes == 0
    assert assembled.text.startswith("x" * 40)
    assert "dropped sections: b" in assembled.text


def test_the_drop_notice_is_appended_outside_the_budget() -> None:
    assembled = assemble_sections([section("only", 0, "z" * 80)], total_budget=20)
    assert assembled.text == "[prompt budget exhausted: dropped sections: only]"
    assert len(assembled.text.encode("utf-8")) > 20


def test_empty_sections_are_skipped_without_a_drop_notice() -> None:
    assembled = assemble_sections([section("empty", 0, ""), section("b", 1, "kept")])
    assert assembled.text == "kept"
    assert [trace.name for trace in assembled.traces] == ["b"]


def test_empty_input_produces_an_empty_prompt() -> None:
    assembled = assemble_sections([])
    assert assembled == AssembledPrompt(text="", traces=())
    assert assembled.empty is True


def test_default_budget_is_64_kib() -> None:
    assert DEFAULT_TOTAL_BUDGET == 65_536
