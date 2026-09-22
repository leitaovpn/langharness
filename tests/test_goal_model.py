
"""Contract tests for the goal and verification services."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_GOAL,
    SPEC_VERIFIER,
    TERMINAL_GOAL_STATUSES,
    AcceptanceCriterion,
    Goal,
    GoalProvider,
    VerificationArtifact,
    VerificationCheck,
    VerificationProvider,
    VerificationReport,
)
from langharness_plugin.validation import contract_for


def sample_goal(**overrides: object) -> Goal:
    values: dict[str, object] = {
        "id": "g1",
        "revision": 1,
        "objective": "fix the flaky test",
        "acceptance_criteria": (
            AcceptanceCriterion(id="c1", text="test passes"),
        ),
        "status": "active",
        "rounds_started": 0,
        "max_rounds": 32,
        "mutation_generation": 0,
        "blocker_history": (),
        "created_at": "2026-09-22T00:00:00+00:00",
        "updated_at": "2026-09-22T00:00:00+00:00",
    }
    values.update(overrides)
    return Goal(**values)  # type: ignore[arg-type]


def test_specifications_are_pinned() -> None:
    assert contract_for(SPEC_GOAL) is GoalProvider
    assert contract_for(SPEC_VERIFIER) is VerificationProvider
    assert SPEC_GOAL in ALL_PLUGIN_SPECS
    assert SPEC_VERIFIER in ALL_PLUGIN_SPECS


def test_terminal_statuses_are_exactly_the_three_ends() -> None:
    assert TERMINAL_GOAL_STATUSES == {"complete", "blocked", "cancelled"}
    assert "active" not in TERMINAL_GOAL_STATUSES
    assert "awaiting_user" not in TERMINAL_GOAL_STATUSES


def test_goal_is_frozen_and_hashable() -> None:
    goal = sample_goal()
    assert goal == sample_goal()
    assert hash(goal) == hash(sample_goal())


def test_goal_revision_is_part_of_identity() -> None:
    assert sample_goal(revision=1) != sample_goal(revision=2)


def test_criteria_default_to_no_evidence() -> None:
    assert AcceptanceCriterion(id="c1", text="t").evidence is None


def test_report_carries_generation_and_checks() -> None:
    report = VerificationReport(
        id="vr1",
        goal_revision=3,
        mutation_generation=7,
        started_at="a",
        finished_at="b",
        checks=(
            VerificationCheck(
                name="make check",
                command=("make", "check"),
                exit_code=0,
                stdout_digest="sha256:x",
                duration_ms=12,
            ),
        ),
        artifacts=(VerificationArtifact(path="src/a.py", sha256="sha256:y"),),
        passed=True,
    )
    assert report.mutation_generation == 7
    assert report.checks[0].command == ("make", "check")
    assert report.artifacts[0].path == "src/a.py"


def test_a_failed_check_makes_the_report_representable() -> None:
    report = VerificationReport(
        id="vr2",
        goal_revision=1,
        mutation_generation=1,
        started_at="a",
        finished_at="b",
        checks=(
            VerificationCheck(
                name="make check",
                command=("make", "check"),
                exit_code=2,
                stdout_digest="sha256:z",
                duration_ms=5,
            ),
        ),
        artifacts=(),
        passed=False,
    )
    assert report.passed is False
