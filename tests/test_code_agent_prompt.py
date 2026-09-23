"""Tests for the shipped code-agent prompt sections."""
# mypy: ignore-errors

from __future__ import annotations

from langharness_core.contracts import PromptContext, PromptSectionProvider
from langharness_core.plugin import AGENT_PLUGIN_CATALOG, agent_plugin_descriptor
from langharness_core.plugins.prompts.code_agent import (
    COMPLETION_ORDER,
    IDENTITY_ORDER,
    SAFETY_ORDER,
    SECTIONS,
    WORKFLOW_ORDER,
    CodeAgentPromptPlugin,
    load_section,
)
from langharness_plugin.validation import validate


def test_every_shipped_section_loads_and_is_non_empty() -> None:
    for name, _ in SECTIONS:
        assert load_section(name).strip()


def test_sections_have_the_designed_order() -> None:
    assert (IDENTITY_ORDER, SAFETY_ORDER, WORKFLOW_ORDER, COMPLETION_ORDER) == (
        0,
        100,
        200,
        300,
    )
    assert [order for _, order in SECTIONS] == sorted(order for _, order in SECTIONS)


def test_identity_and_completion_cover_the_required_points() -> None:
    assert "coding agent" in load_section("identity").lower()
    completion = load_section("completion").lower()
    assert "finish_goal" in completion
    assert "evidence" in completion
    assert "blocked" in completion


def test_safety_names_the_approval_and_secret_rules() -> None:
    safety = load_section("safety").lower()
    assert "workspace" in safety
    assert "secret" in safety or "token" in safety
    assert "git reset" in safety


def test_plugin_renders_sections_in_order(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path), model="gpt-5")
    )
    assert [item.name for item in sections] == [
        "identity",
        "safety",
        "workflow",
        "completion",
        "goal_round",
        "goal_wrapup",
    ]
    assert [item.order for item in sections] == [0, 100, 200, 300, 9000, 9001]
    assert "gpt-5" in sections[0].text
    assert str(tmp_path) in sections[0].text


def test_unknown_model_falls_back_to_a_placeholder(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert "unknown" in sections[0].text


def test_sources_point_at_the_shipped_files(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert all(item.source.endswith(".md") for item in sections)


def test_plugin_conforms_to_its_contract() -> None:
    assert validate(CodeAgentPromptPlugin(), PromptSectionProvider) == ()


def test_catalog_entry_points_at_the_component() -> None:
    assert AGENT_PLUGIN_CATALOG["prompt"] == (
        "langharness_core.plugins.prompts.code_agent",
        "code-agent-prompt-plugin-factory",
        "agent.plugin.prompt_section",
    )
    assert agent_plugin_descriptor("prompt").description.strip()


def test_plugin_info_reports_the_component_identity() -> None:
    assert CodeAgentPromptPlugin().get_plugin_info() == {
        "name": "code-agent-prompt",
        "version": "1.0.0",
    }


def test_goal_sections_sit_after_tool_guidance() -> None:
    orders = dict(SECTIONS)
    assert orders["goal_round"] == 9000
    assert orders["goal_wrapup"] == 9001
    assert orders["goal_round"] > 5000


def test_the_goal_round_section_explains_what_a_quiet_turn_means() -> None:
    text = load_section("goal_round").lower()
    assert "continue" in text
    assert "verify" in text
    assert "finish_goal" in text


def test_the_wrapup_section_demands_facts() -> None:
    text = load_section("goal_wrapup").lower()
    assert "evidence" in text or "tool result" in text
    assert "risk" in text
    assert "no more tools" in text or "do not call" in text


def test_the_plugin_emits_the_goal_sections(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    names = [item.name for item in sections]
    assert names[-2:] == ["goal_round", "goal_wrapup"]
