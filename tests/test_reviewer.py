"""Tests for the optional reviewer capability."""

import json
import os
import re
import sqlite3
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from langharness_core.contracts import ReviewerProvider
from langharness_core.plugin import agent_plugin_template_descriptor
from langharness_core.plugins.llm.llm import LLMPlugin
from langharness_core.plugins.reviewer.provider import OptionalReviewer
from langharness_plugin.validation import validate


def test_reviewer_is_disabled_and_never_claims_completion() -> None:
    reviewer = OptionalReviewer()

    assert validate(reviewer, ReviewerProvider) == ()
    assert reviewer.is_enabled() is False
    assert reviewer.review({"goal": "x"}) == {
        "status": "disabled",
        "independent": True,
        "completion_authority": "verifier",
    }


def test_enabled_reviewer_without_model_reports_unavailable() -> None:
    reviewer = OptionalReviewer()
    reviewer._enabled = True

    result = reviewer.review({"goal": "x", "artifact": "a"})

    assert result["status"] == "unavailable"
    assert result["completion_authority"] == "verifier"


def test_model_review_persists_artifact_without_subject_body(tmp_path: Path) -> None:
    reviewer = OptionalReviewer()
    reviewer._enabled = True
    reviewer._path = str(tmp_path / "reviews.sqlite3")
    reviewer._agent_id = "alpha"
    calls: list[Any] = []

    class FakeModel:
        def invoke(self, messages: Any) -> Any:
            calls.append(messages)
            return SimpleNamespace(content="Potential issue: missing test")

    reviewer._llm = SimpleNamespace(get_model=lambda: FakeModel())
    result = reviewer.review({"goal": "secret subject", "artifact": "a.py"})

    assert result["status"] == "completed"
    assert result["review_text"] == "Potential issue: missing test"
    assert result["independent"] is True
    assert result["completion_authority"] == "verifier"
    assert len(calls) == 1
    assert "secret subject" in str(calls[0])
    with sqlite3.connect(reviewer._path) as connection:
        row = connection.execute(
            "SELECT agent_id, subject_sha256, review_text FROM review_artifacts "
            "WHERE id = ?", (result["artifact_id"],)
        ).fetchone()
    assert row is not None
    assert row[0] == "alpha"
    assert len(row[1]) == 64
    assert row[2] == result["review_text"]
    assert "secret subject" not in str(row)


def test_reviewer_is_catalogued_as_an_optional_agent_plugin() -> None:
    descriptor = agent_plugin_template_descriptor("reviewer")
    assert descriptor.factory == "reviewer-plugin-factory"
    assert descriptor.specification == "agent.plugin.reviewer"


@pytest.mark.real_llm
def test_real_reviewer_smoke(tmp_path: Path) -> None:
    provider_name = os.environ.get("LANG_HARNESS_REAL_LLM_PROVIDER")
    if not provider_name:
        pytest.skip("set LANG_HARNESS_REAL_LLM_PROVIDER to run reviewer smoke")
    config_path = Path.home() / ".langharness" / "langharness.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))["providers"][provider_name]
    llm = LLMPlugin()
    llm._model_name = config["model"]
    llm._api_key = config["api_key"]
    llm._base_url = config.get("base_url", "")
    llm._protocol = config["protocol"]
    reviewer = OptionalReviewer()
    reviewer._enabled = True
    reviewer._llm = llm
    reviewer._agent_id = "real-reviewer-smoke"
    reviewer._path = str(tmp_path / "review.sqlite3")
    result = reviewer.review({"goal": "Check that 2 + 3 equals 5", "artifact": "answer.txt"})
    safe = {"status": result.get("status"), "error_type": result.get("error_type")}
    evidence_dir = Path(os.environ.get("LANG_HARNESS_REAL_LLM_EVIDENCE_DIR", str(tmp_path)))
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / f"reviewer-{re.sub(r'[^A-Za-z0-9_-]', '_', provider_name)}.json").write_text(
        json.dumps(safe) + "\n", encoding="utf-8"
    )
    assert result["status"] == "completed"
    assert result["completion_authority"] == "verifier"
