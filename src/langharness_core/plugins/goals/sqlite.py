
"""Durable goal state with revision compare-and-swap.

Sync ``sqlite3`` on purpose: tools run in LangChain's thread pool, and a sync
store is the only shape a sync tool can call directly. The connection is
shared across those threads, so every access takes the lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pelix.ipopo.decorators import ComponentFactory, Invalidate, Property, Provides

from langharness_core.contracts import (
    AcceptanceCriterion,
    Goal,
    GoalProvider,
)

BLOCKER_THRESHOLD = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    objective TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    status TEXT NOT NULL,
    rounds_started INTEGER NOT NULL DEFAULT 0,
    max_rounds INTEGER NOT NULL DEFAULT 32,
    mutation_generation INTEGER NOT NULL DEFAULT 0,
    blocker_history TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Not on rowid: SQLite refuses to index the rowid pseudocolumn, and the
-- newest-goal lookup orders by it rather than filtering on it.
CREATE INDEX IF NOT EXISTS goals_status ON goals (status);
"""


class GoalConflictError(RuntimeError):
    """Raised when a goal update loses the revision race."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        revision=row["revision"],
        objective=row["objective"],
        acceptance_criteria=tuple(
            AcceptanceCriterion(
                id=item["id"], text=item["text"], evidence=item.get("evidence")
            )
            for item in json.loads(row["acceptance_criteria"])
        ),
        status=row["status"],
        rounds_started=row["rounds_started"],
        max_rounds=row["max_rounds"],
        mutation_generation=row["mutation_generation"],
        blocker_history=tuple(json.loads(row["blocker_history"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@ComponentFactory("sqlite-goal-plugin-factory")
@Provides(GoalProvider)
@Property("_plugin_name", "plugin.name", "sqlite-goal")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_path", "plugin.goal.path", "langharness_goals.sqlite3")
@Property("_max_rounds", "plugin.goal.max_rounds", 32)
class SQLiteGoalPlugin:
    """Persists goals and enforces the revision CAS that guards completion."""

    def __init__(self) -> None:
        self._plugin_name = "sqlite-goal"
        self._plugin_version = "1.0.0"
        self._path = "langharness_goals.sqlite3"
        self._max_rounds = 32
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    def create_goal(
        self,
        objective: str,
        acceptance_criteria: list[dict[str, str]],
        *,
        max_rounds: int | None = None,
    ) -> Goal:
        goal_id = uuid4().hex
        resolved_rounds = int(self._max_rounds if max_rounds is None else max_rounds)
        timestamp = _now()
        criteria = [
            {
                "id": str(item.get("id") or f"c{index + 1}"),
                "text": str(item.get("text", "")),
                "evidence": item.get("evidence"),
            }
            for index, item in enumerate(acceptance_criteria or [])
        ]
        with self._lock:
            connection = self._connection_or_open()
            connection.execute(
                "INSERT INTO goals (id, revision, objective, acceptance_criteria,"
                " status, rounds_started, max_rounds, mutation_generation,"
                " blocker_history, created_at, updated_at)"
                " VALUES (?, 1, ?, ?, 'active', 0, ?, 0, '[]', ?, ?)",
                (
                    goal_id,
                    objective,
                    json.dumps(criteria),
                    resolved_rounds,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            row = self._require(connection, goal_id)
        return _row_to_goal(row)

    def get_goal(self, goal_id: str) -> Goal | None:
        with self._lock:
            connection = self._connection_or_open()
            row = self._find(connection, goal_id)
        return _row_to_goal(row) if row is not None else None

    def current_goal(self) -> Goal | None:
        """The newest goal that is not in a terminal state."""
        with self._lock:
            connection = self._connection_or_open()
            cursor = connection.execute(
                "SELECT * FROM goals WHERE status NOT IN"
                " ('complete', 'blocked', 'cancelled')"
                " ORDER BY rowid DESC LIMIT 1"
            )
            row = cursor.fetchone()
        return _row_to_goal(row) if row is not None else None

    def update_goal(
        self,
        goal_id: str,
        *,
        expected_revision: int,
        status: str | None = None,
        blocker: str | None = None,
        start_round: bool = False,
        mutation_generation: int | None = None,
    ) -> Goal:
        with self._lock:
            connection = self._connection_or_open()
            row = self._require(connection, goal_id)
            if row["revision"] != expected_revision:
                raise GoalConflictError(
                    f"goal {goal_id!r} is at revision {row['revision']}, "
                    f"not {expected_revision}"
                )
            history = list(json.loads(row["blocker_history"]))
            if blocker is not None:
                history.append(blocker)
            connection.execute(
                "UPDATE goals SET revision = revision + 1, status = ?,"
                " blocker_history = ?, rounds_started = rounds_started + ?,"
                " mutation_generation = ?, updated_at = ? WHERE id = ?",
                (
                    status or row["status"],
                    json.dumps(history),
                   1 if start_round else 0,
                    (
                        mutation_generation
                        if mutation_generation is not None
                        else row["mutation_generation"]
                    ),
                    _now(),
                    goal_id,
                ),
            )
            connection.commit()
            updated = self._require(connection, goal_id)
        return _row_to_goal(updated)

    def set_criterion_evidence(
        self, goal_id: str, evidence: dict[str, str]
    ) -> Goal:
        """Record what proved each criterion, without revising the goal.

        Deliberately not a revision bump: this runs *after* a completion has
        been granted, and moving the revision would invalidate the very report
        that earned it.
        """
        with self._lock:
            connection = self._connection_or_open()
            row = self._require(connection, goal_id)
            criteria = json.loads(row["acceptance_criteria"])
            for item in criteria:
                replacement = evidence.get(item["id"])
                if replacement:
                    item["evidence"] = replacement
            connection.execute(
                "UPDATE goals SET acceptance_criteria = ? WHERE id = ?",
                (json.dumps(criteria), goal_id),
            )
            connection.commit()
            updated = self._require(connection, goal_id)
        return _row_to_goal(updated)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    @staticmethod
    def _find(connection: sqlite3.Connection, goal_id: str) -> sqlite3.Row | None:
        cursor = connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        row: sqlite3.Row | None = cursor.fetchone()
        return row

    def _require(self, connection: sqlite3.Connection, goal_id: str) -> sqlite3.Row:
        """The row for an id the caller is about to write to.

        Splitting this from :meth:`_find` keeps the read path honest about
        returning ``None`` while the write paths get a value they can use
        without every one of them repeating a null check.
        """
        row = self._find(connection, goal_id)
        if row is None:
            raise GoalConflictError(f"unknown goal {goal_id!r}")
        return row

    def _connection_or_open(self) -> sqlite3.Connection:
        if self._connection is None:
            path = Path(self._path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            connection.commit()
            self._connection = connection
        return self._connection

    @Invalidate
    def _invalidate(self, bundle_context: Any) -> None:
        with self._lock:
            if self._connection is None:
                return
            self._connection.close()
            self._connection = None
