"""Persistent runtime state (schema v3) and append-only lifecycle history."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from langharness_plugin.registry import PluginDescriptor, PluginInstanceRecord
from langharness_scope import ScopeId

RUNTIME_SCHEMA = 3

PluginStatus = Literal[
    "installed", "disabled", "upgrade_available", "missing", "failed"
]
DescriptorSource = Literal["discovered", "assembly"]


class PluginStateConflictError(ValueError):
    pass


class RuntimeStateSchemaError(ValueError):
    """The persisted state file uses an unsupported schema.

    This development build does not migrate legacy data: delete the file and
    restart to start from a clean state.
    """


@dataclass(frozen=True, slots=True)
class PersistedDescriptorRecord:
    descriptor: PluginDescriptor
    source: DescriptorSource


@dataclass(frozen=True, slots=True)
class PersistedPluginRegistration:
    """Package provenance of one dynamically installed instance."""

    package_id: str
    contribution_id: str
    package_version: str
    instance: str  # instance UUID created for this contribution
    factory: str
    module: str
    scope_id: ScopeId
    enabled: bool
    status: PluginStatus
    registration_key: str | None = None  # caller-supplied idempotency key


@dataclass(frozen=True, slots=True)
class RuntimeStateSnapshot:
    version: int
    scopes: tuple[dict[str, str | None], ...]
    descriptors: tuple[PersistedDescriptorRecord, ...]
    instances: tuple[PluginInstanceRecord, ...]
    registrations: tuple[PersistedPluginRegistration, ...]


class RuntimeStateStore(Protocol):
    def load(self) -> RuntimeStateSnapshot | None: ...

    def save(
        self, snapshot: RuntimeStateSnapshot, *, expected_version: int
    ) -> int: ...


def _encode_snapshot(snapshot: RuntimeStateSnapshot) -> str:
    return json.dumps(
        {
            "schema": RUNTIME_SCHEMA,
            "scopes": list(snapshot.scopes),
            "descriptors": [
                {
                    "descriptor": record.descriptor.to_dict(),
                    "source": record.source,
                }
                for record in snapshot.descriptors
            ],
            "instances": [item.to_dict() for item in snapshot.instances],
            "registrations": [
                _encode_registration(item) for item in snapshot.registrations
            ],
        }
    )


def _decode_snapshot(
    raw: dict[str, Any],
) -> tuple[
    tuple[dict[str, str | None], ...],
    tuple[PersistedDescriptorRecord, ...],
    tuple[PluginInstanceRecord, ...],
    tuple[PersistedPluginRegistration, ...],
]:
    return (
        tuple(raw["scopes"]),
        tuple(
            PersistedDescriptorRecord(
                PluginDescriptor.from_dict(item["descriptor"]),
                str(item["source"]),  # type: ignore[arg-type]
            )
            for item in raw["descriptors"]
        ),
        tuple(PluginInstanceRecord.from_dict(item) for item in raw["instances"]),
        tuple(_decode_registration(item) for item in raw["registrations"]),
    )


def _encode_registration(item: PersistedPluginRegistration) -> dict[str, Any]:
    return {
        "package_id": item.package_id,
        "contribution_id": item.contribution_id,
        "package_version": item.package_version,
        "instance": item.instance,
        "factory": item.factory,
        "module": item.module,
        "scope_id": str(item.scope_id),
        "enabled": item.enabled,
        "status": item.status,
        "registration_key": item.registration_key,
    }


def _decode_registration(data: dict[str, Any]) -> PersistedPluginRegistration:
    return PersistedPluginRegistration(
        package_id=str(data["package_id"]),
        contribution_id=str(data["contribution_id"]),
        package_version=str(data["package_version"]),
        instance=str(data["instance"]),
        factory=str(data["factory"]),
        module=str(data["module"]),
        scope_id=ScopeId(str(data["scope_id"])),
        enabled=bool(data["enabled"]),
        status=str(data["status"]),  # type: ignore[arg-type]
        registration_key=(
            str(data["registration_key"])
            if data.get("registration_key") is not None
            else None
        ),
    )


class InMemoryRuntimeStateStore:
    def __init__(self) -> None:
        self.snapshot: RuntimeStateSnapshot | None = None

    def load(self) -> RuntimeStateSnapshot | None:
        return self.snapshot

    def save(self, snapshot: RuntimeStateSnapshot, *, expected_version: int) -> int:
        current = self.snapshot.version if self.snapshot is not None else 0
        if current != expected_version:
            raise PluginStateConflictError(
                f"Runtime state version changed: expected {expected_version}, got {current}"
            )
        version = current + 1
        self.snapshot = replace(snapshot, version=version)
        return version


class SqliteRuntimeStateStore:
    """Atomically persists scopes, definitions, instances, and registrations."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runtime_state ("
                "singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1), "
                "version INTEGER NOT NULL, snapshot_json TEXT NOT NULL)"
            )

    def load(self) -> RuntimeStateSnapshot | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT version, snapshot_json FROM runtime_state WHERE singleton_id = 1"
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(row[1])
        if raw.get("schema") != RUNTIME_SCHEMA:
            raise RuntimeStateSchemaError(
                f"Runtime state file {self.path} uses schema "
                f"{raw.get('schema')!r}; this build only supports schema "
                f"{RUNTIME_SCHEMA} and does not migrate legacy data. "
                f"Delete the file to reset."
            )
        scopes, descriptors, instances, registrations = _decode_snapshot(raw)
        return RuntimeStateSnapshot(
            int(row[0]), scopes, descriptors, instances, registrations
        )

    def save(self, snapshot: RuntimeStateSnapshot, *, expected_version: int) -> int:
        payload = _encode_snapshot(snapshot)
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT version FROM runtime_state WHERE singleton_id = 1"
            ).fetchone()
            current = int(row[0]) if row is not None else 0
            if current != expected_version:
                raise PluginStateConflictError(
                    f"Runtime state version changed: expected {expected_version}, got {current}"
                )
            version = current + 1
            connection.execute(
                "INSERT INTO runtime_state VALUES (1, ?, ?) "
                "ON CONFLICT(singleton_id) DO UPDATE SET "
                "version=excluded.version, snapshot_json=excluded.snapshot_json",
                (version, payload),
            )
        return version


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    ts: str
    action: str
    factory: str | None = None
    module: str | None = None
    scope_id: str | None = None
    instance: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class PluginHistoryStore(Protocol):
    def append(self, entry: HistoryEntry) -> None: ...

    def entries(self) -> tuple[HistoryEntry, ...]: ...


