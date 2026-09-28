"""Workspace mutation generations: what a verification report is valid for."""

from __future__ import annotations

import threading

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import MutationRecord, WorkspaceStateProvider


@ComponentFactory("workspace-state-plugin-factory")
@Provides(WorkspaceStateProvider)
@Property("_plugin_name", "plugin.name", "workspace-state")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_root", "plugin.workspace.root_dir", ".")
class WorkspaceStatePlugin:
    """Counts workspace writes so stale verification can be detected.

    Tools run in LangChain's thread pool, so the counter is guarded by a
    threading lock rather than an asyncio one: two concurrent writes must not
    collapse into a single generation.
    """

    def __init__(self) -> None:
        self._plugin_name = "workspace-state"
        self._plugin_version = "1.0.0"
        self._root = "."
        self._lock = threading.Lock()
        self._generation = 0
        self._records: list[MutationRecord] = []

    def get_generation(self) -> int:
        with self._lock:
            return self._generation

    def record_mutation(
        self,
        path: str,
        tool: str,
        *,
        before_sha256: str | None = None,
        after_sha256: str | None = None,
    ) -> int:
        with self._lock:
            self._generation += 1
            self._records.append(
                MutationRecord(
                    path=path,
                    generation=self._generation,
                    tool=tool,
                    before_sha256=before_sha256,
                    after_sha256=after_sha256,
                )
            )
            return self._generation

    def mutations(self, since: int = 0) -> list[MutationRecord]:
        with self._lock:
            return [item for item in self._records if item.generation > since]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
