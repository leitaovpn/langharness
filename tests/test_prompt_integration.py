"""End-to-end prompt assembly through the real Pelix/iPOPO framework."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from langharness_core.contracts import (
    SPEC_AGENT_LOOP,
    SPEC_LLM,
    SPEC_PROMPT_SECTION,
    SPEC_SYSTEM_PROMPT,
    SPEC_TOOL,
    SPEC_WORKSPACE,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_scope import ROOT_SCOPE_ID

DESCRIPTION = (
    "Prompt integration test plugin. Implements a test specification. "
    "Properties vary per instance. Requires a restart for property changes. "
    "Uninstall when tests finish."
)

SYSTEM_PROMPTS: list[str] = []


@tool
def read_file(path: str) -> str:
    """Read a file from the workspace."""
    return f"contents of {path}"


class CapturingModel(BaseChatModel):
    """Records every system prompt it is asked to answer under."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for message in messages:
            if message.type == "system":
                SYSTEM_PROMPTS.append(str(message.content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    @property
    def _llm_type(self) -> str:
        return "capturing"

    def bind_tools(self, tools, **kwargs):
        return self


class ToolCallingModel(CapturingModel):
    """Calls ``read_file`` once, then stops."""

    called: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for message in messages:
            if message.type == "system":
                SYSTEM_PROMPTS.append(str(message.content))
        if not self.called:
            self.called = True
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "read_file",
                                    "args": {"path": "deep/a.py"},
                                    "id": "c1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, tools, **kwargs):
        return self


def descriptor(
    name: str, module: str, factory: str, specification: str
) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=DESCRIPTION,
    )


def start(
    installs: list[tuple[PluginDescriptor, dict[str, Any]]],
) -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager.start()
    for item, _ in installs:
        manager.install_descriptor(item)
    for item, properties in installs:
        manager.create_instance(
            item.factory, item.module, ROOT_SCOPE_ID, properties=properties
        )
    return manager


def installs(
    workspace: Path, model: BaseChatModel
) -> list[tuple[PluginDescriptor, dict[str, Any]]]:
    return [
        (
            descriptor(
                "llm",
                "langharness_core.plugins.llm.llm",
                "llm-plugin-factory",
                SPEC_LLM,
            ),
            {"plugin.model.instance": model},
        ),
        (
            descriptor(
                "tools",
                "langharness_core.plugins.tools.tools",
                "tools-plugin-factory",
                SPEC_TOOL,
            ),
            {"plugin.tool_functions": [read_file]},
        ),
        (
            descriptor(
                "workspace",
                "langharness_core.plugins.workspace.root",
                "workspace-root-plugin-factory",
                SPEC_WORKSPACE,
            ),
            {"plugin.workspace.root_dir": str(workspace)},
        ),
        (
            descriptor(
                "system-prompt",
                "langharness_core.plugins.system_prompt.template_system_prompt",
                "system-prompt-plugin-factory",
                SPEC_SYSTEM_PROMPT,
            ),
            {"plugin.system_prompt": "legacy persona"},
        ),
        (
            descriptor(
                "repository-instructions",
                "langharness_core.plugins.instructions.repository",
                "repository-instructions-plugin-factory",
                SPEC_PROMPT_SECTION,
            ),
            {},
        ),
        (
            descriptor(
                "agent-loop",
                "langharness_core.plugins.loop.agent_loop",
                "agent-loop-factory",
                SPEC_AGENT_LOOP,
            ),
            {},
        ),
    ]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_legacy_and_repository_sections_reach_the_model(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root instruction")
    SYSTEM_PROMPTS.clear()
    manager = start(installs(tmp_path, CapturingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("hello")
    finally:
        manager.stop()
    assert SYSTEM_PROMPTS, "the model never saw a system prompt"
    prompt = SYSTEM_PROMPTS[-1]
    # order 500 (instructions) sorts before order 1000 (legacy persona)
    assert prompt.index("root instruction") < prompt.index("legacy persona")


def test_deeper_instructions_arrive_on_the_next_model_step(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root instruction")
    write(tmp_path / "deep" / "AGENTS.md", "deep instruction")
    SYSTEM_PROMPTS.clear()
    manager = start(installs(tmp_path, ToolCallingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("touch deep/a.py")
    finally:
        manager.stop()
    assert len(SYSTEM_PROMPTS) >= 2, SYSTEM_PROMPTS
    assert "deep instruction" not in SYSTEM_PROMPTS[0]
    assert "deep instruction" in SYSTEM_PROMPTS[1]


def test_instruction_changes_take_effect_without_restarting_the_agent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "AGENTS.md"
    write(target, "first revision")
    SYSTEM_PROMPTS.clear()
    manager = start(installs(tmp_path, CapturingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("hello")
        assert "first revision" in SYSTEM_PROMPTS[-1]

        write(target, "second revision")
        # Guarantee a different mtime even on a coarse-grained filesystem.
        os.utime(target, (1, 1))

        # Same loop instance, no reinstall: the prompt must pick up the edit.
        loop.invoke("again")
        assert "second revision" in SYSTEM_PROMPTS[-1]
        assert "first revision" not in SYSTEM_PROMPTS[-1]
    finally:
        manager.stop()


def test_repository_instructions_drop_out_when_the_workspace_is_gone() -> None:
    SYSTEM_PROMPTS.clear()
    manager = start(installs(Path("/nonexistent-workspace-root"), CapturingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("hello")
    finally:
        manager.stop()
    assert SYSTEM_PROMPTS[-1] == "legacy persona"
