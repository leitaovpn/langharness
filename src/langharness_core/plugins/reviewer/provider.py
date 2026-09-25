"""Optional model-backed reviewer whose evidence cannot complete a goal."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)

from langharness_core.contracts import LLMProvider, ReviewerProvider
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_artifacts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    subject_sha256 TEXT NOT NULL,
    review_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@ComponentFactory("reviewer-plugin-factory")
@Provides(ReviewerProvider)
@RequiresBest("_llm", LLMProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_llm")
@Property("_plugin_name", "plugin.name", "reviewer")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_enabled", "plugin.reviewer.enabled", False)
@Property("_path", "plugin.reviewer.path", str(Path.home() / ".langharness" / "reviews.sqlite3"))
@Property("_agent_id", "plugin.agent_id", "")
class OptionalReviewer:
    """Run a separate review call and persist its evidence, never a gate result."""

    def __init__(self) -> None:
        self._plugin_name = "reviewer"
        self._plugin_version = "1.0.0"
        self._enabled = False
        self._path = str(Path.home() / ".langharness" / "reviews.sqlite3")
        self._agent_id = ""
        self._llm: Any = None
        self._guard = ContractGuard(self, "_llm", LLMProvider)

    @BindField("_llm", if_valid=True)
    def _on_llm_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guard.admit(service)

    @UnbindField("_llm")
    def _on_llm_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guard.release(service)

    def is_enabled(self) -> bool:
        return bool(self._enabled)

    def review(self, subject: dict[str, Any]) -> dict[str, Any]:
        base = {"independent": True, "completion_authority": "verifier"}
        if not self.is_enabled():
            return {"status": "disabled", **base}
        if self._llm is None:
            return {"status": "unavailable", **base}
        payload = json.dumps(subject, ensure_ascii=False, sort_keys=True, default=str)
        messages = [
            SystemMessage(content=(
                "You are an independent code reviewer. Examine the supplied goal and "
                "evidence for risks, gaps and failing assumptions. Report concrete "
                "findings only. You cannot mark the goal complete; the project verifier "
                "is the sole completion authority."
            )),
            HumanMessage(content=payload),
        ]
        try:
            response = self._llm.get_model().invoke(messages)
        except Exception as exc:
            return {"status": "error", "error_type": type(exc).__name__, **base}
        review_text = str(getattr(response, "content", ""))[:65_536]
        if not review_text.strip():
            return {"status": "error", "error_type": "EmptyReview", **base}
        artifact_id = uuid4().hex
        path = Path(self._path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            os.chmod(path, 0o600)
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT INTO review_artifacts "
                "(id, agent_id, subject_sha256, review_text, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    self._agent_id,
                    hashlib.sha256(payload.encode()).hexdigest(),
                    review_text,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return {
            "status": "completed",
            "artifact_id": artifact_id,
            "review_text": review_text,
            **base,
        }

    def get_plugin_info(self) -> dict[str, str]:
        return {
            "name": self._plugin_name,
            "version": self._plugin_version,
            "enabled": str(self.is_enabled()).lower(),
        }
