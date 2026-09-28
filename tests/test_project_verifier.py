
"""Tests for the host-side project verifier."""
# mypy: ignore-errors

from __future__ import annotations

import sys
from pathlib import Path

from langharness_core.contracts import VerificationProvider
from langharness_core.plugins.verifier.project import (
    ProjectVerifier,
    _artifact_set,
)
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def verifier(workspace: Path, *commands: list[str]) -> ProjectVerifier:
    instance = ProjectVerifier()
    root = WorkspaceRootPlugin()
    root._root = str(workspace)
    instance._workspace = root
    instance._state = WorkspaceStatePlugin()
    instance._commands = [list(item) for item in commands]
    return instance


def ok(*text: str) -> list[str]:
    return [sys.executable, "-c", f"print({text[0]!r})" if text else "print('ok')"]


def failing(*text: str) -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(3)"]


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(verifier(tmp_path), VerificationProvider) == ()


def test_a_passing_command_produces_a_passing_report(tmp_path: Path) -> None:
    report = verifier(tmp_path, ok("all good")).verify(goal_revision=2)
    assert report.passed is True
    assert report.goal_revision == 2
    assert report.checks[0].exit_code == 0
    assert report.checks[0].stdout_digest.startswith("sha256:")
    assert report.checks[0].duration_ms >= 0
    assert report.id.startswith("vr_")


def test_a_failing_command_makes_the_report_fail(tmp_path: Path) -> None:
    report = verifier(tmp_path, failing()).verify(goal_revision=1)
    assert report.passed is False
    assert report.checks[0].exit_code == 3


def test_every_command_runs_even_after_one_fails(tmp_path: Path) -> None:
    report = verifier(tmp_path, failing(), ok("second")).verify(goal_revision=1)
    assert [check.exit_code for check in report.checks] == [3, 0]


def test_no_configured_commands_passes_with_no_checks(tmp_path: Path) -> None:
    report = verifier(tmp_path).verify(goal_revision=1)
    assert report.checks == ()
    assert report.passed is True


def test_the_report_records_the_generation_it_verified(tmp_path: Path) -> None:
    instance = verifier(tmp_path, ok())
    instance._state.record_mutation("a.py", "apply_patch")
    report = instance.verify(goal_revision=1)
    assert report.mutation_generation == 1


def test_a_command_that_writes_does_not_advance_the_generation(tmp_path: Path) -> None:
    # The gate's own build artifacts must not invalidate the gate's own report.
    writer = [
        sys.executable,
        "-c",
        f"open({str(tmp_path / '.coverage')!r}, 'w').write('x')",
    ]
    instance = verifier(tmp_path, writer)
    report = instance.verify(goal_revision=1)
    assert instance._state.get_generation() == 0
    assert report.mutation_generation == 0


def test_a_hanging_command_is_timed_out(tmp_path: Path) -> None:
    instance = verifier(tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"])
    instance._timeout_seconds = 1
    report = instance.verify(goal_revision=1)
    assert report.passed is False
    assert report.checks[0].exit_code == -1
    assert "timed out" in _last_output(instance).lower()


def test_a_missing_binary_is_reported_not_raised(tmp_path: Path) -> None:
    report = verifier(tmp_path, ["/nonexistent/binary"]).verify(goal_revision=1)
    assert report.passed is False
    assert report.checks[0].exit_code == -1


def test_artifacts_come_from_recorded_mutations(tmp_path: Path) -> None:
    instance = verifier(tmp_path, ok())
    instance._state.record_mutation(
        "src/a.py", "apply_patch", after_sha256="sha256:new"
    )
    instance._state.record_mutation("src/gone.py", "apply_patch", after_sha256=None)
    report = instance.verify(goal_revision=1)
    assert report.artifacts == (
        type(report.artifacts[0])(path="src/a.py", sha256="sha256:new"),
    )


def test_artifact_set_keeps_the_newest_hash_per_path() -> None:
    from langharness_core.contracts import MutationRecord

    records = [
        MutationRecord("a.py", 1, "apply_patch", None, "sha256:v1"),
        MutationRecord("a.py", 2, "apply_patch", "sha256:v1", "sha256:v2"),
        MutationRecord("b.py", 3, "apply_patch", "sha256:x", None),
    ]
    assert [(item.path, item.sha256) for item in _artifact_set(records)] == [
        ("a.py", "sha256:v2")
    ]


def _last_output(instance: ProjectVerifier) -> str:
    return instance._last_output
