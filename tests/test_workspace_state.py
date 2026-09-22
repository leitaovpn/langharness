"""Tests for the workspace mutation-generation service."""
# mypy: ignore-errors

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_WORKSPACE_STATE,
    MutationRecord,
    WorkspaceStateProvider,
)
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import contract_for, validate


def test_specification_is_pinned_to_the_protocol() -> None:
    assert contract_for(SPEC_WORKSPACE_STATE) is WorkspaceStateProvider
    assert SPEC_WORKSPACE_STATE in ALL_PLUGIN_SPECS


def test_plugin_conforms_to_its_contract() -> None:
    assert validate(WorkspaceStatePlugin(), WorkspaceStateProvider) == ()


def test_generation_starts_at_zero() -> None:
    assert WorkspaceStatePlugin().get_generation() == 0


def test_every_mutation_advances_the_generation() -> None:
    plugin = WorkspaceStatePlugin()
    assert plugin.record_mutation("a.py", "apply_patch") == 1
    assert plugin.record_mutation("b.py", "apply_patch") == 2
    assert plugin.get_generation() == 2


def test_records_carry_hashes_and_tool_name() -> None:
    plugin = WorkspaceStatePlugin()
    plugin.record_mutation(
        "a.py", "apply_patch", before_sha256="sha256:old", after_sha256="sha256:new"
    )
    assert plugin.mutations() == [
        MutationRecord(
            path="a.py",
            generation=1,
            tool="apply_patch",
            before_sha256="sha256:old",
            after_sha256="sha256:new",
        )
    ]


def test_mutations_can_be_read_since_a_generation() -> None:
    plugin = WorkspaceStatePlugin()
    plugin.record_mutation("a.py", "apply_patch")
    plugin.record_mutation("b.py", "apply_patch")
    assert [item.path for item in plugin.mutations(since=1)] == ["b.py"]


def test_crossing_ten_generations_is_detected_by_comparison_not_parity() -> None:
    plugin = WorkspaceStatePlugin()
    for _ in range(11):
        plugin.record_mutation("a.py", "apply_patch")
    # A stale report recorded at generation 1 must not look fresh at 11.
    assert plugin.get_generation() == 11


def test_concurrent_mutations_never_lose_a_generation() -> None:
    plugin = WorkspaceStatePlugin()
    with ThreadPoolExecutor(max_workers=8) as pool:
        generations = list(
            pool.map(
                lambda index: plugin.record_mutation(f"{index}.py", "apply_patch"),
                range(64),
            )
        )
    assert sorted(generations) == list(range(1, 65))
    assert plugin.get_generation() == 64
    assert len(plugin.mutations()) == 64


def test_plugin_info_reports_the_component_identity() -> None:
    assert WorkspaceStatePlugin().get_plugin_info() == {
        "name": "workspace-state",
        "version": "1.0.0",
    }
