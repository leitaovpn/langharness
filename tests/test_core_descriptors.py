"""Tests for seven-field builtin descriptors and assembly requests."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from langharness_core.contracts import SPEC_CHECKPOINTER, SPEC_LLM, SPEC_TOOL
from langharness_core.plugin import (
    agent_binding_properties,
    agent_directory_descriptor,
    agent_loop_descriptor,
    agent_loop_properties,
    agent_plugin_descriptor,
    agent_plugin_template_descriptor,
    agent_registry_descriptor,
    agent_registry_properties,
    agent_required_modules,
    agent_scoped_specifications,
    builtin_package,
    dynamic_package,
    dynamic_template_descriptor,
    session_index_descriptor,
    session_index_properties,
    sqlite_checkpointer_descriptor,
    sqlite_checkpointer_properties,
)
from langharness_plugin.registry import validate_descriptor


def test_every_builtin_descriptor_passes_static_validation() -> None:
    for package in (builtin_package(), dynamic_package()):
        for contribution in package.contributions:
            validate_descriptor(contribution.descriptor)
            assert contribution.descriptor.description.strip()


def test_agent_plugin_descriptor_is_static_only() -> None:
    descriptor = agent_plugin_descriptor("llm")
    assert descriptor.name == "llm"
    assert descriptor.module == "langharness_core.plugins.llm.llm"
    assert descriptor.factory == "llm-plugin-factory"
    assert descriptor.specification == SPEC_LLM
    validate_descriptor(descriptor)
    assert not hasattr(descriptor, "scope")
    assert not hasattr(descriptor, "instance")
    assert not hasattr(descriptor, "properties")


def test_agent_binding_properties_carry_agent_id() -> None:
    properties = agent_binding_properties("a1", "tools", {"plugin.tools.root_dir": "/x"})
    assert properties["plugin.agent_id"] == "a1"
    assert properties["plugin.tools.root_dir"] == "/x"


def test_state_properties_store_files_under_directory(tmp_path) -> None:
    directory = str(tmp_path)
    checkpointer = sqlite_checkpointer_properties(directory)
    sessions = session_index_properties(directory)
    registry = agent_registry_properties(directory)

    assert checkpointer["plugin.checkpoint.path"] == str(
        tmp_path / "langharness_checkpoints.sqlite3"
    )
    assert sessions["plugin.sessions.path"] == str(tmp_path / "sessions.sqlite3")
    assert registry["plugin.agents.path"] == str(tmp_path / "agents.json")


def test_state_descriptors_declare_their_specifications() -> None:
    assert sqlite_checkpointer_descriptor().specification == SPEC_CHECKPOINTER
    assert session_index_descriptor().specification == "session.index"
    assert agent_registry_descriptor().specification == "agent.registry"


def test_agent_plugin_template_shares_factory_with_instance_descriptor() -> None:
    template = agent_plugin_template_descriptor("tools")
    instance = agent_plugin_descriptor("tools")
    assert template.factory == instance.factory
    assert template.module == instance.module
    assert template.specification == SPEC_TOOL


def test_agent_loop_properties_keep_user_filters() -> None:
    descriptor = agent_loop_descriptor()
    validate_descriptor(descriptor)
    properties = agent_loop_properties(
        "a", ["agent.plugin.llm"], visibility_filter="(plugin.agent_id=a)"
    )
    assert properties["plugin.agent_id"] == "a"
    assert properties["requires.filters"]["_llm_provider"] == "(plugin.agent_id=a)"


def test_agent_directory_descriptor_is_static() -> None:
    validate_descriptor(agent_directory_descriptor())
    assert not hasattr(agent_directory_descriptor(), "scope")


def test_dynamic_template_descriptor_is_static() -> None:
    validate_descriptor(dynamic_template_descriptor("middleware-plugin"))


def test_helper_catalogs_unchanged() -> None:
    assert agent_required_modules() == [
        "langharness_core.plugins.tools.workspace",
        "langharness_core.plugins.name.template_name",
        "langharness_core.plugins.loop.agent_loop",
    ]
    assert "agent.plugin.llm" in agent_scoped_specifications()
