"""Acceptance tests for append-only audit persistence."""

from __future__ import annotations

import asyncio

from langharness_core.plugins.sessions.audit import SQLiteAuditPlugin


def test_audit_events_round_trip_and_preserve_sequence(tmp_path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    events = [
        {"type": "ptc_audit", "root_call_id": "root-1", "sequence": 1, "status": "started"},
        {"type": "ptc_audit", "root_call_id": "root-1", "sequence": 2, "status": "completed", "result": 3},
    ]

    async def scenario() -> list[dict]:
        await plugin.append_events(events)
        return await plugin.list_events("root-1")

    assert asyncio.run(scenario()) == events
