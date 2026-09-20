"""Tests for the SQLite-backed scope snapshot store."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_scope.errors import ScopeConflictError
from langharness_scope.model import ROOT_SCOPE_ID, Scope, ScopeId, ScopeSnapshot
from langharness_scope.sqlite import SqliteScopeStore


def snapshot(*, version: int = 0) -> ScopeSnapshot:
    return ScopeSnapshot(
        version,
        (
            Scope(ROOT_SCOPE_ID, None, "root"),
            Scope(ScopeId("server"), ROOT_SCOPE_ID, "Server"),
        ),
    )


def test_empty_store_loads_none(tmp_path) -> None:
    store = SqliteScopeStore(tmp_path / "scope.sqlite3")
    assert store.load() is None


def test_save_load_round_trip(tmp_path) -> None:
    store = SqliteScopeStore(tmp_path / "scope.sqlite3")
    version = store.save(snapshot(), expected_version=0)
    loaded = store.load()
    assert loaded is not None
    assert loaded.version == version
    assert loaded.scopes == snapshot().scopes


def test_save_uses_cas_and_rejects_stale_versions(tmp_path) -> None:
    store = SqliteScopeStore(tmp_path / "scope.sqlite3")
    store.save(snapshot(), expected_version=0)
    with pytest.raises(ScopeConflictError, match="expected 0"):
        store.save(snapshot(), expected_version=0)
    assert store.save(snapshot(), expected_version=1) == 2


def test_scope_tree_uses_sqlite_store_end_to_end(tmp_path) -> None:
    from langharness_scope.tree import ScopeTree

    store = SqliteScopeStore(tmp_path / "scope.sqlite3")
    tree = ScopeTree(store)
    tree.create(ScopeId("server"), "Server")
    reloaded = ScopeTree(store)
    assert reloaded.get(ScopeId("server")) is not None
    assert reloaded.get(ScopeId("server")).parent_id == ROOT_SCOPE_ID
