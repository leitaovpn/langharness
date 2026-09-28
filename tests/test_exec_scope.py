
"""Two agents must not see or kill each other's jobs."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from langharness_core.contracts import SPEC_JOB_MANAGER, SPEC_TOOL, SPEC_WORKSPACE
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id

DESCRIPTION = (
    "Exec scope test plugin. Implements a test specification. Properties vary "
    "per instance. Requires a restart for property changes. Uninstall when "
    "tests finish."
)


def descriptor(
    name: str, module: str, factory: str, specification: str
) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=DESCRIPTION,
    )


BUNDLES = (
    descriptor(
        "job-manager",
        "langharness_core.plugins.exec.jobs",
        "job-manager-plugin-factory",
        SPEC_JOB_MANAGER,
    ),
    descriptor(
        "exec-tools",
        "langharness_core.plugins.exec.tools",
        "exec-tools-plugin-factory",
        SPEC_TOOL,
    ),
    descriptor(
        "workspace",
        "langharness_core.plugins.workspace.root",
        "workspace-root-plugin-factory",
        SPEC_WORKSPACE,
    ),
)


def start_agent(manager: PluginManager, agent_id: str, workspace: Path) -> None:
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    manager.create_instance(
        "job-manager-plugin-factory",
        "langharness_core.plugins.exec.jobs",
        scope,
    )
    manager.create_instance(
        "exec-tools-plugin-factory",
        "langharness_core.plugins.exec.tools",
        scope,
    )
    manager.create_instance(
        "workspace-root-plugin-factory",
        "langharness_core.plugins.workspace.root",
        scope,
        properties={"plugin.workspace.root_dir": str(workspace)},
    )


def test_job_managers_are_per_agent(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        for name in ("alpha", "beta"):
            workspace = tmp_path / name
            workspace.mkdir()
            start_agent(manager, name, workspace)

        alpha: list[Any] = manager.find_services(
            SPEC_JOB_MANAGER, filter="(plugin.scope_id=agent:alpha)"
        )
        beta: list[Any] = manager.find_services(
            SPEC_JOB_MANAGER, filter="(plugin.scope_id=agent:beta)"
        )
        assert len(alpha) == 1
        assert len(beta) == 1

        job = alpha[0].start_job(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            cwd=str(tmp_path / "alpha"),
            owner="alpha::s1",
        )
        assert len(alpha[0].list_jobs()) == 1
        assert beta[0].list_jobs() == []
        # beta cannot reach alpha's job, let alone stop it.
        assert beta[0].get_job(job.id) is None
        alpha[0].stop_job(job.id)
    finally:
        manager.stop()


def test_exec_tools_bind_their_own_agents_services(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        workspace = tmp_path / "alpha"
        workspace.mkdir()
        start_agent(manager, "alpha", workspace)
        providers: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:alpha)"
        )
        assert len(providers) == 1
        jobs = manager.find_service(
            SPEC_JOB_MANAGER, filter="(plugin.scope_id=agent:alpha)"
        )
        assert providers[0]._jobs is jobs
        names = {item.name for item in providers[0].get_tools()}
        assert names == {"exec_command", "write_stdin", "wait_job", "stop_job"}
    finally:
        manager.stop()
