
"""Contract tests for the job manager service."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_JOB_MANAGER,
    JobManagerProvider,
    JobRecord,
)
from langharness_plugin.validation import contract_for


def sample_job(**overrides: object) -> JobRecord:
    values: dict[str, object] = {
        "id": "j1",
        "command": ("echo", "hi"),
        "cwd": "/tmp",
        "pid": 1234,
        "state": "running",
        "owner": "alpha::s1",
        "started_at": "2026-09-22T00:00:00+00:00",
        "exit_code": None,
    }
    values.update(overrides)
    return JobRecord(**values)  # type: ignore[arg-type]


def test_specification_is_pinned() -> None:
    assert contract_for(SPEC_JOB_MANAGER) is JobManagerProvider
    assert SPEC_JOB_MANAGER in ALL_PLUGIN_SPECS


def test_job_record_is_frozen_and_hashable() -> None:
    assert sample_job() == sample_job()
    assert hash(sample_job()) == hash(sample_job())


def test_a_running_job_has_no_exit_code() -> None:
    assert sample_job().exit_code is None


def test_a_finished_job_carries_its_exit_code() -> None:
    assert sample_job(state="exited", exit_code=0).exit_code == 0


def test_the_owner_is_part_of_identity() -> None:
    assert sample_job(owner="alpha::s1") != sample_job(owner="beta::s1")


def test_job_error_codes_are_representable() -> None:
    from langharness_core.common.tool_error import tool_error

    for code in ("job_not_found", "job_not_running", "timeout"):
        assert tool_error(code, "x")["error"]["code"] == code
