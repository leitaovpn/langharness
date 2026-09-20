"""Versioned plugin configuration store.

Every write appends a full snapshot to an append-only history; rolling back
appends a new version pointing at an older snapshot, so the file always
describes what happened and never loses a version.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1
SECRET_KEY_SUFFIXES = ("api_key", "token", "secret", "password")
CONFIG_DIRNAME = "plugin_config"


def config_dir(directory: str) -> Path:
    return Path(directory).expanduser() / CONFIG_DIRNAME


def scope_path(directory: str, scope: str) -> Path:
    """Resolve the config file of one scope: cli, api, or agent:<id>."""
    if scope.startswith("agent:"):
        return config_dir(directory) / "agents" / f"{scope.split(':', 1)[1]}.json"
    return config_dir(directory) / f"{scope}.json"


def load_overrides(directory: str, scope: str) -> dict[str, Any]:
    """Stored overrides of one scope; empty when the scope has no file yet."""
    path = scope_path(directory, scope)
    if not path.exists():
        return {}
    return PluginConfigStore.load(path, scope).plugins()


def agent_scope_configs(directory: str) -> list[tuple[str, dict[str, Any]]]:
    """Every stored agent-scope configuration, as (agent_id, plugins) pairs."""
    agents_dir = config_dir(directory) / "agents"
    if not agents_dir.exists():
        return []
    configs: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(agents_dir.glob("*.json")):
        store = PluginConfigStore.load(path, f"agent:{path.stem}")
        configs.append((path.stem, store.plugins()))
    return configs


def _now() -> str:
    return datetime.now(UTC).isoformat()


def strip_secrets(properties: dict[str, Any]) -> dict[str, Any]:
    """Drop credential-like properties: plugin config files are not a safe store."""
    return {
        key: value
        for key, value in properties.items()
        if not key.rsplit(".", 1)[-1].lower().endswith(SECRET_KEY_SUFFIXES)
    }


def clean_config(plugins: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for name, entry in plugins.items():
        item = dict(entry)
        if isinstance(item.get("properties"), dict):
            item["properties"] = strip_secrets(item["properties"])
        cleaned[name] = item
    return cleaned


def merge_overrides(
    defaults: Mapping[str, Any], override: Mapping[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Merge one stored config entry into code-built property defaults.

    Returns (enabled, properties): enabled defaults to True; stored
    properties override defaults key by key.
    """
    enabled = bool(override.get("enabled", True))
    properties = dict(defaults)
    stored = override.get("properties")
    if isinstance(stored, dict):
        properties.update(stored)
    return enabled, properties


class PluginConfigStore:
    """One scope of plugin configuration with its version history."""

    def __init__(self, path: Path, scope: str, payload: dict[str, Any]) -> None:
        self.path = path
        self.scope = scope
        self._payload = payload

    @classmethod
    def load(cls, path: Path, scope: str) -> PluginConfigStore:
        path = Path(path).expanduser()
        if not path.exists():
            payload = {
                "schema": SCHEMA,
                "scope": scope,
                "current": 1,
                "history": [
                    {
                        "seq": 1,
                        "ts": _now(),
                        "action": "init",
                        "actor": "system",
                        "config": {"plugins": {}},
                    }
                ],
            }
            store = cls(path, scope, payload)
            store._write()
            return store

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid plugin config file: {path}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ValueError(f"Unsupported plugin config schema in {path}")
        if not isinstance(payload.get("history"), list) or not payload["history"]:
            raise ValueError(f"Invalid plugin config file: {path}")
        return cls(path, scope, payload)

    def plugins(self) -> dict[str, Any]:
        return dict(self.current()["config"].get("plugins", {}))

    def current(self) -> dict[str, Any]:
        return dict(self._version(self._payload["current"]))

    def current_seq(self) -> int:
        return int(self._payload["current"])

    def history(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._payload["history"]]

    def update(
        self,
        plugins: dict[str, Any],
        *,
        actor: str,
        action: str = "set",
    ) -> int:
        """Append a new version holding the given plugin configuration."""
        return self._append(
            {"plugins": clean_config(plugins)},
            action=action,
            actor=actor,
        )

    def rollback(self, seq: int, *, actor: str) -> int:
        target = self._version(seq)
        return self._append(
            json.loads(json.dumps(target["config"])),
            action="rollback",
            actor=actor,
            target_seq=seq,
        )

    def config_of(self, seq: int) -> dict[str, Any]:
        """The stored configuration of one version, as a copy."""
        config: dict[str, Any] = json.loads(json.dumps(self._version(seq)["config"]))
        return config

    def _version(self, seq: int) -> dict[str, Any]:
        history: list[dict[str, Any]] = self._payload["history"]
        for entry in history:
            if entry.get("seq") == seq:
                return entry
        raise KeyError(f"Unknown plugin config version: {seq}")

    def _append(
        self,
        config: dict[str, Any],
        *,
        action: str,
        actor: str,
        target_seq: int | None = None,
    ) -> int:
        seq = int(self._payload["current"]) + 1
        entry: dict[str, Any] = {
            "seq": seq,
            "ts": _now(),
            "action": action,
            "actor": actor,
            "config": config,
        }
        if target_seq is not None:
            entry["target_seq"] = target_seq
        self._payload["history"].append(entry)
        self._payload["current"] = seq
        self._write()
        return seq

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
