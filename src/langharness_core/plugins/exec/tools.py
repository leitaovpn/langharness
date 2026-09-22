
"""The commands the code agent may run, and how they are bounded."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    JobManagerProvider,
    ToolProvider,
    WorkspaceProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.exec.runner import (
    DEFAULT_ENV_ALLOWLIST,
    build_environment,
    run_command,
)
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
    to_tool_error,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_OUTPUT_BYTES = 65_536


WAIT_CEILING_SECONDS = 30


class JobIdArgs(BaseModel):
    job_id: str = Field(min_length=1, description="Job id from exec_command.")


class WaitJobArgs(JobIdArgs):
    # No upper bound here: the tool clamps to WAIT_CEILING_SECONDS itself, the
    # same way exec_command clamps its timeout. Rejecting the call would cost
    # the model a round trip to learn a number it does not care about.
    timeout_seconds: int = Field(
        default=WAIT_CEILING_SECONDS,
        ge=1,
        description="How long to wait before returning the current snapshot.",
    )


class WriteStdinArgs(JobIdArgs):
    data: str = Field(description="Text to send to the process's standard input.")


class ExecCommandArgs(BaseModel):
    command: list[str] = Field(
        min_length=1,
        description=(
            "argv array, run without a shell. Use ['bash', '-lc', '...'] when "
            "you actually need shell semantics."
        ),
    )
    cwd: str = Field(
        default=".", description="Workspace-relative working directory."
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS, ge=1, description="Wall-clock limit."
    )
    background: bool = Field(
        default=False,
        description="Start it as a job and return immediately with a job id.",
    )



@ComponentFactory("exec-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "exec-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property(
    "_default_timeout_seconds",
    "plugin.exec.default_timeout_seconds",
    DEFAULT_TIMEOUT_SECONDS,
)
@Property(
    "_max_timeout_seconds", "plugin.exec.max_timeout_seconds", MAX_TIMEOUT_SECONDS
)
@Property("_max_output_bytes", "plugin.exec.max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)
@RequiresBest("_jobs", JobManagerProvider, optional=True, immediate_rebind=True)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_jobs", "_workspace", "_state")
class ExecToolsPlugin:
    """Runs commands inside the workspace, in the foreground or as a job."""

    def __init__(self) -> None:
        self._plugin_name = "exec-tools"
        self._plugin_version = "1.0.0"
        self._default_timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self._max_timeout_seconds = MAX_TIMEOUT_SECONDS
        self._max_output_bytes = DEFAULT_MAX_OUTPUT_BYTES
        self._jobs: Any = None
        self._workspace: Any = None
        self._state: Any = None
        self._agent_id = ""
        self._offsets: dict[str, int] = {}
        self._guards: dict[str, ContractGuard] = {
            "_jobs": ContractGuard(self, "_jobs", JobManagerProvider),
            "_workspace": ContractGuard(self, "_workspace", WorkspaceProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

    @BindField("_jobs", if_valid=True)
    def _on_jobs_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_jobs")
    def _on_jobs_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_workspace", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_workspace")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_state", if_valid=True)
    def _on_state_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_state")
    def _on_state_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._workspace is None or self._jobs is None:
            return []
        return [
            self._exec_tool(),
            self._wait_tool(),
            self._stdin_tool(),
            self._stop_tool(),
        ]

    def get_tool_presentations(self) -> dict[str, str]:
        return {
            "exec_command": "exec_command({command})",
            "wait_job": "wait_job({job_id})",
            "stop_job": "stop_job({job_id})",
        }

    def _resolve_cwd(self, raw: str) -> Any:
        return resolve_workspace_path(self._workspace.get_workspace(), raw or ".")

    def _note_mutation(self, cwd_relative: str, tool: str) -> None:
        """Every command is treated as a possible write (design 6.4).

        Whether a command wrote is not something the model gets to declare, so
        the generation advances either way and any earlier verification report
        stops being fresh.
        """
        if self._state is not None:
            self._state.record_mutation(cwd_relative, tool)

    def _exec_tool(self) -> StructuredTool:
        plugin = self

        def exec_command(
            command: list[str],
            cwd: str = ".",
            timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
            background: bool = False,
        ) -> dict[str, Any]:
            try:
                target = plugin._resolve_cwd(cwd)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            argv = [str(part) for part in command]
            if background:
                try:
                    record = plugin._jobs.start_job(
                        argv,
                        cwd=str(target.absolute),
                        owner=plugin._agent_id or "local",
                    )
                except (RuntimeError, OSError) as exc:
                    return tool_error("internal_error", str(exc), retryable=True)
                return {
                    "job_id": record.id,
                    "state": record.state,
                    "command": list(record.command),
                    "cwd": target.relative,
                }
            limit = min(int(timeout_seconds), int(plugin._max_timeout_seconds))
            outcome = run_command(
                argv,
                cwd=target.absolute,
                timeout_seconds=float(limit),
                max_output_bytes=int(plugin._max_output_bytes),
                env=build_environment(DEFAULT_ENV_ALLOWLIST),
            )
            plugin._note_mutation(target.relative, "exec_command")
            if outcome.timed_out:
                return tool_error(
                    "timeout",
                    f"command exceeded its {limit}s deadline and was killed",
                    retryable=True,
                    limit_seconds=limit,
                    command=argv,
                )
            return {
                "command": list(outcome.command),
                "cwd": target.relative,
                "exit_code": outcome.exit_code,
                "output": outcome.output,
                "truncated": outcome.truncated,
                "duration_ms": outcome.duration_ms,
            }

        return StructuredTool.from_function(
            func=exec_command,
            name="exec_command",
            description=(
                "Run a command inside the workspace and return its output and "
                "exit code. `command` is an argv array executed WITHOUT a "
                "shell, so ['bash', '-lc', '...'] is how you ask for shell "
                "semantics explicitly. The working directory must stay inside "
                "the workspace. Output is capped and the cap is reported via "
                "'truncated'. The command inherits a filtered environment: "
                "API keys and other secrets are not passed to it, so a command "
                "that needs one will not find it. Every command counts as a "
                "possible workspace change, so any verification you ran before "
                "it is no longer fresh — re-run verification before claiming "
                "completion. Set background=true for a long-running command; "
                "you then get a job id back immediately and drive it with "
                "wait_job / write_stdin / stop_job. Returns {\"command\": "
                "[str], \"cwd\": str, \"exit_code\": int, \"output\": str, "
                "\"truncated\": bool, \"duration_ms\": int}, or {\"job_id\": "
                "str, \"state\": str, \"command\": [str], \"cwd\": str} for a "
                "background job, or {\"error\": {code, message, retryable, "
                "details}}."
            ),
            args_schema=ExecCommandArgs,
        )

    def _wait_tool(self) -> StructuredTool:
        plugin = self

        def wait_job(
            job_id: str, timeout_seconds: int = WAIT_CEILING_SECONDS
        ) -> dict[str, Any]:
            try:
                record = plugin._jobs.wait_job(
                    job_id, min(int(timeout_seconds), WAIT_CEILING_SECONDS)
                )
                text, offset = plugin._jobs.read_output(
                    job_id, plugin._offsets.get(job_id, 0)
                )
            except KeyError:
                return tool_error("job_not_found", f"unknown job {job_id!r}")
            plugin._offsets[job_id] = offset
            if record.state != "running":
                plugin._note_mutation(".", "wait_job")
            return {
                "job_id": job_id,
                "state": record.state,
                "exit_code": record.exit_code,
                "output": text,
                "truncated": False,
                "new_output": bool(text),
            }

        return StructuredTool.from_function(
            func=wait_job,
            name="wait_job",
            description=(
                "Wait up to timeout_seconds for a background job, then return "
                "its current snapshot. 'output' is only what arrived SINCE "
                "YOUR LAST CALL to this tool, so polling does not re-flood the "
                "conversation. If 'state' is still 'running' the job is not "
                "finished; decide whether to keep waiting or do something else "
                "rather than spinning here. Returns {\"job_id\": str, "
                "\"state\": 'running'|'exited'|'killed'|'timed_out', "
                "\"exit_code\": int|null, \"output\": str, \"truncated\": bool, "
                "\"new_output\": bool}, or {\"error\": {code, message, "
                "retryable, details}}."
            ),
            args_schema=WaitJobArgs,
        )

    def _stdin_tool(self) -> StructuredTool:
        plugin = self

        def write_stdin(job_id: str, data: str) -> dict[str, Any]:
            try:
                record = plugin._jobs.write_stdin(job_id, data)
            except KeyError:
                return tool_error("job_not_found", f"unknown job {job_id!r}")
            except RuntimeError as exc:
                return tool_error("job_not_running", str(exc), retryable=True)
            return {"job_id": record.id, "state": record.state, "written": len(data)}

        return StructuredTool.from_function(
            func=write_stdin,
            name="write_stdin",
            description=(
                "Send text to a background job's standard input, for commands "
                "that prompt or read a stream. Returns {\"job_id\": str, "
                "\"state\": str, \"written\": int}, or {\"error\": {code, "
                "message, retryable, details}} when the job is unknown or has "
                "already stopped taking input."
            ),
            args_schema=WriteStdinArgs,
        )

    def _stop_tool(self) -> StructuredTool:
        plugin = self

        def stop_job(job_id: str) -> dict[str, Any]:
            try:
                record = plugin._jobs.stop_job(job_id)
            except KeyError:
                return tool_error("job_not_found", f"unknown job {job_id!r}")
            plugin._note_mutation(".", "stop_job")
            return {
                "job_id": record.id,
                "state": record.state,
                "exit_code": record.exit_code,
            }

        return StructuredTool.from_function(
            func=stop_job,
            name="stop_job",
            description=(
                "Stop a background job and its whole process group, including "
                "any children it spawned. Use it when a command is stuck or no "
                "longer needed; the process does not survive this call. "
                "Returns {\"job_id\": str, \"state\": 'killed', \"exit_code\": "
                "int|null}, or {\"error\": {code, message, retryable, "
                "details}}."
            ),
            args_schema=JobIdArgs,
        )

