
"""Decides whether the agent keeps working after a turn goes quiet.

A turn going quiet means the model stopped calling tools. It does not mean the
goal is done — that is `finish_goal`'s call and the host's gate. This component
sits between the two: while the goal is active it hands the loop another round,
and it is the only thing that ends an automatic run.
"""

from __future__ import annotations

from typing import Any

from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)

from langharness_core.contracts import (
    AwaitUser,
    Continue,
    GoalProvider,
    RunControllerProvider,
    RunDecision,
    Stop,
)
from langharness_core.plugins.goals.sqlite import GoalConflictError
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

WRAPUP_COMPLETE = (
    "The goal is complete. Write the final answer now, from facts you can "
    "point at: what changed, which tool results or hashes prove it, which "
    "checks you ran, and what risks remain. Do not call more tools."
)
WRAPUP_BLOCKED = (
    "The goal is blocked. Write the final answer now: what you tried, what "
    "stopped you, and exactly what would unblock it. Do not call more tools."
)
WRAPUP_NO_CHECKPOINTER = (
    "This run cannot continue automatically because there is no checkpointer, "
    "so the conversation would restart and lose its context. Stop here and "
    "tell the user what is left to do."
)


@ComponentFactory("goal-round-controller-plugin-factory")
@Provides(RunControllerProvider)
@Property("_plugin_name", "plugin.name", "goal-round-controller")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals")
class GoalRoundController:
    """Keeps an active goal moving until it settles or runs out of rounds."""

    def __init__(self) -> None:
        self._plugin_name = "goal-round-controller"
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

    def decide(
        self, *, rounds_run: int, checkpointer_available: bool
    ) -> RunDecision:
        if self._goals is None:
            return Stop(status="cancelled", reason="no-goal-store", wrapup=WRAPUP_BLOCKED)
        goal = self._goals.current_goal()
        if goal is None:
            return Stop(status="complete", reason="no-goal", wrapup=WRAPUP_COMPLETE)
        if goal.status == "awaiting_user":
            return AwaitUser(reason=f"goal {goal.id} is waiting on the user")
        if goal.status == "complete":
            return Stop(status="complete", reason="done", wrapup=WRAPUP_COMPLETE)
        if goal.status == "blocked":
            return Stop(status="blocked", reason="blocked", wrapup=WRAPUP_BLOCKED)
        if not checkpointer_available:
            return Stop(
                status="active",
                reason="no-checkpointer",
                wrapup=WRAPUP_NO_CHECKPOINTER,
            )
        # The goal carries its own limit, set when it was created. Keeping the
        # budget on the goal rather than on this component means it survives a
        # restart alongside the goal it describes.
        limit = int(goal.max_rounds)
        if goal.rounds_started >= limit:
            self._mark_blocked(goal, "round-limit")
            return Stop(
                status="blocked",
                reason="round-limit",
                wrapup=f"The goal hit its {limit}-round limit. " + WRAPUP_BLOCKED,
            )
        return self._continue(goal, limit)

    def _continue(self, goal: Any, limit: int) -> RunDecision:
        try:
            updated = self._goals.update_goal(
                goal.id, expected_revision=goal.revision, start_round=True
            )
        except GoalConflictError:
            # Someone moved the goal under us; let the next decision see it.
            return Stop(status="active", reason="goal-moved", wrapup=WRAPUP_BLOCKED)
        return Continue(
            message=(
                f"[goal {updated.id} · revision {updated.revision} · "
                f"round {updated.rounds_started} of {limit}]\n"
                f"Objective: {updated.objective}\n"
                "The turn went quiet but the goal is not complete. Continue "
                "working on it. If you believe it is done, call verify to see "
                "what is still missing, then finish_goal with evidence. If you "
                "need a decision only the user can make, call ask_user."
            ),
            round=updated.rounds_started,
        )

    def _mark_blocked(self, goal: Any, reason: str) -> None:
        try:
            self._goals.update_goal(
                goal.id,
                expected_revision=goal.revision,
                status="blocked",
                blocker=reason,
            )
        except GoalConflictError:
            return
