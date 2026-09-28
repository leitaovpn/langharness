"""Tests for the system-prompt plugin vertical slice."""
# mypy: ignore-errors

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import langharness_core.plugins.loop.agent_loop as agent_loop_module
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.prompts.middleware import PromptMiddleware
from langharness_core.plugins.system_prompt.template_system_prompt import (
    TemplateSystemPromptPlugin,
)


def make_provider(text: str, name: str = "system-prompt-plugin"):
    return SimpleNamespace(
        get_system_prompt=lambda: text,
        get_plugin_info=lambda: {"name": name, "version": "1.0.0"},
    )


def test_system_prompt_plugin_exposes_configured_prompt() -> None:
    plugin = TemplateSystemPromptPlugin()
    plugin._system_prompt = "You are a calculator."
    assert plugin.get_system_prompt() == "You are a calculator."
    assert plugin.get_plugin_info() == {
        "name": "system-prompt-plugin",
        "version": "1.0.0",
    }


def test_legacy_providers_are_joined_in_effective_order() -> None:
    loop = PluginAgentLoop()
    loop._system_prompt_providers = [
        make_provider("You are A.", "a"),
        make_provider("You are B.", "b"),
    ]
    assert loop._prompt_for_request({"messages": []}).text == "You are A.\nYou are B."


def test_no_providers_produce_an_empty_prompt() -> None:
    loop = PluginAgentLoop()
    loop._system_prompt_providers = []
    assert loop._prompt_for_request({"messages": []}).empty is True


def test_nearest_scope_provider_shadows_the_same_plugin_further_out() -> None:
    loop = PluginAgentLoop()
    loop._scope_chain = ["agent:a", "agent", "root"]
    near = make_provider("near", "persona")
    far = make_provider("far", "persona")
    loop._system_prompt_providers = [far, near]
    loop._service_metadata[id(far)] = ("root", "persona", 0)
    loop._service_metadata[id(near)] = ("agent:a", "persona", 0)
    assert loop._prompt_for_request({"messages": []}).text == "near"


def test_a_failing_section_provider_does_not_break_assembly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Boom:
        def get_prompt_sections(self, context):
            raise RuntimeError("provider exploded")

    loop = PluginAgentLoop()
    loop._system_prompt_providers = [make_provider("legacy survives")]
    loop._prompt_section_providers = [Boom()]
    assembled = loop._prompt_for_request({"messages": []})
    assert assembled.text == "legacy survives"
    assert "provider exploded" in caplog.text


def test_rebuild_installs_the_prompt_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_agent(
        model, tools=None, *, system_prompt=None, middleware=None, **kwargs
    ):
        captured["system_prompt"] = system_prompt
        captured["middleware"] = middleware
        return object()

    monkeypatch.setattr(agent_loop_module, "create_agent", fake_create_agent)

    loop = PluginAgentLoop()
    loop._llm_provider = SimpleNamespace(get_model=lambda: object())
    loop._tool_providers = []
    loop._middleware_providers = []
    loop._system_prompt_providers = [make_provider("You are A.")]

    loop._rebuild()

    assert captured["system_prompt"] is None
    middlewares = captured["middleware"]
    assert isinstance(middlewares[-1], PromptMiddleware)
