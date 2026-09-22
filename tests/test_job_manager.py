
"""Contract tests for the job manager service."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_JOB_MANAGER,
    JobManagerProvider,
    JobRecord,
)
from langharness_core.plugins.exec.jobs import JobManagerPlugin
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


def _group_alive(pid: int) -> bool:

    try:
        os.getpgid(pid)
    except ProcessLookupError:
        return False
    listing = subprocess.run(
        ["pgrep", "-g", str(pid)], capture_output=True, text=True, check=False
    )
    return bool(listing.stdout.split())


def manager(tmp_path: Path) -> JobManagerPlugin:
    plugin = JobManagerPlugin()
    return plugin


def shell(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_command_that_exits_is_recorded_as_exited(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print('hi')"), cwd=str(tmp_path), owner="a::s")
    finished = plugin.wait_job(record.id, 10)
    assert finished.state == "exited"
    assert finished.exit_code == 0
    assert finished.owner == "a::s"


def test_output_can_be_read_incrementally(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print('one')"), cwd=str(tmp_path), owner="a::s")
    plugin.wait_job(record.id, 10)
    text, offset = plugin.read_output(record.id, 0)
    assert "one" in text
    assert offset == len(text)
    again, offset2 = plugin.read_output(record.id, offset)
    assert again == ""
    assert offset2 == offset


def test_an_unknown_job_is_none(tmp_path: Path) -> None:
    assert manager(tmp_path).get_job("nope") is None


def test_jobs_are_listed_per_owner(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    plugin.start_job(shell("print(1)"), cwd=str(tmp_path), owner="a::s")
    plugin.start_job(shell("print(2)"), cwd=str(tmp_path), owner="b::s")
    assert len(plugin.list_jobs("a::s")) == 1
    assert len(plugin.list_jobs()) == 2
    assert plugin.list_jobs("a::s")[0].owner == "a::s"


def test_stopping_a_job_kills_its_process_group(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    record = plugin.start_job(shell(script), cwd=str(tmp_path), owner="a::s")
    time.sleep(0.6)
    stopped = plugin.stop_job(record.id)
    assert stopped.state == "killed"
    time.sleep(0.3)
    assert not _group_alive(record.pid)


def test_waiting_past_the_deadline_returns_the_running_job(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(
        shell("import time; time.sleep(30)"), cwd=str(tmp_path), owner="a::s"
    )
    still = plugin.wait_job(record.id, 0.3)
    assert still.state == "running"
    plugin.stop_job(record.id)


def test_write_stdin_reaches_the_process(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(
        shell("import sys; print(sys.stdin.readline().strip())"),
        cwd=str(tmp_path),
        owner="a::s",
    )
    plugin.write_stdin(record.id, "piped\n")
    plugin.wait_job(record.id, 10)
    text, _ = plugin.read_output(record.id, 0)
    assert "piped" in text


def test_writing_to_a_finished_job_is_refused(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print(1)"), cwd=str(tmp_path), owner="a::s")
    plugin.wait_job(record.id, 10)
    with pytest.raises(RuntimeError):
        plugin.write_stdin(record.id, "late\n")


def test_unknown_jobs_raise_lookups_errors(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    with pytest.raises(KeyError):
        plugin.write_stdin("nope", "x")
    with pytest.raises(KeyError):
        plugin.wait_job("nope", 1)
    with pytest.raises(KeyError):
        plugin.stop_job("nope")


def test_events_are_queued_and_drained_once(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print('evt')"), cwd=str(tmp_path), owner="a::s")
    plugin.wait_job(record.id, 10)
    events = plugin.drain_events()
    kinds = [item["type"] for item in events]
    assert kinds[0] == "job_started"
    assert "job_finished" in kinds
    assert plugin.drain_events() == []
    assert events[0]["job_id"] == record.id


def test_the_job_cap_is_enforced(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    plugin._max_jobs = 2
    plugin.start_job(shell("import time; time.sleep(5)"), cwd=str(tmp_path), owner="a::s")
    plugin.start_job(shell("import time; time.sleep(5)"), cwd=str(tmp_path), owner="a::s")
    with pytest.raises(RuntimeError):
        plugin.start_job(shell("print(1)"), cwd=str(tmp_path), owner="a::s")
    plugin.shutdown_jobs()


def test_shutdown_kills_every_live_job(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(
        shell("import time; time.sleep(30)"), cwd=str(tmp_path), owner="a::s"
    )
    plugin.shutdown_jobs()
    time.sleep(0.3)
    assert not _group_alive(record.pid)
    assert plugin.list_jobs() == []


def test_the_manager_conforms_to_its_contract(tmp_path: Path) -> None:
    from langharness_core.contracts import JobManagerProvider
    from langharness_plugin.validation import validate

    assert validate(manager(tmp_path), JobManagerProvider) == ()


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert manager(tmp_path).get_plugin_info() == {
        "name": "job-manager",
        "version": "1.0.0",
    }
