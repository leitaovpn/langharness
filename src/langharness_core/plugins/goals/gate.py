
"""The deterministic checks that stand between "done" and "complete".

Deliberately a pure function over values: this is the product's central
judgement, so it must be testable without Pelix, a workspace, or a subprocess.
The caller gathers the inputs; nothing here reaches out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langharness_core.contracts import (
    Goal,
    VerificationArtifact,
    VerificationReport,
)

MANUAL_EVIDENCE = "manual/user_review"


@dataclass(frozen=True, slots=True)
class GateRejection:
    """One reason completion was refused, phrased for the model to act on."""

    code: str
    message: str


def evaluate_completion(
    goal: Goal,
    *,
    expected_revision: int,
    report: VerificationReport | None,
    current_generation: int,
    artifacts: Sequence[VerificationArtifact],
    blockers: Sequence[str],
) -> tuple[GateRejection, ...]:
    """Return every reason this goal may not complete; empty means it may.

    All reasons are returned rather than the first, so one round of the loop
    can fix everything that is wrong instead of discovering it one at a time.
    """
    rejections: list[GateRejection] = []
    if goal.revision != expected_revision:
        rejections.append(
            GateRejection(
                "goal_revision_mismatch",
                f"goal is at revision {goal.revision}, not {expected_revision}; "
                "the objective changed since this work started",
            )
        )
    if report is None:
        rejections.append(
            GateRejection(
                "verification_missing",
                "no verification report exists for this goal; run verify first",
            )
        )
    else:
        if not report.passed:
            rejections.append(
                GateRejection(
                    "verification_failed",
                    f"verification report {report.id} did not pass",
                )
            )
        for check in report.checks:
            if check.exit_code != 0:
                rejections.append(
                    GateRejection(
                        "check_failed",
                        f"check {check.name!r} exited {check.exit_code}: "
                        f"{' '.join(check.command)}",
                    )
                )
        if report.mutation_generation != current_generation:
            rejections.append(
                GateRejection(
                    "verification_stale",
                    f"report was produced at generation "
                    f"{report.mutation_generation} but the workspace is at "
                    f"{current_generation}; the files changed after it ran",
                )
            )
    for criterion in goal.acceptance_criteria:
        if not criterion.evidence:
            rejections.append(
                GateRejection(
                    "criterion_without_evidence",
                    f"acceptance criterion {criterion.id!r} "
                    f"({criterion.text!r}) has no evidence attached",
                )
            )
    if blockers:
        rejections.append(
            GateRejection(
                "pending_activity",
                "work is still outstanding: " + "; ".join(blockers),
            )
        )
    needs_artifact = any(
        criterion.evidence != MANUAL_EVIDENCE
        for criterion in goal.acceptance_criteria
    )
    if needs_artifact and not artifacts:
        rejections.append(
            GateRejection(
                "no_artifacts",
                "no workspace file changed since the goal started, so there is "
                "nothing to point at as the deliverable; mark the criteria "
                f"{MANUAL_EVIDENCE!r} if the goal truly produces no file",
            )
        )
    return tuple(rejections)
