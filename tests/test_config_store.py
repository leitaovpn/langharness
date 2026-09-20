"""Unit tests for the versioned plugin config store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langharness_plugin.config_store import (
    PluginConfigStore,
    merge_overrides,
)


def make_store(tmp_path: Path, scope: str = "api") -> PluginConfigStore:
    return PluginConfigStore.load(tmp_path / "plugin_config" / f"{scope}.json", scope)


def test_load_creates_initial_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.scope == "api"
    assert store.plugins() == {}
    assert store.current_seq() == 1
    history = store.history()
    assert [entry["action"] for entry in history] == ["init"]
    assert store.path.exists()


def test_update_appends_a_full_snapshot(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    seq = store.update(
        {"api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 50}}},
        actor="cli",
    )
    assert seq == 2
    assert store.plugins() == {
        "api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 50}}
    }

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["current"] == 2
    assert [entry["action"] for entry in payload["history"]] == ["init", "set"]
    assert payload["history"][1]["actor"] == "cli"


def test_update_reloads_existing_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.update({"a": {"enabled": True}}, actor="cli")
    reloaded = PluginConfigStore.load(store.path, "api")
    assert reloaded.plugins() == {"a": {"enabled": True}}
    assert reloaded.current_seq() == 2


def test_update_strips_secrets(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.update(
        {
            "runtime-llm": {
                "enabled": True,
                "properties": {
                    "plugin.model.name": "demo",
                    "plugin.model.api_key": "sk-secret",
                    "plugin.token": "tok",
                    "plugin.client_secret": "shh",
                },
            }
        },
        actor="cli",
    )
    assert store.plugins()["runtime-llm"]["properties"] == {
        "plugin.model.name": "demo"
    }


def test_rollback_appends_new_version_pointing_at_the_target(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.update({"a": {"enabled": True}}, actor="cli")
    store.update({"b": {"enabled": True}}, actor="cli")

    seq = store.rollback(2, actor="cli")

    assert seq == 4
    assert store.plugins() == {"a": {"enabled": True}}
    last = store.history()[-1]
    assert last["action"] == "rollback"
    assert last["target_seq"] == 2
    assert store.history()[1]["config"]["plugins"] == {"a": {"enabled": True}}


def test_rollback_rejects_unknown_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.rollback(99, actor="cli")


def test_load_rejects_broken_files(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid plugin config"):
        PluginConfigStore.load(path, "api")

    path.write_text(json.dumps({"schema": 2, "history": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported plugin config schema"):
        PluginConfigStore.load(path, "api")


def test_merge_overrides_merges_enabled_and_properties() -> None:
    defaults = {"plugin.limit": 100}

    enabled, properties = merge_overrides(
        defaults, {"enabled": False, "properties": {"plugin.limit": 5}}
    )

    assert enabled is False
    assert properties == {"plugin.limit": 5}
    assert defaults == {"plugin.limit": 100}


def test_merge_overrides_keeps_defaults() -> None:
    defaults = {"plugin.token": "secret"}
    enabled, properties = merge_overrides(defaults, {})
    assert enabled is True
    assert properties == {"plugin.token": "secret"}
    assert defaults == {"plugin.token": "secret"}


def test_config_of_returns_a_copy(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.update({"a": {"enabled": True}}, actor="cli")

    config = store.config_of(2)

    assert config == {"plugins": {"a": {"enabled": True}}}
    config["plugins"]["a"]["enabled"] = False
    assert store.config_of(2) == {"plugins": {"a": {"enabled": True}}}


def test_clean_config_keeps_entries_without_properties(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.update(
        {
            "plain": {"enabled": False},
            "odd": {"enabled": True, "properties": "not-a-dict"},
        },
        actor="cli",
    )
    stored = store.plugins()
    assert stored["plain"] == {"enabled": False}
    assert stored["odd"]["properties"] == "not-a-dict"


def test_load_overrides_and_agent_configs_round_trip(tmp_path: Path) -> None:
    from langharness_plugin.config_store import (
        agent_scope_configs,
        load_overrides,
        scope_path,
    )

    directory = str(tmp_path)
    assert load_overrides(directory, "api") == {}
    assert agent_scope_configs(directory) == []

    api_store = PluginConfigStore.load(scope_path(directory, "api"), "api")
    api_store.update({"api-auth": {"enabled": True}}, actor="cli")
    agent_store = PluginConfigStore.load(scope_path(directory, "agent:alpha"), "agent:alpha")
    agent_store.update({"tools": {"enabled": False}}, actor="cli")

    assert load_overrides(directory, "api") == {"api-auth": {"enabled": True}}
    assert agent_scope_configs(directory) == [("alpha", {"tools": {"enabled": False}})]
