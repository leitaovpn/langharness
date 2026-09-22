
"""Runs the project's configured gates and signs a verification report.

The host owns this, not the model: a gate the model could skip or edit is not
a gate. Commands are argv arrays executed without a shell.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)

from langharness_core.common.text import digest
from langharness_core.contracts import (
    MutationRecord,
    VerificationArtifact,
    VerificationCheck,
    VerificationProvider,
    VerificationReport,
    WorkspaceProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.exec.runner import run_command
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

DEFAULT_TIMEOUT_SECONDS = 900
MAX_OUTPUT_BYTES = 65_536


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_set(records: list[MutationRecord]) -> tuple[VerificationArtifact, ...]:
    """The newest surviving content hash per path.

    A path written and then deleted has no ``after_sha256``; it is not an
    artifact, it is the absence of one.
    """
    newest: dict[str, str] = {}
    for record in records:
        if record.after_sha256:
            newest[record.path] = record.after_sha256
        else:
            newest.pop(record.path, None)
    return tuple(
        VerificationArtifact(path=path, sha256=newest[path])
        for path in sorted(newest)
    )


@ComponentFactory("project-verifier-plugin-factory")
@Provides(VerificationProvider)
@Property("_plugin_name", "plugin.name", "project-verifier")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_commands", "plugin.verify.commands", None)
@Property("_timeout_seconds", "plugin.verify.timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_workspace", "_state")
class ProjectVerifier:
    """Executes the configured gate commands and reports what it observed."""

    def __init__(self) -> None:
        self._plugin_name = "project-verifier"
        self._plugin_version = "1.0.0"
        self._commands: Any = None
        self._timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self._workspace: Any = None
        self._state: Any = None
        self._last_output = ""
        self._guards: dict[str, ContractGuard] = {
            "_workspace": ContractGuard(self, "_workspace", WorkspaceProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

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

    def verify(self, goal_revision: int) -> VerificationReport:
        """Run every configured gate and summarise the outcome.

        The generation is snapshotted before anything runs: build artifacts the
        gate itself writes are not agent mutations, and counting them would
        invalidate this report the moment it is signed.
        """
        started_at = _now()
        generation = self._state.get_generation() if self._state else 0
        records = self._state.mutations() if self._state else []
        checks = tuple(self._run(command) for command in self._configured())
        return VerificationReport(
            id=f"vr_{uuid4().hex}",
            goal_revision=goal_revision,
            mutation_generation=generation,
            started_at=started_at,
            finished_at=_now(),
            checks=checks,
            artifacts=_artifact_set(list(records)),
            passed=all(check.exit_code == 0 for check in checks),
        )

    def _configured(self) -> list[list[str]]:
        commands = self._commands or []
        return [[str(part) for part in item] for item in commands if item]

    def _run(self, command: list[str]) -> VerificationCheck:
        """Run one gate through the shared executor.

        ``env`` is deliberately left unset so the gate inherits the host
        environment: these are the project's own commands and restricting them
        to the agent's allowlist would break real gates (``make``, compilers,
        CI variables) for no security gain — the host configured them.
        """
        outcome = run_command(
            command,
            cwd=self._workspace.get_workspace() if self._workspace else ".",
            timeout_seconds=float(self._timeout_seconds),
        )
        self._last_output = outcome.output[:MAX_OUTPUT_BYTES]
        return VerificationCheck(
            name=" ".join(command),
            command=tuple(command),
            exit_code=outcome.exit_code,
            stdout_digest=digest(outcome.output),
            duration_ms=outcome.duration_ms,
        )
