"""Append-only SQLite storage for agent audit events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
from pelix.ipopo.decorators import ComponentFactory, Invalidate, Property, Provides

from langharness_core.contracts import AuditProvider

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_call_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS audit_events_root ON audit_events(root_call_id, sequence, id);
"""


@ComponentFactory("audit-plugin-factory")
@Provides(AuditProvider)
@Property("_plugin_name", "plugin.name", "sqlite-audit")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_path", "plugin.audit.path", str(Path.home() / ".langharness" / "audit.sqlite3"))
class SQLiteAuditPlugin:
    """Stores immutable JSON event snapshots and supports bounded reads."""

    def __init__(self) -> None:
        self._plugin_name = "sqlite-audit"
        self._plugin_version = "1.0.0"
        self._path = str(Path.home() / ".langharness" / "audit.sqlite3")
        self._connection: aiosqlite.Connection | None = None

    async def append_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        connection = await self._ensure_connection()
        await connection.executemany(
            "INSERT INTO audit_events(root_call_id, agent_id, session_id, sequence, event_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    str(event.get("root_call_id", "")),
                    str(event.get("agent_id", "")),
                    str(event.get("session_id", "")),
                    int(event.get("sequence", 0)),
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                )
                for event in events
            ],
        )
        await connection.commit()

    async def list_events(self, root_call_id: str, limit: int = 100) -> list[dict[str, Any]]:
        connection = await self._ensure_connection()
        async with connection.execute(
            "SELECT event_json FROM audit_events WHERE root_call_id = ? "
            "ORDER BY sequence, id LIMIT ?",
            (root_call_id, max(0, min(limit, 1000))),
        ) as cursor:
            rows = await cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    async def list_session_events(
        self, agent_id: str, session_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        connection = await self._ensure_connection()
        async with connection.execute(
            "SELECT event_json FROM audit_events WHERE agent_id = ? AND session_id = ? "
            "ORDER BY id LIMIT ?",
            (agent_id, session_id, max(0, min(limit, 1000))),
        ) as cursor:
            rows = await cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    async def find_pending_approval(
        self, agent_id: str, session_id: str
    ) -> dict[str, Any] | None:
        connection = await self._ensure_connection()
        async with connection.execute(
            "SELECT request.event_json FROM audit_events AS request "
            "WHERE request.agent_id = ? AND request.session_id = ? "
            "AND json_extract(request.event_json, '$.type') = 'approval_audit' "
            "AND json_extract(request.event_json, '$.status') = 'required' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM audit_events AS decision "
            "WHERE decision.root_call_id = request.root_call_id "
            "AND decision.agent_id = request.agent_id "
            "AND decision.session_id = request.session_id "
            "AND json_extract(decision.event_json, '$.type') = 'approval_audit' "
            "AND json_extract(decision.event_json, '$.status') = 'decision_submitted'"
            ") ORDER BY request.id DESC LIMIT 1",
            (agent_id, session_id),
        ) as cursor:
            row = await cursor.fetchone()
        return json.loads(row[0]) if row is not None else None

    async def _ensure_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            path = Path(self._path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(path)
            await self._connection.execute("PRAGMA journal_mode=WAL")
            await self._connection.executescript(_SCHEMA)
            async with self._connection.execute("PRAGMA table_info(audit_events)") as cursor:
                columns = {str(row[1]) for row in await cursor.fetchall()}
            for column in ("agent_id", "session_id"):
                if column not in columns:
                    await self._connection.execute(
                        f"ALTER TABLE audit_events ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            await self._connection.execute(
                "CREATE INDEX IF NOT EXISTS audit_events_session "
                "ON audit_events(agent_id, session_id, id)"
            )
            await self._connection.commit()
        return self._connection

    @Invalidate
    def _invalidate(self, bundle_context: Any) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(connection.close())
        else:
            loop.create_task(connection.close())

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