class InMemoryPluginHistoryStore:
    def __init__(self) -> None:
        self._entries: list[HistoryEntry] = []

    def append(self, entry: HistoryEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)


class SqlitePluginHistoryStore:
    """Append-only lifecycle history inside the runtime state database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS plugin_history ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ts TEXT NOT NULL, action TEXT NOT NULL, "
                "factory TEXT, module TEXT, scope_id TEXT, instance TEXT, "
                "detail TEXT NOT NULL)"
            )

    def append(self, entry: HistoryEntry) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO plugin_history "
                "(ts, action, factory, module, scope_id, instance, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.ts,
                    entry.action,
                    entry.factory,
                    entry.module,
                    entry.scope_id,
                    entry.instance,
                    json.dumps(entry.detail),
                ),
            )

    def entries(self) -> tuple[HistoryEntry, ...]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT ts, action, factory, module, scope_id, instance, detail "
                "FROM plugin_history ORDER BY seq"
            ).fetchall()
        return tuple(
            HistoryEntry(
                str(row[0]),
                str(row[1]),
                str(row[2]) if row[2] else None,
                str(row[3]) if row[3] else None,
                str(row[4]) if row[4] else None,
                str(row[5]) if row[5] else None,
                json.loads(row[6]),
            )
            for row in rows
        )
