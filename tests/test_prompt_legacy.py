"""Tests for the legacy SystemPromptProvider adapter."""

from __future__ import annotations

from dataclasses import dataclass

from langharness_core.plugins.prompts.legacy import LEGACY_ORDER, legacy_sections
from langharness_core.plugins.prompts.sections import assemble_sections


@dataclass
class FakePrompt:
    text: str
    name: str = "system-prompt-plugin"

    def get_system_prompt(self) -> str:
        return self.text

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self.name, "version": "1.0.0"}


def test_legacy_sections_collapse_into_one_ordered_section() -> None:
    sections = legacy_sections([FakePrompt("You are A."), FakePrompt("You are B.")])
    assert len(sections) == 1
    assert sections[0].name == "legacy"
    assert sections[0].order == LEGACY_ORDER


def test_joined_output_matches_the_old_string_concatenation() -> None:
    providers = [FakePrompt("You are A."), FakePrompt("You are B.")]
    assembled = assemble_sections(legacy_sections(providers))
    assert assembled.text == "You are A.\nYou are B."


def test_a_single_legacy_provider_is_passed_through_unchanged() -> None:
    assembled = assemble_sections(legacy_sections([FakePrompt("You are A.")]))
    assert assembled.text == "You are A."


def test_empty_providers_are_skipped() -> None:
    sections = legacy_sections([FakePrompt(""), FakePrompt("You are B.")])
    assert sections[0].text == "You are B."


def test_source_lists_every_contributing_provider() -> None:
    sections = legacy_sections(
        [FakePrompt("x", name="mine"), FakePrompt("y", name="theirs")]
    )
    assert sections[0].source == "mine@1.0.0, theirs@1.0.0"


def test_no_providers_yield_no_sections() -> None:
    assert legacy_sections([]) == []
    assert legacy_sections(None) == []
