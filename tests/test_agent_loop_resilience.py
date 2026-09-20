"""Regression tests: provider failures must not kill the agent loop component.

A rebind to a broken LLM configuration used to let ``LLMPlugin.get_model()``
raise inside iPOPO's validation callback, which put the loop instance into the
permanent ERRONEOUS state: every later ``/stream`` returned 503
"Agent loop unavailable" until restart.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import langharness_core.plugins.loop.agent_loop as agent_loop_module
from langharness_core.contracts import SPEC_AGENT_DIRECTORY
from langharness_core.plugin import (
    agent_directory_descriptor,
    agent_loop_template_descriptor,
    agent_plugin_template_descriptor,
    agent_registry_descriptor,
    agent_registry_properties,
)
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.scope_const import SERVER_SCOPE_ID


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


class BrokenLLM:
    def get_model(self) -> Any:
        raise ValueError("Unable to infer model provider for model='broken'")

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": "broken-llm", "version": "1.0.0"}


class LoopBoundCheckpointer:
    """A provider that can only build its saver on a running event loop."""

    def __init__(self) -> None:
        self.calls = 0

    def get_checkpointer(self) -> Any:
        self.calls += 1
        asyncio.get_running_loop()
        return object()


def test_rebuild_defers_when_checkpointer_needs_an_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = PluginAgentLoop()
    loop._llm_provider = SimpleNamespace(
        get_model=lambda: object(),
        get_plugin_info=lambda: {"name": "fake-llm", "version": "1.0.0"},
    )
    checkpointer = LoopBoundCheckpointer()
    loop._checkpointer_provider = checkpointer
    built: list[dict[str, Any]] = []

    def fake_create_agent(model: Any, **kwargs: Any) -> Any:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(agent_loop_module, "create_agent", fake_create_agent)

    loop._rebuild()

    # Without a running loop the graph build is deferred, not failed.
    assert loop._graph is None
    assert checkpointer.calls == 0
    assert built == []


def test_rebuild_on_running_loop_builds_with_the_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = PluginAgentLoop()
    loop._llm_provider = SimpleNamespace(
        get_model=lambda: object(),
        get_plugin_info=lambda: {"name": "fake-llm", "version": "1.0.0"},
    )
    checkpointer = LoopBoundCheckpointer()
    loop._checkpointer_provider = checkpointer
    built: list[dict[str, Any]] = []

    def fake_create_agent(model: Any, **kwargs: Any) -> Any:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(agent_loop_module, "create_agent", fake_create_agent)

    async def scenario() -> None:
        loop._rebuild()

    asyncio.run(scenario())

    assert loop._graph is not None
    assert checkpointer.calls == 1
    assert "checkpointer" in built[0]


def test_rebuild_survives_llm_get_model_failure() -> None:
    """A provider exception during graph rebuild clears the graph instead of
    escaping into iPOPO's validation callback."""
    loop = PluginAgentLoop()
    loop._llm_provider = BrokenLLM()

    loop._rebuild()

    assert loop._graph is None


def test_loop_survives_rebind_to_broken_llm_configuration(tmp_path: Any) -> None:
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
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
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

        async def scenario() -> tuple[Any, Any, Any]:
            working_model = StaticModel()
            directory.ensure_plugin_instance(
                "alpha", "llm", {"plugin.model.instance": working_model}
            )
            loop_before = directory.get_loop("alpha")
            assert loop_before is not None
            assert loop_before._graph is not None

            # Rebind to a name-only configuration without credentials:
            # get_model() falls back to init_chat_model() and raises.
            directory.ensure_plugin_instance(
                "alpha",
                "llm",
                {"plugin.model.name": "definitely-not-a-provider-xyz"},
            )
            loop_broken = directory.get_loop("alpha")
            return loop_before, loop_broken, working_model

        loop_before, loop_broken, working_model = asyncio.run(scenario())

        # The loop service must survive the broken rebind...
        assert loop_broken is not None
        assert loop_broken._graph is None
        # ...and a later good rebind must restore the graph.
        directory.ensure_plugin_instance(
            "alpha", "llm", {"plugin.model.instance": working_model}
        )
        loop_restored = directory.get_loop("alpha")
        assert loop_restored is not None
        assert loop_restored._graph is not None
    finally:
        manager.stop()
