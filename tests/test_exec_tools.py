
"""Tests for the exec_command tool."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import sys
from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.exec.jobs import JobManagerPlugin
from langharness_core.plugins.exec.tools import ExecToolsPlugin
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path) -> ExecToolsPlugin:
    instance = ExecToolsPlugin()
    root = WorkspaceRootPlugin()
    root._root = str(tmp_path)
    instance._workspace = root
    instance._state = WorkspaceStatePlugin()
    instance._jobs = JobManagerPlugin()
    return instance


def tools(instance: ExecToolsPlugin) -> dict[str, object]:
    return {item.name: item for item in instance.get_tools()}


def run(instance: ExecToolsPlugin, **args: object) -> dict:
    return tools(instance)["exec_command"].invoke(args)


def test_no_workspace_means_no_tools() -> None:
    assert ExecToolsPlugin().get_tools() == []


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_a_command_runs_in_the_workspace(tmp_path: Path) -> None:
    result = run(
        build(tmp_path), command=[sys.executable, "-c", "import os; print(os.getcwd())"]
    )
    assert result["exit_code"] == 0
    assert Path(result["output"].strip()).resolve() == tmp_path.resolve()


def test_exec_command_uses_bound_sandbox_and_reports_isolation(tmp_path: Path) -> None:
    instance = build(tmp_path)

    class Sandbox:
        def get_isolation(self) -> str:
            return "os-sandbox"

        def wrap_command(
            self,
            argv: list[str],
            cwd: str,
            workspace_root: str | None = None,
            read_only_paths: list[str] | None = None,
        ) -> list[str]:
            return [*argv]

    instance._sandbox = Sandbox()
    result = run(instance, command=[sys.executable, "-c", "print('ok')"])

    assert result["isolation"] == "os-sandbox"


def test_a_relative_cwd_is_resolved_against_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    result = run(
        build(tmp_path),
        command=[sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd="sub",
    )
    assert Path(result["output"].strip()).resolve() == (tmp_path / "sub").resolve()


def test_a_cwd_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    result = run(build(tmp_path), command=["echo", "hi"], cwd="../..")
    assert result["error"]["code"] == "path_outside_workspace"


def test_a_command_bumps_the_mutation_generation(tmp_path: Path) -> None:
    instance = build(tmp_path)
    assert instance._state.get_generation() == 0
    run(instance, command=[sys.executable, "-c", "print(1)"])
    assert instance._state.get_generation() == 1
    assert instance._state.mutations()[0].tool == "exec_command"


def test_a_failing_command_still_bumps_the_generation(tmp_path: Path) -> None:
    # Design 6.4: whether a command wrote is not the model's call to make.
    instance = build(tmp_path)
    run(instance, command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    assert instance._state.get_generation() == 1


def test_the_timeout_is_clamped_to_the_ceiling(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._max_timeout_seconds = 5
    result = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=600,
    )
    assert result["error"]["code"] == "timeout"
    assert result["error"]["details"]["limit_seconds"] == 5


def test_a_timed_out_command_reports_the_timeout(tmp_path: Path) -> None:
    instance = build(tmp_path)
    result = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=1,
    )
    assert result["error"]["code"] == "timeout"
    assert result["error"]["retryable"] is True


def test_the_environment_withholds_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "sk-secret")
    instance = build(tmp_path)
    result = run(
        instance,
        command=[sys.executable, "-c", "import os; print(os.environ.get('MY_API_KEY'))"],
    )
    assert result["output"].strip() == "None"


def test_background_returns_a_job_id_immediately(tmp_path: Path) -> None:
    instance = build(tmp_path)
    result = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        background=True,
    )
    assert result["state"] == "running"
    assert result["job_id"]
    # A background command counts as a workspace mutation on completion, not
    # on start; the running job is what the agent must wait for.
    instance._jobs.shutdown_jobs()


def test_output_is_truncated_and_flagged(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._max_output_bytes = 50
    result = run(instance, command=[sys.executable, "-c", "print('x' * 500)"])
    assert result["truncated"] is True
    assert len(result["output"].encode()) <= 50


def test_presentations_name_only_arguments_the_tools_take(tmp_path: Path) -> None:
    presentations = build(tmp_path).get_tool_presentations()
    assert presentations["exec_command"] == "exec_command({command})"


WAIT_CEILING = 30


def test_wait_job_returns_output_since_the_last_call(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "print('first')"],
        background=True,
    )
    first = wait(instance, started["job_id"], 10)
    assert first["state"] == "exited"
    assert "first" in first["output"]
    again = wait(instance, started["job_id"], 1)
    assert again["output"] == ""
    assert again["new_output"] is False


def test_wait_job_on_a_running_job_returns_the_current_snapshot(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    snapshot = wait(instance, started["job_id"], 1)
    assert snapshot["state"] == "running"
    assert snapshot["exit_code"] is None
    stop(instance, started["job_id"])


def test_wait_job_clamps_its_window(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    import time as clock

    begun = clock.monotonic()
    wait(instance, started["job_id"], 600)
    assert clock.monotonic() - begun < WAIT_CEILING + 5
    stop(instance, started["job_id"])


def test_wait_job_on_an_unknown_job_is_an_error(tmp_path: Path) -> None:
    assert wait(build(tmp_path), "nope", 1)["error"]["code"] == "job_not_found"


def test_stop_job_kills_the_group_and_reports_it(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    stopped = stop(instance, started["job_id"])
    assert stopped["state"] == "killed"


def test_stop_job_on_an_unknown_job_is_an_error(tmp_path: Path) -> None:
    assert (
        stop(build(tmp_path), "nope")["error"]["code"] == "job_not_found"
    )


def test_write_stdin_feeds_the_process(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import sys; print(sys.stdin.readline().strip())"],
        background=True,
    )
    assert write(instance, started["job_id"], "hello\n")["written"] == 6
    assert "hello" in wait(instance, started["job_id"], 10)["output"]


def test_write_stdin_to_a_finished_job_is_an_error(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance, command=[sys.executable, "-c", "print(1)"], background=True
    )
    wait(instance, started["job_id"], 10)
    result = write(instance, started["job_id"], "late\n")
    assert result["error"]["code"] == "job_not_running"


def test_background_jobs_are_owned_by_the_agent(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._agent_id = "alpha::s1"
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    assert instance._jobs.get_job(started["job_id"]).owner == "alpha::s1"
    stop(instance, started["job_id"])


def test_presentations_cover_every_job_tool(tmp_path: Path) -> None:
    presentations = build(tmp_path).get_tool_presentations()
    assert presentations["wait_job"] == "wait_job({job_id})"
    assert presentations["stop_job"] == "stop_job({job_id})"
    # write_stdin is absent: its payload reads better as the bare name.
    assert "write_stdin" not in presentations


def wait(instance: ExecToolsPlugin, job_id: str, timeout: int) -> dict:
    return tools(instance)["wait_job"].invoke(
        {"job_id": job_id, "timeout_seconds": timeout}
    )


def stop(instance: ExecToolsPlugin, job_id: str) -> dict:
    return tools(instance)["stop_job"].invoke({"job_id": job_id})


def write(instance: ExecToolsPlugin, job_id: str, data: str) -> dict:
    return tools(instance)["write_stdin"].invoke({"job_id": job_id, "data": data})
