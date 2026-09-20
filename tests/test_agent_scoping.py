"""Spike: iPOPO ``requires.filters`` scopes plugin services per agent instance.

Phase 2 builds per-agent plugin sets on this mechanism, so these tests pin the
semantics it relies on: instantiate-time properties are published as service
properties, a component instance can scope a requirement to a subset of that
specification, and malformed filters are silently ignored (the implementation
must validate filters before instantiating).

Per-agent components are instantiated from an already installed bundle
(``PluginManager.instantiate_instance``) rather than by installing the same
module twice, because Pelix re-executes a bundle module when it is installed
again after an uninstall.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from langharness_core.contracts import (
    SPEC_AGENT_DIRECTORY,
    SPEC_AGENT_LOOP,
    SPEC_LLM,
)
from langharness_core.plugin import (
    agent_directory_descriptor,
    agent_loop_template_descriptor,
    agent_plugin_descriptor,
    agent_plugin_template_descriptor,
    agent_registry_descriptor,
    agent_registry_properties,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, SERVER_SCOPE_ID
from langharness_scope import ScopeId


class StaticModel(BaseChatModel):
    response: str = "ok"

    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.response))]
        )

    @property
    def _llm_type(self) -> str:
        return "static-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> StaticModel:
        return self


def bound_model_ids(manager: PluginManager) -> set[int]:
    loops = manager.get_services(SPEC_AGENT_LOOP)
    return {id(loop._llm_provider.get_model()) for loop in loops}


def install_llm_bundle(manager: PluginManager, agent_id: str, model: StaticModel) -> None:
    """Install the llm bundle once and instantiate one agent-scoped llm."""
    manager.install_descriptor(agent_plugin_descriptor("llm"))
    add_scoped_llm(manager, agent_id, model)


def add_scoped_llm(
    manager: PluginManager,
    agent_id: str,
    model: StaticModel,
    *,
    scope_id: ScopeId = AGENT_SCOPE_ID,
) -> None:
    manager.create_instance(
        "llm-plugin-factory",
        "langharness_core.plugins.llm.llm",
        scope_id,
        properties={"plugin.model.instance": model, "plugin.agent_id": agent_id},
    )


def add_scoped_loop(
    manager: PluginManager,
    agent_id: str,
    llm_filter: str | None,
    *,
    scope_id: ScopeId = AGENT_SCOPE_ID,
) -> None:
    properties: dict[str, Any] = {"plugin.agent_id": agent_id}
    if llm_filter is not None:
        properties["requires.filters"] = {"_llm_provider": llm_filter}
    manager.create_instance(
        "agent-loop-factory",
        "langharness_core.plugins.loop.agent_loop",
        scope_id,
        properties=properties,
    )


def install_loop_bundle(manager: PluginManager, agent_id: str, llm_filter: str | None) -> None:
    manager.install_descriptor(agent_loop_template_descriptor())
    add_scoped_loop(manager, agent_id, llm_filter)


def agent_scope(manager: PluginManager, agent_id: str) -> ScopeId:
    scope_id = ScopeId(f"agent:{agent_id}")
    manager.add_scope(scope_id, name=agent_id, parent_id=AGENT_SCOPE_ID)
    return scope_id


def test_instantiate_properties_are_published_on_the_service() -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_llm_bundle(manager, "ag1", StaticModel())
        properties = manager.service_properties(SPEC_LLM)
        assert [item.get("plugin.agent_id") for item in properties] == ["ag1"]
    finally:
        manager.stop()


def test_requires_filters_scope_each_loop_to_its_own_llm() -> None:
    model_a = StaticModel()
    model_b = StaticModel()
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        manager.install_descriptor(agent_plugin_descriptor("llm"))
        manager.install_descriptor(agent_loop_template_descriptor())
        # Each agent owns a scope, so the loop's scope chain sees only its
        # own LLM instance.
        add_scoped_llm(manager, "ag1", model_a, scope_id=agent_scope(manager, "ag1"))
        add_scoped_llm(manager, "ag2", model_b, scope_id=agent_scope(manager, "ag2"))
        add_scoped_loop(manager, "ag1", "(plugin.agent_id=ag1)", scope_id=agent_scope(manager, "ag1"))
        add_scoped_loop(manager, "ag2", "(plugin.agent_id=ag2)", scope_id=agent_scope(manager, "ag2"))

        assert len(manager.get_services(SPEC_AGENT_LOOP)) == 2
        assert bound_model_ids(manager) == {id(model_a), id(model_b)}
    finally:
        manager.stop()


def test_unfiltered_loops_share_one_best_llm() -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_llm_bundle(manager, "ag1", StaticModel())
        add_scoped_llm(manager, "ag2", StaticModel())
        install_loop_bundle(manager, "ag1", None)
        add_scoped_loop(manager, "ag2", None)

        assert len(bound_model_ids(manager)) == 1
    finally:
        manager.stop()


def test_malformed_filter_rejects_instance_creation() -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_llm_bundle(manager, "ag1", StaticModel())
        add_scoped_llm(manager, "ag2", StaticModel())
        manager.install_descriptor(agent_loop_template_descriptor())

        with pytest.raises(ValueError, match="filter"):
            add_scoped_loop(manager, "ag1", "not a filter")

        assert manager.get_services(SPEC_AGENT_LOOP) == []
    finally:
        manager.stop()


def test_directory_materializes_isolated_plugin_sets(tmp_path: Any) -> None:
    registry_path = tmp_path / "agents.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "agents": [
                    {
                        "id": "alpha",
                        "name": "Alpha",
                        "description": "first agent",
                        "enabled": True,
                        "created_at": "2026-09-15T00:00:00+00:00",
                        "updated_at": "2026-09-15T00:00:00+00:00",
                    },
                    {
                        "id": "beta",
                        "name": "Beta",
                        "description": "second agent",
                        "enabled": True,
                        "created_at": "2026-09-15T00:00:00+00:00",
                        "updated_at": "2026-09-15T00:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    model_a = StaticModel()
    model_b = StaticModel()
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for descriptor in [
            agent_plugin_template_descriptor("llm"),
            agent_plugin_template_descriptor("tools"),
            agent_plugin_template_descriptor("name"),
            agent_loop_template_descriptor(),
            agent_registry_descriptor(),
            agent_directory_descriptor(),
        ]:
            manager.install_descriptor(descriptor)
        manager.create_instance(
            "agent-registry-plugin-factory",
            "langharness_core.plugins.agents.registry",
            SERVER_SCOPE_ID,
            properties=agent_registry_properties(str(tmp_path)),
        )
        manager.create_instance(
            "agent-directory-plugin-factory",
            "langharness_core.plugins.agents.directory",
            SERVER_SCOPE_ID,
        )
        directory = manager.get_service(SPEC_AGENT_DIRECTORY)
        assert directory is not None

        async def scenario() -> tuple[Any, Any]:
            directory.ensure_plugin_instance(
                "alpha", "llm", {"plugin.model.instance": model_a}
            )
            directory.ensure_plugin_instance(
                "beta", "llm", {"plugin.model.instance": model_b}
            )
            return directory.get_loop("alpha"), directory.get_loop("beta")

        loop_a, loop_b = asyncio.run(scenario())

        assert loop_a is not None
        assert loop_b is not None
        assert loop_a is not loop_b
        assert loop_a._llm_provider.get_model() is model_a
        assert loop_b._llm_provider.get_model() is model_b
        assert loop_a._name_provider.get_name() == "Alpha"
        assert loop_b._name_provider.get_name() == "Beta"
        assert "bash" in [tool.name for tool in loop_a._collect_tools()]
        assert directory.list_agents()[0]["materialized"] is True
    finally:
        manager.stop()
