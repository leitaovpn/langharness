
"""The goal tools: read the objective, and try to close it."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    GoalProvider,
    ToolProvider,
    VerificationProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.goals.gate import evaluate_completion
from langharness_core.plugins.goals.sqlite import BLOCKER_THRESHOLD, GoalConflictError
from langharness_core.plugins.tools.output_schemas import OUTPUT_SCHEMAS
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


class FinishGoalArgs(BaseModel):
    status: Literal["complete", "blocked"] = Field(
        description="'complete' to close the goal, 'blocked' to give up on it."
    )
    summary: str = Field(
        min_length=1, description="One-paragraph account of what happened."
    )
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps acceptance-criterion id to its evidence: a tool call id, a "
            "file sha256, a test case name, or 'manual/user_review'."
        ),
    )
    blocker: str | None = Field(
        default=None, description="Required when status is 'blocked'."
    )


def _goal_payload(goal: Any) -> dict[str, Any]:
    return {
        "id": goal.id,
        "revision": goal.revision,
        "objective": goal.objective,
        "status": goal.status,
        "rounds_started": goal.rounds_started,
        "max_rounds": goal.max_rounds,
        "acceptance_criteria": [
            {"id": item.id, "text": item.text, "evidence": item.evidence}
            for item in goal.acceptance_criteria
        ],
        "blocker_history": list(goal.blocker_history),
    }


@ComponentFactory("goal-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "goal-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@RequiresBest("_verifier", VerificationProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals", "_verifier", "_state")
class GoalToolsPlugin:
    """Exposes the goal to the model, and enforces the completion gate."""

    def __init__(self) -> None:
        self._plugin_name = "goal-tools"
        self._plugin_version = "1.0.0"
        self._goals: Any = None
        self._verifier: Any = None
        self._state: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_goals": ContractGuard(self, "_goals", GoalProvider),
            "_verifier": ContractGuard(self, "_verifier", VerificationProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

    @BindField("_goals", if_valid=True)
    def _on_goals_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_goals")
    def _on_goals_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_verifier", if_valid=True)
    def _on_verifier_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_verifier")
    def _on_verifier_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_state", if_valid=True)
    def _on_state_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_state")
    def _on_state_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._goals is None or self._verifier is None:
            return []
        return [self._get_tool(), self._finish_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        """``finish_goal`` is absent: its summary reads better as the bare name."""
        return {"get_goal": "get_goal()"}

    def _get_tool(self) -> StructuredTool:
        plugin = self

        def get_goal() -> dict[str, Any]:
            goal = plugin._goals.current_goal()
            return {"goal": _goal_payload(goal) if goal is not None else None}

        return StructuredTool.from_function(
            func=get_goal,
            name="get_goal",
            description=(
                "Read the current goal: its objective, status, revision, "
                "acceptance criteria and how many rounds have run. Check it "
                "before deciding you are finished, and re-read it after any "
                "rejection — a rejected completion leaves the goal unchanged, "
                "and the rejection names what is still missing. Returns "
                "{\"goal\": {\"id\": str, \"revision\": int, \"objective\": "
                "str, \"status\": str, \"rounds_started\": int, \"max_rounds\": "
                "int, \"acceptance_criteria\": [{\"id\": str, \"text\": str, "
                "\"evidence\": str|null}], \"blocker_history\": [str]}|null}."
            ),
            args_schema=NoArgs,
            metadata={"output_schema": OUTPUT_SCHEMAS["get_goal"]},
        )

    def _finish_tool(self) -> StructuredTool:
        plugin = self

        def finish_goal(
            status: str,
            summary: str,
            evidence: dict[str, str] | None = None,
            blocker: str | None = None,
        ) -> dict[str, Any]:
            goal = plugin._goals.current_goal()
            if goal is None:
                return tool_error("no_active_goal", "there is no goal to finish")
            if status == "blocked":
                return plugin._block(goal, summary, blocker)
            if status != "complete":
                return tool_error(
                    "invalid_arguments", f"unknown status {status!r}"
                )
            return plugin._complete(goal, summary, evidence or {})

        return StructuredTool.from_function(
            func=finish_goal,
            name="finish_goal",
            description=(
                "Ask the host to close the current goal. This is the only way a "
                "goal reaches 'complete': the host runs the project's gates and "
                "checks that the report is fresh, that every acceptance "
                "criterion carries evidence, and that nothing is still "
                "outstanding. A rejection tells you exactly what is missing and "
                "leaves the goal untouched — fix it and try again rather than "
                "restating that you are done. Use status='blocked' only when "
                "the same blocker has repeated; the host refuses the first few "
                "attempts so a goal is not abandoned early. Returns {\"status\": "
                "'complete'|'blocked', \"goal_id\": str, \"revision\": int, "
                "\"report_id\": str|null, \"summary\": str}, or {\"error\": "
                "{code, message, retryable, details}} where details lists every "
                "unmet gate condition."
            ),
            args_schema=FinishGoalArgs,
            metadata={"output_schema": OUTPUT_SCHEMAS["finish_goal"]},
        )

    def _complete(
        self, goal: Any, summary: str, evidence: dict[str, str]
    ) -> dict[str, Any]:
        report = self._verifier.verify(goal.revision)
        candidate = replace(
            goal,
            acceptance_criteria=tuple(
                replace(item, evidence=evidence.get(item.id, item.evidence))
                for item in goal.acceptance_criteria
            ),
        )
        current_generation = self._state.get_generation() if self._state else 0
        rejections = evaluate_completion(
            candidate,
            expected_revision=goal.revision,
            report=report,
            current_generation=current_generation,
            artifacts=report.artifacts,
            blockers=(),
        )
        if rejections:
            return tool_error(
                "completion_rejected",
                "the goal does not meet its completion conditions yet: "
                + "; ".join(item.message for item in rejections),
                retryable=True,
                reasons=[item.code for item in rejections],
                report_id=report.id,
            )
        try:
            updated = self._goals.update_goal(
                goal.id, expected_revision=goal.revision, status="complete"
            )
        except GoalConflictError as exc:
            return tool_error("goal_revision_mismatch", str(exc), retryable=True)
        self._goals.set_criterion_evidence(updated.id, evidence)
        return {
            "status": "complete",
            "goal_id": updated.id,
            "revision": updated.revision,
            "report_id": report.id,
            "summary": summary,
        }

    def _block(self, goal: Any, summary: str, blocker: str | None) -> dict[str, Any]:
        text = (blocker or "").strip()
        if not text:
            return tool_error(
                "invalid_arguments", "status='blocked' requires a blocker text"
            )
        streak = 0
        for previous in reversed(goal.blocker_history):
            if previous != text:
                break
            streak += 1
        attempts = streak + 1
        if attempts < BLOCKER_THRESHOLD:
            # The attempt has to be recorded or the streak can never grow and
            # the goal could never be given up on. Status stays where it is.
            try:
                self._goals.update_goal(
                    goal.id, expected_revision=goal.revision, blocker=text
                )
            except GoalConflictError as exc:
                return tool_error("goal_revision_mismatch", str(exc), retryable=True)
            return tool_error(
                "blocker_not_established",
                f"blocker recorded {attempts} of {BLOCKER_THRESHOLD} times; "
                "try a different approach before giving up, and describe what "
                "would unblock you",
                retryable=True,
                blocker=text,
            )
        try:
            updated = self._goals.update_goal(
                goal.id,
                expected_revision=goal.revision,
                status="blocked",
                blocker=text,
            )
        except GoalConflictError as exc:
            return tool_error("goal_revision_mismatch", str(exc), retryable=True)
        return {
            "status": "blocked",
            "goal_id": updated.id,
            "revision": updated.revision,
            "report_id": None,
            "summary": summary,
        }
