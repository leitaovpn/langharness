
"""Tests for the completion gate."""

from __future__ import annotations

from langharness_core.contracts import (
    AcceptanceCriterion,
    Goal,
    VerificationArtifact,
    VerificationCheck,
    VerificationReport,
)
from langharness_core.plugins.goals.gate import (
    MANUAL_EVIDENCE,
    GateRejection,
    evaluate_completion,
)


def goal(**overrides: object) -> Goal:
    values: dict[str, object] = {
        "id": "g1",
        "revision": 3,
        "objective": "fix the bug",
        "acceptance_criteria": (
            AcceptanceCriterion(id="c1", text="test passes", evidence="tool:call_1"),
        ),
        "status": "verifying",
        "rounds_started": 2,
        "max_rounds": 32,
        "mutation_generation": 2,
        "blocker_history": (),
        "created_at": "a",
        "updated_at": "b",
    }
    values.update(overrides)
    return Goal(**values)  # type: ignore[arg-type]


def report(**overrides: object) -> VerificationReport:
    values: dict[str, object] = {
        "id": "vr1",
        "goal_revision": 3,
        "mutation_generation": 7,
        "started_at": "a",
        "finished_at": "b",
        "checks": (
            VerificationCheck(
                name="make check",
                command=("make", "check"),
                exit_code=0,
                stdout_digest="sha256:x",
                duration_ms=10,
            ),
        ),
        "artifacts": (VerificationArtifact(path="src/a.py", sha256="sha256:y"),),
        "passed": True,
    }
    values.update(overrides)
    return VerificationReport(**values)  # type: ignore[arg-type]


def codes(rejections: tuple[GateRejection, ...]) -> list[str]:
    return [item.code for item in rejections]


def test_a_complete_goal_with_fresh_evidence_passes() -> None:
    assert (
        evaluate_completion(
            goal(),
            expected_revision=3,
            report=report(),
            current_generation=7,
            artifacts=report().artifacts,
            blockers=(),
        )
        == ()
    )


def test_a_stale_revision_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(revision=4),
        expected_revision=3,
        report=report(goal_revision=3),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["goal_revision_mismatch"]


def test_a_missing_report_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=None,
        current_generation=7,
        artifacts=(),
        blockers=(),
    )
    # Both are true at once: there is no report, and nothing was produced.
    assert codes(rejections) == ["verification_missing", "no_artifacts"]


def test_a_failed_report_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(passed=False),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["verification_failed"]


def test_editing_after_verification_invalidates_the_report() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(mutation_generation=7),
        current_generation=8,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["verification_stale"]


def test_a_failing_check_is_named_in_the_rejection() -> None:
    failing = VerificationCheck(
        name="make check",
        command=("make", "check"),
        exit_code=2,
        stdout_digest="sha256:z",
        duration_ms=3,
    )
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(checks=(failing,), passed=False),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert "check_failed" in codes(rejections)
    assert any("make check" in item.message for item in rejections)


def test_a_criterion_without_evidence_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(
            acceptance_criteria=(
                AcceptanceCriterion(id="c1", text="it works", evidence=None),
            )
        ),
        expected_revision=3,
        report=report(),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["criterion_without_evidence"]
    assert "c1" in rejections[0].message


def test_pending_activity_blocks_completion() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=("job j1 still running",),
    )
    assert codes(rejections) == ["pending_activity"]
    assert "j1" in rejections[0].message


def test_a_goal_that_changed_nothing_has_no_artifacts() -> None:
    rejections = evaluate_completion(
        goal(mutation_generation=7),
        expected_revision=3,
        report=report(),
        current_generation=7,
        artifacts=(),
        blockers=(),
    )
    assert codes(rejections) == ["no_artifacts"]


def test_a_manual_only_goal_needs_no_artifacts() -> None:
    assert (
        evaluate_completion(
            goal(
                acceptance_criteria=(
                    AcceptanceCriterion(
                        id="c1", text="looks right", evidence=MANUAL_EVIDENCE
                    ),
                ),
                mutation_generation=7,
            ),
            expected_revision=3,
            report=report(artifacts=()),
            current_generation=7,
            artifacts=(),
            blockers=(),
        )
        == ()
    )


def test_every_rejection_is_reported_not_just_the_first() -> None:
    rejections = evaluate_completion(
        goal(
            revision=9,
            acceptance_criteria=(
                AcceptanceCriterion(id="c1", text="t", evidence=None),
            ),
        ),
        expected_revision=3,
        report=None,
        current_generation=7,
        artifacts=(),
        blockers=("approval pending",),
    )
    assert set(codes(rejections)) == {
        "goal_revision_mismatch",
        "verification_missing",
        "criterion_without_evidence",
        "pending_activity",
        "no_artifacts",
    }
