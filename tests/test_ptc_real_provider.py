"""Opt-in PTC smoke test against one configured external model provider."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tomllib
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from langharness_core.plugins.checkpointer.sqlite import SQLiteCheckpointerPlugin
from langharness_core.plugins.llm.llm import LLMPlugin
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.presentation.mode import ToolPresentationPlugin
from langharness_core.plugins.presentation.runtime import NodePTCRuntime
from langharness_core.plugins.prompts.ptc import PTCPromptPlugin
from langharness_core.plugins.tools.tools import ToolPlugin


def _write_evidence(
    directory: Path,
    provider_name: str,
    events: list[dict[str, Any]],
    summary: dict[str, Any],
    credential: str,
) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]+", provider_name) is None:
        raise ValueError("provider name must be a safe filename")
    directory.mkdir(parents=True, exist_ok=True)

    def serialize(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, default=str)
        return raw.replace(credential, "[REDACTED]") if credential else raw

    (directory / f"{provider_name}.ndjson").write_text(
        "".join(serialize(event) + "\n" for event in events), encoding="utf-8"
    )
    (directory / f"{provider_name}.json").write_text(
        serialize(summary) + "\n", encoding="utf-8"
    )


def _failure_category(exc: Exception) -> str:
    message = str(exc).lower()
    if "free quota exhausted" in message or "insufficient_quota" in message:
        return "quota_exhausted"
    if "no generations found in stream" in message:
        return "empty_stream"
    return "provider_error"


def test_evidence_writer_redacts_credentials(tmp_path: Path) -> None:
    event = {"type": "assistant", "content": "key=top-secret"}
    _write_evidence(
        tmp_path, "provider-one", [event], {"answer": "top-secret"}, "top-secret"
    )

    ndjson = (tmp_path / "provider-one.ndjson").read_text(encoding="utf-8")
    summary = (tmp_path / "provider-one.json").read_text(encoding="utf-8")
    assert "top-secret" not in ndjson + summary
    assert "[REDACTED]" in ndjson + summary


def test_failure_classifier_keeps_only_safe_category() -> None:
    assert _failure_category(RuntimeError("Free quota exhausted: secret detail")) == "quota_exhausted"
    assert _failure_category(ValueError("No generations found in stream.")) == "empty_stream"


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
    checkpointer = SQLiteCheckpointerPlugin()
    checkpointer._path = str(tmp_path / "ptc-checkpoint.sqlite3")
    loop._checkpointer_provider = checkpointer
    thread_id = f"ptc-real-{uuid.uuid4().hex}"
    evidence_dir = Path(
        os.environ.get("LANG_HARNESS_REAL_LLM_EVIDENCE_DIR", str(tmp_path))
    )

    async def collect() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        events = [
            event
            async for event in loop.astream(
                "Call run_code with JavaScript code exactly: "
                "return await tools.add({a: 2, b: 3}); "
                "Then answer with the returned number.",
                thread_id=thread_id,
            )
        ]
        checkpoint = await checkpointer.get_checkpointer().aget_tuple(
            {"configurable": {"thread_id": thread_id}}
        )
        assert checkpoint is not None
        messages = checkpoint.checkpoint["channel_values"]["messages"]
        return events, {
            "message_count": len(messages),
            "message_types": [type(message).__name__ for message in messages],
            "tool_call_names": [
                call["name"]
                for message in messages
                for call in getattr(message, "tool_calls", [])
            ],
        }

    events: list[dict[str, Any]] = []
    checkpoint_summary: dict[str, Any] = {}
    failure: str | None = None
    try:
        events, checkpoint_summary = asyncio.run(
            asyncio.wait_for(collect(), timeout=90)
        )
    except Exception as exc:
        _write_evidence(
            evidence_dir,
            provider_name,
            [],
            {
                "protocol": config["protocol"],
                "error_type": type(exc).__name__,
                "error_category": _failure_category(exc),
                "status": getattr(exc, "status_code", None),
            },
            config["api_key"],
        )
        failure = (
            f"real provider {config['protocol']} failed: {type(exc).__name__}; "
            f"status={getattr(exc, 'status_code', None)}"
        )
    finally:
        checkpointer._invalidate(cast(Any, None))
    if failure is not None:
        pytest.fail(failure, pytrace=False)
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
        "tool_outputs": [
            event.get("output") for event in events if event["type"] == "tool_output"
        ],
        "checkpoint": checkpoint_summary,
    }
    _write_evidence(
        evidence_dir, provider_name, events, summary, config["api_key"]
    )
    assert summary["tool_calls"] == ["run_code"]
    assert summary["audit_results"] == [5]
    assert not summary["errors"]
    assert "5" in summary["answer"]
