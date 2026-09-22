
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
