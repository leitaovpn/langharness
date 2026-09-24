"""Opt-in PTC smoke test against one configured external model provider."""

from __future__ import annotations

import asyncio
import json
import os
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

from langharness_core.plugins.llm.llm import LLMPlugin
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.presentation.mode import ToolPresentationPlugin
from langharness_core.plugins.presentation.runtime import NodePTCRuntime
from langharness_core.plugins.prompts.ptc import PTCPromptPlugin
from langharness_core.plugins.tools.tools import ToolPlugin


@pytest.mark.real_llm
def test_real_provider_ptc_add(tmp_path: Path) -> None:
    provider_name = os.environ.get("LANG_HARNESS_REAL_LLM_PROVIDER")
    if not provider_name:
        pytest.skip("set LANG_HARNESS_REAL_LLM_PROVIDER to run real PTC smoke")
    config_path = Path.home() / ".langharness" / "langharness.toml"
    providers = tomllib.loads(config_path.read_text(encoding="utf-8"))["providers"]
    config = providers[provider_name]
    llm = LLMPlugin()
    llm._model_name = config["model"]
    llm._api_key = config["api_key"]
    llm._base_url = config.get("base_url", "")
    llm._protocol = config["protocol"]
    presentation = ToolPresentationPlugin()
    presentation._mode = "ptc"
    prompt = PTCPromptPlugin()
    prompt._presentation = cast(Any, presentation)
    loop = PluginAgentLoop()
    loop._llm_provider = llm
    loop._tool_providers = [ToolPlugin()]
    loop._presentation_provider = presentation
    loop._ptc_runtime = NodePTCRuntime()
    loop._prompt_section_providers = [prompt]

    async def collect() -> list[dict[str, Any]]:
        return [
            event
            async for event in loop.astream(
                "Call run_code with JavaScript code exactly: "
                "return await tools.add({a: 2, b: 3}); "
                "Then answer with the returned number."
            )
        ]

    try:
        events = asyncio.run(asyncio.wait_for(collect(), timeout=90))
    except Exception as exc:
        pytest.fail(
            f"real provider {config['protocol']} failed: {type(exc).__name__}; "
            f"status={getattr(exc, 'status_code', None)}"
        )
    summary = {
        "protocol": config["protocol"],
        "tool_calls": [event["name"] for event in events if event["type"] == "tool_call"],
        "audit_results": [
            event.get("result")
            for event in events
            if event["type"] == "ptc_audit" and event.get("status") == "completed"
        ],
        "answer": " ".join(
            event.get("content", "") for event in events if event["type"] == "assistant"
        ),
        "errors": [event.get("error") for event in events if event["type"] == "error"],
    }
    (tmp_path / "ptc-real-smoke.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert summary["tool_calls"] == ["run_code"]
    assert summary["audit_results"] == [5]
    assert not summary["errors"]
    assert "5" in summary["answer"]
