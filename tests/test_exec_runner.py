
"""Tests for the shared argv command runner."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from langharness_core.plugins.exec import runner


def test_a_command_reports_output_and_exit_code(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "print('hello')"], cwd=tmp_path, timeout_seconds=10
    )
    assert outcome.exit_code == 0
    assert outcome.output.strip() == "hello"
    assert outcome.truncated is False
    assert outcome.duration_ms >= 0
    assert outcome.command == (sys.executable, "-c", "print('hello')")


def test_stderr_is_captured_alongside_stdout(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom')"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert "boom" in outcome.output


def test_a_non_zero_exit_is_a_result_not_an_error(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "import sys; sys.exit(4)"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert outcome.exit_code == 4


def test_output_is_capped_and_flagged(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "print('x' * 5000)"],
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=100,
    )
    assert outcome.truncated is True
    assert len(outcome.output.encode()) <= 100


def test_a_hanging_command_times_out_and_is_killed(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=0.5,
    )
    assert outcome.exit_code == -1
    assert outcome.timed_out is True
    assert "timed out" in outcome.output.lower()


def test_a_missing_binary_is_reported_not_raised(tmp_path: Path) -> None:
    outcome = runner.run_command(
        ["/nonexistent/binary"], cwd=tmp_path, timeout_seconds=5
    )
    assert outcome.exit_code == -1
    assert "could not be run" in outcome.output


def test_the_whole_process_group_dies_with_the_command(tmp_path: Path) -> None:
    # A command that leaves a grandchild behind must not leak it on timeout.
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    outcome = runner.run_command(
        [sys.executable, "-c", script], cwd=tmp_path, timeout_seconds=0.8
    )
    assert outcome.timed_out is True
    assert not _group_alive(outcome.pid)


def test_a_process_that_ignores_sigterm_is_killed(tmp_path: Path) -> None:
    script = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "time.sleep(30)"
    )
    outcome = runner.run_command(
        [sys.executable, "-c", script], cwd=tmp_path, timeout_seconds=0.5
    )
    assert outcome.timed_out is True
    assert not _group_alive(outcome.pid)


def test_terminating_an_already_dead_process_is_a_no_op(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True
    )
    process.wait()
    # The leader is reaped, so its group id is gone; this must not raise.
    runner.terminate_process_group(process)


def test_cwd_is_honoured(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    outcome = runner.run_command(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=nested,
        timeout_seconds=10,
    )
    assert Path(outcome.output.strip()).resolve() == nested.resolve()


def test_default_environment_withholds_secrets(
    monkeypatch: object,
) -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "MY_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp_secret",
        "DB_PASSWORD": "hunter2",
        "SOME_CREDENTIALS": "x",
    }
    env = runner.build_environment(source=source)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/x"}


def test_an_allowlisted_name_is_still_refused_when_it_looks_secret() -> None:
    env = runner.build_environment(
        ["PATH", "MY_API_KEY"], source={"PATH": "/usr/bin", "MY_API_KEY": "sk-secret"}
    )
    assert env == {"PATH": "/usr/bin"}


def test_a_runner_with_no_allowlist_inherits_everything() -> None:
    source = {"PATH": "/usr/bin", "MY_API_KEY": "sk-secret"}
    assert runner.build_environment(None, source=source) == source


def test_the_default_allowlist_covers_what_a_toolchain_needs() -> None:
    for name in ("PATH", "HOME", "LANG", "TERM", "TMPDIR", "VIRTUAL_ENV"):
        assert name in runner.DEFAULT_ENV_ALLOWLIST


def _group_alive(pid: int) -> bool:
    try:
        os.getpgid(pid)
    except ProcessLookupError:
        return False
    listing = subprocess.run(
        ["pgrep", "-g", str(pid)], capture_output=True, text=True, check=False
    )
    return bool(listing.stdout.split())
