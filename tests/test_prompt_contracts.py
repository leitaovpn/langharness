"""Contract tests for the ordered prompt-section service."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_PROMPT_SECTION,
    PromptContext,
    PromptSection,
    PromptSectionProvider,
)
from langharness_plugin.validation import contract_for, validate


class ConformingProvider:
    """Smallest service that satisfies the prompt-section contract."""

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        return [
            PromptSection(
                name="identity", order=0, text=context.workspace, source="test"
            )
        ]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": "conforming", "version": "1.0.0"}


class MissingPluginInfo:
    """Violates the contract by omitting ``get_plugin_info``."""

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        return []


def test_specification_is_pinned_to_the_protocol() -> None:
    assert contract_for(SPEC_PROMPT_SECTION) is PromptSectionProvider
    assert SPEC_PROMPT_SECTION in ALL_PLUGIN_SPECS


def test_pin_does_not_pollute_protocol_members() -> None:
    assert "__SPECIFICATION__" not in getattr(
        PromptSectionProvider, "__protocol_attrs__"
    )


def test_conforming_provider_has_no_violations() -> None:
    assert validate(ConformingProvider(), PromptSectionProvider) == ()


def test_missing_method_is_reported() -> None:
    violations = validate(MissingPluginInfo(), PromptSectionProvider)
    assert [item.method for item in violations] == ["get_plugin_info"]
    assert violations[0].code == "MISSING_METHOD"


def test_isinstance_works_against_the_protocol() -> None:
    assert isinstance(ConformingProvider(), PromptSectionProvider)


def test_prompt_section_defaults_to_no_per_section_budget() -> None:
    section = PromptSection(name="n", order=1, text="t", source="s")
    assert section.max_bytes is None
    assert hash(section) == hash(PromptSection(name="n", order=1, text="t", source="s"))


def test_prompt_context_defaults_to_no_touched_paths() -> None:
    context = PromptContext(workspace="/w")
    assert context.touched_paths == ()
    assert context.model is None
