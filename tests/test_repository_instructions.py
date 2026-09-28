"""Tests for the repository-instructions prompt-section plugin."""
# mypy: ignore-errors

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import PromptContext, PromptSectionProvider
from langharness_core.plugin import AGENT_PLUGIN_CATALOG, agent_plugin_descriptor
from langharness_core.plugins.instructions.repository import (
    INSTRUCTION_ORDER,
    OMISSION_ORDER,
    RepositoryInstructionsPlugin,
)
from langharness_plugin.validation import validate


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def plugin(**properties) -> RepositoryInstructionsPlugin:
    instance = RepositoryInstructionsPlugin()
    for key, value in properties.items():
        setattr(instance, key, value)
    return instance


def test_root_only_before_any_target_is_known(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root rules")
    write(tmp_path / "src" / "AGENTS.md", "src rules")
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path)))
    assert [item.name for item in sections] == ["repository-instructions:AGENTS.md"]
    assert sections[0].order == INSTRUCTION_ORDER


def test_deeper_files_are_ordered_by_depth(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "src" / "AGENTS.md", "src")
    sections = plugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path), touched_paths=("src/a.py",))
    )
    assert [item.name for item in sections] == [
        "repository-instructions:AGENTS.md",
        "repository-instructions:src/AGENTS.md",
    ]
    assert sections[1].order == INSTRUCTION_ORDER + 1


def test_source_records_the_real_path_and_text_is_verbatim(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root\nrules\n")
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path)))
    assert sections[0].text == "root\nrules\n"
    assert sections[0].source == str((tmp_path / "AGENTS.md").resolve())


def test_omitted_files_are_reported_to_the_model(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 100)
    write(tmp_path / "src" / "AGENTS.md", "m" * 100)
    write(tmp_path / "src" / "deep" / "AGENTS.md", "n" * 100)
    sections = plugin(_budget=250).get_prompt_sections(
        PromptContext(workspace=str(tmp_path), touched_paths=("src/deep/a.py",))
    )
    notice = sections[-1]
    assert notice.name == "repository-instructions-omitted"
    assert notice.order == OMISSION_ORDER
    assert "src/AGENTS.md" in notice.text
    assert "250" in notice.text


def test_no_notice_when_everything_fits(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path)))
    assert [item.name for item in sections] == ["repository-instructions:AGENTS.md"]


def test_truncated_root_is_reported_in_the_notice(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 1000)
    sections = plugin(_budget=200).get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert "AGENTS.md" in sections[-1].text
    assert "truncated" in sections[-1].text.lower()


def test_missing_workspace_yields_no_sections(tmp_path: Path) -> None:
    sections = plugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path / "nope"))
    )
    assert sections == []


def test_include_property_accepts_a_comma_separated_string(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "CLAUDE.md", "compat")
    sections = plugin(_include="CLAUDE.md").get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert [item.name for item in sections] == [
        "repository-instructions:AGENTS.md",
        "repository-instructions:CLAUDE.md",
    ]


def test_include_property_accepts_a_list(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "compat")
    sections = plugin(_include=["CLAUDE.md"]).get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert [item.name for item in sections] == ["repository-instructions:CLAUDE.md"]


def test_resolvers_are_cached_per_workspace(tmp_path: Path) -> None:
    instance = plugin()
    first = instance._resolver(tmp_path)
    assert instance._resolver(tmp_path) is first


def test_plugin_conforms_to_its_contract() -> None:
    assert validate(plugin(), PromptSectionProvider) == ()


def test_catalog_entry_points_at_the_component() -> None:
    assert AGENT_PLUGIN_CATALOG["repository-instructions"] == (
        "langharness_core.plugins.instructions.repository",
        "repository-instructions-plugin-factory",
        "agent.plugin.prompt_section",
    )
    assert agent_plugin_descriptor("repository-instructions").description.strip()


def test_plugin_info_reports_the_component_identity() -> None:
    assert plugin().get_plugin_info() == {
        "name": "repository-instructions",
        "version": "1.0.0",
    }
