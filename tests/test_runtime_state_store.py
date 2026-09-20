"""Tests for runtime state persistence (schema v3) and history."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import sqlite3

import pytest

from langharness_plugin.registry import (
    PluginDescriptor,
    PluginInstanceRecord,
    validate_descriptor,
)
from langharness_plugin.state_store import (
    HistoryEntry,
    InMemoryPluginHistoryStore,
    InMemoryRuntimeStateStore,
    PersistedDescriptorRecord,
    PersistedPluginRegistration,
    PluginStateConflictError,
    RuntimeStateSchemaError,
    RuntimeStateSnapshot,
    SqlitePluginHistoryStore,
    SqliteRuntimeStateStore,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Test plugin. Implements test.spec. Use in tests only. "
    "No properties. Never uninstall while a test runs."
)


def descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="test-plugin",
        version="1.0.0",
        module="tests.test_module",
        factory="test-plugin-factory",
        specification="test.spec",
        description=DESCRIPTION,
    )


def instance_record() -> PluginInstanceRecord:
    return PluginInstanceRecord(
        "uuid-1",
        "test-plugin-factory",
        "tests.test_module",
        ScopeId("server"),
        {"plugin.model.name": "gpt"},
    )


def snapshot(version: int = 0) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        version,
        (
            {"id": "root", "parent_id": None, "name": "root"},
            {"id": "server", "parent_id": "root", "name": "Server"},
        ),
        (PersistedDescriptorRecord(descriptor(), "assembly"),),
        (instance_record(),),
        (
            PersistedPluginRegistration(
                "pkg",
                "contrib",
                "1.0.0",
                "uuid-1",
                "test-plugin-factory",
                "tests.test_module",
                ScopeId("server"),
                True,
                "installed",
            ),
        ),
    )


def test_in_memory_save_load_round_trip() -> None:
    store = InMemoryRuntimeStateStore()
    store.save(snapshot(), expected_version=0)
    loaded = store.load()
    assert loaded is not None
    assert loaded.descriptors[0].descriptor == descriptor()
    assert loaded.instances[0] == instance_record()
    assert loaded.registrations[0].instance == "uuid-1"
    validate_descriptor(loaded.descriptors[0].descriptor)


def test_cas_conflict_raises() -> None:
    store = InMemoryRuntimeStateStore()
    store.save(snapshot(), expected_version=0)
    with pytest.raises(PluginStateConflictError):
        store.save(snapshot(), expected_version=0)


def test_sqlite_round_trip_and_conflict(tmp_path) -> None:
    path = tmp_path / "runtime_state.sqlite3"
    store = SqliteRuntimeStateStore(path)
    version = store.save(snapshot(), expected_version=0)
    loaded = store.load()
    assert loaded is not None and loaded.version == version
    assert loaded.scopes[1]["id"] == "server"
    # A stale expected version loses the CAS race.
    with pytest.raises(PluginStateConflictError):
        store.save(snapshot(), expected_version=0)


def test_old_schema_file_raises_clear_error(tmp_path) -> None:
    path = tmp_path / "runtime_state.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE runtime_state (singleton_id INTEGER PRIMARY KEY, "
            "version INTEGER NOT NULL, snapshot_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO runtime_state VALUES (1, 4, ?)",
            ('{"schema": 2, "scopes": [], "plugins": []}',),
        )
    store = SqliteRuntimeStateStore(path)
    with pytest.raises(RuntimeStateSchemaError, match="Delete the file"):
        store.load()


def test_history_append_and_read(tmp_path) -> None:
    memory = InMemoryPluginHistoryStore()
    memory.append(
        HistoryEntry(
            "2026-09-19T00:00:00Z",
            "uninstall",
            factory="f",
            module="m",
            scope_id="server",
            instance="uuid-1",
            detail={"reason": "test"},
        )
    )
    entries = memory.entries()
    assert len(entries) == 1
    assert entries[0].action == "uninstall"
    assert entries[0].detail == {"reason": "test"}

    sqlite = SqlitePluginHistoryStore(tmp_path / "history.sqlite3")
    sqlite.append(
        HistoryEntry("2026-09-19T00:00:01Z", "create_instance", factory="f", instance="uuid-2")
    )
    assert len(sqlite.entries()) == 1
    assert sqlite.entries()[0].instance == "uuid-2"


def test_registration_key_round_trip() -> None:
    registration = PersistedPluginRegistration(
        "pkg",
        "contrib",
        "1.0.0",
        "uuid-1",
        "f",
        "m",
        ScopeId("server"),
        True,
        "installed",
        "my-key",
    )
    from langharness_plugin.state_store import (
        _decode_registration,
        _encode_registration,
    )

    assert _decode_registration(_encode_registration(registration)) == registration
    assert PersistedPluginRegistration(
        "pkg", "contrib", "1", "u", "f", "m", ROOT_SCOPE_ID, True, "installed"
    ).registration_key is None


def test_empty_store_load_returns_none(tmp_path) -> None:
    assert SqliteRuntimeStateStore(tmp_path / "empty.sqlite3").load() is None
    assert InMemoryRuntimeStateStore().load() is None
