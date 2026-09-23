
"""The `verify` tool: run the gates and see what completion would say.

Distinct from `finish_goal` on purpose. The design lists both, and the split is
what lets a model check its work *before* submitting rather than learning what
is missing from a rejection.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    GoalProvider,
    ToolProvider,
    VerificationProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.goals.gate import evaluate_completion
from langharness_core.plugins.tools.output_schemas import OUTPUT_SCHEMAS
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


@ComponentFactory("verify-tool-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "verify-tool")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@RequiresBest("_verifier", VerificationProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals", "_verifier", "_state")
class VerifyToolPlugin:
    """Runs the project's gates and reports whether completion would be accepted."""

    def __init__(self) -> None:
        self._plugin_name = "verify-tool"
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
        return [self._verify_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        return {"verify": "verify()"}

    def _verify_tool(self) -> StructuredTool:
        plugin = self

        def verify() -> dict[str, Any]:
            goal = plugin._goals.current_goal()
            if goal is None:
                return tool_error("no_active_goal", "there is no goal to verify")
            report = plugin._verifier.verify(goal.revision)
            current = plugin._state.get_generation() if plugin._state else 0
            # Judged with the same gate finish_goal uses, so the two can never
            # disagree about what "ready" means.
            rejections = evaluate_completion(
                goal,
                expected_revision=goal.revision,
                report=report,
                current_generation=current,
                artifacts=report.artifacts,
                blockers=(),
            )
            return {
                "report_id": report.id,
                "goal_revision": goal.revision,
                "mutation_generation": current,
                "passed": report.passed,
                "checks": [
                    {
                        "name": check.name,
                        "exit_code": check.exit_code,
                        "duration_ms": check.duration_ms,
                    }
                    for check in report.checks
                ],
                "would_complete": not rejections,
                "blockers": [item.message for item in rejections],
            }

        return StructuredTool.from_function(
            func=verify,
            name="verify",
            description=(
                "Run the project's gate commands and report whether "
                "finish_goal(status='complete') would be accepted right now. "
                "Use it BEFORE submitting: 'blockers' names exactly what is "
                "still missing, so you can fix it in this turn instead of "
                "burning a round on a rejection. This only observes — it never "
                "changes the goal. Note that any command you run afterwards "
                "counts as a workspace change and makes this report stale, so "
                "verify last. Returns {\"report_id\": str, \"goal_revision\": "
                "int, \"mutation_generation\": int, \"passed\": bool, "
                "\"checks\": [{\"name\": str, \"exit_code\": int, "
                "\"duration_ms\": int}], \"would_complete\": bool, "
                "\"blockers\": [str]}, or {\"error\": {code, message, "
                "retryable, details}} when there is no active goal."
            ),
            args_schema=NoArgs,
            metadata={"output_schema": OUTPUT_SCHEMAS["verify"]},
        )
