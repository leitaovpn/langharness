
"""The `ask_user` tool: hand a decision back to the person.

The host cannot tell whether a question truly needed a human, so this is a
policy the model follows rather than one the host enforces. What the host does
enforce is the consequence: the goal leaves `active`, so the run controller
stops auto-continuing until a real user message arrives.
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
from pydantic import BaseModel, Field

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import GoalProvider, ToolProvider
from langharness_core.plugins.goals.sqlite import GoalConflictError
from langharness_core.plugins.tools.output_schemas import OUTPUT_SCHEMAS
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class AskUserArgs(BaseModel):
    question: str = Field(
        min_length=1,
        description="The single decision you need from the user, stated plainly.",
    )


@ComponentFactory("ask-user-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "ask-user")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals")
class AskUserToolPlugin:
    """Pauses the goal so a human can answer a question the repo cannot."""

    def __init__(self) -> None:
        self._plugin_name = "ask-user"
        self._plugin_version = "1.0.0"
        self._goals: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_goals": ContractGuard(self, "_goals", GoalProvider),
        }

    @BindField("_goals", if_valid=True)
    def _on_goals_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_goals")
    def _on_goals_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._goals is None:
            return []
        return [self._ask_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        return {"ask_user": "ask_user({question})"}

    def _ask_tool(self) -> StructuredTool:
        plugin = self

        def ask_user(question: str) -> dict[str, Any]:
            text = question.strip()
            if not text:
                return tool_error("invalid_arguments", "the question is empty")
            goal = plugin._goals.current_goal()
            if goal is None:
                return tool_error("no_active_goal", "there is no goal to pause")
            try:
                updated = plugin._goals.update_goal(
                    goal.id,
                    expected_revision=goal.revision,
                    status="awaiting_user",
                )
            except GoalConflictError as exc:
                return tool_error("goal_revision_mismatch", str(exc), retryable=True)
            return {
                "status": updated.status,
                "goal_id": updated.id,
                "question": text,
                "note": (
                    "The run is now paused and will not continue on its own. "
                    "The user answers in their next message; treat that answer "
                    "as the decision and resume from there. Do not guess an "
                    "answer and do not keep working in the meantime."
                ),
            }

        return StructuredTool.from_function(
            func=ask_user,
            name="ask_user",
            description=(
                "Ask the user to decide something you cannot determine from the "
                "repository. Use it ONLY for choices that genuinely change the "
                "requirements — which of two behaviours is wanted, whether a "
                "breaking change is acceptable, what to do when the spec is "
                "ambiguous. Do not use it for anything you could find by "
                "reading the code or running a command, and do not use it to "
                "report progress. Calling it pauses the run: it will not "
                "continue until the user replies. Returns {\"status\": "
                "'awaiting_user', \"goal_id\": str, \"question\": str, "
                "\"note\": str}, or {\"error\": {code, message, retryable, "
                "details}}."
            ),
            args_schema=AskUserArgs,
            metadata={"output_schema": OUTPUT_SCHEMAS["ask_user"]},
        )
