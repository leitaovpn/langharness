"""E2E: the agent directory seeds an agent-scope default LLM from providers.default.

The default instance lives in the ``agent`` scope and every agent loop
inherits it through scope visibility; an agent-specific LLM (stored config or
``ensure_plugin_instance``) shadows it while it exists and the loop falls back
when it is removed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from langharness_config.plugin import config_descriptors
from langharness_core.contracts import SPEC_AGENT_DIRECTORY, SPEC_LLM
from langharness_core.plugin import (
    agent_directory_descriptor,
    agent_loop_template_descriptor,
    agent_plugin_template_descriptor,
    agent_registry_descriptor,
    agent_registry_properties,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.scope_const import SERVER_SCOPE_ID
from langharness_scope import ROOT_SCOPE_ID

DEFAULT_TOML = """
[providers.default]
protocol = "chat"
base_url = "https://default.example/v1"
model = "default-model"
api_key = "default-key"
"""


class StaticModel(BaseChatModel):
    response: str = "shadow"

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


def _write_agent(path: Any) -> None:
    path.write_text(
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


def _manager(tmp_path: Any) -> PluginManager:
    (tmp_path / "langharness.toml").write_text(DEFAULT_TOML, encoding="utf-8")
    _write_agent(tmp_path / "agents.json")
    manager = PluginManager(PluginRegistry())
    manager.start()
    for descriptor in [
        *config_descriptors(),
        agent_registry_descriptor(),
        agent_directory_descriptor(),
        # Templates install after the directory in the real bootstrap, so
        # the default LLM must wait for the llm bundle instead of failing.
        agent_plugin_template_descriptor("llm"),
        agent_plugin_template_descriptor("tools"),
        agent_plugin_template_descriptor("name"),
        agent_loop_template_descriptor(),
    ]:
        manager.install_descriptor(descriptor)
    manager.create_instance(
        "toml-config-plugin-factory",
        "langharness_config.plugins.toml",
        ROOT_SCOPE_ID,
        properties={"plugin.config.path": str(tmp_path / "langharness.toml")},
    )
    manager.create_instance(
        "configs-plugin-factory",
        "langharness_config.plugins.configs",
        ROOT_SCOPE_ID,
    )
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
    return manager


def test_loop_inherits_agent_scope_default_llm_and_shadowing(tmp_path: Any) -> None:
    manager = _manager(tmp_path)
    try:
        directory = manager.get_service(SPEC_AGENT_DIRECTORY)
        assert directory is not None

        loop = directory.get_loop("alpha")
        assert loop is not None
        assert loop._llm_provider is not None
        assert loop._llm_provider._plugin_scope_id == "agent"
        assert loop._llm_provider._model_name == "default-model"
        assert loop._graph is not None

        llm_properties = {
            item.get("plugin.scope_id"): item.get("plugin.model.name")
            for item in manager.service_properties(SPEC_LLM)
        }
        assert llm_properties["agent"] == "default-model"

        # An agent-specific LLM shadows the default while it exists.
        own_model = StaticModel()
        directory.ensure_plugin_instance(
            "alpha", "llm", {"plugin.model.instance": own_model}
        )
        shadowed = directory.get_loop("alpha")
        assert shadowed is not None
        assert shadowed._llm_provider.get_model() is own_model

        # Removing the agent-specific LLM makes the loop fall back to the default.
        directory.reload("alpha")
        restored = directory.get_loop("alpha")
        assert restored is not None
        assert restored._llm_provider._plugin_scope_id == "agent"
        assert restored._llm_provider._model_name == "default-model"
    finally:
        manager.stop()


def test_empty_llm_binding_gets_default_fields_and_builds_without_errors(
    tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    manager = _manager(tmp_path)
    try:
        directory = manager.get_service(SPEC_AGENT_DIRECTORY)
        assert directory is not None
        directory.apply_agent_config(
            "alpha", {"llm": {"enabled": True, "properties": {}}}
        )

        loop = directory.get_loop("alpha")
        assert loop is not None
        assert loop._llm_provider is not None
        assert loop._llm_provider._plugin_scope_id == "agent:alpha"
        assert loop._llm_provider._model_name == "default-model"
        assert loop._graph is not None
        assert "Agent graph rebuild failed" not in caplog.text
        assert "Could not create the default LLM" not in caplog.text
    finally:
        manager.stop()
