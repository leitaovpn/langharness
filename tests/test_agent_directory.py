"""Unit tests for the agent directory plugin (per-agent plugin sets)."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

import pytest

from langharness_core.contracts import (
    SPEC_AGENT_DIRECTORY,
    SPEC_AGENT_LOOP,
    AgentDirectoryProvider,
)
from langharness_core.plugins.agents.directory import AgentDirectoryPlugin
from langharness_plugin.contracts import SPEC_PLUGIN_SCOPE, ScopedPluginRegistrar
from langharness_plugin.registry import PluginInstanceSnapshot
from langharness_plugin.scope_const import AGENT_SCOPE_ID
from langharness_plugin.validation import contract_for, validate
from langharness_scope import ROOT_SCOPE_ID, Scope, ScopeId, ScopeTree

FACTORIES = {
    "tools": "workspace-tools-plugin-factory",
    "name": "agent-name-plugin-factory",
    "llm": "llm-plugin-factory",
    "agent-loop": "agent-loop-factory",
}


def agent_record(
    agent_id: str = "simple_agent", *, enabled: bool = True, name: str = "Simple Agent"
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": name,
        "description": f"{agent_id} description",
        "enabled": enabled,
        "created_at": "2026-09-15T00:00:00+00:00",
        "updated_at": "2026-09-15T00:00:00+00:00",
    }


REQUIRED_MODULES = {
    "langharness_core.plugins.tools.workspace",
    "langharness_core.plugins.name.template_name",
    "langharness_core.plugins.loop.agent_loop",
}

LLM_MODULE = "langharness_core.plugins.llm.llm"

# The default fake has every module installed; tests that exercise the
# wait-for-bundles path remove modules from this set explicitly.
INSTALLED_MODULES = REQUIRED_MODULES | {LLM_MODULE}


class FakeScope:
    def __init__(
        self, *, fail_on: str | None = None, modules: set[str] | None = None
    ) -> None:
        self.instances: dict[str, PluginInstanceSnapshot] = {}
        self.instance_scopes: dict[str, str] = {}
        self.killed: list[str] = []
        self.services: dict[tuple[str, str | None], Any] = {}
        self.fail_on = fail_on  # factory name that fails to instantiate
        self.modules = INSTALLED_MODULES if modules is None else modules
        self.tree = ScopeTree()
        self._counter = 0

    def installed_modules(self) -> set[str]:
        return set(self.modules)

    def create_instance(
        self,
        factory: str,
        module: str,
        scope_id: ScopeId | None = None,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool = True,
        ranking: int = 0,
    ) -> PluginInstanceSnapshot:
        if self.fail_on is not None and factory == self.fail_on:
            raise ValueError(f"cannot instantiate {factory}")
        self._counter += 1
        instance = f"uuid-{self._counter}"
        snapshot = PluginInstanceSnapshot(
            instance, factory, module, scope_id or ROOT_SCOPE_ID,
            dict(properties or {}), enabled, ranking,
            "active" if enabled else "disabled",
        )
        self.instances[instance] = snapshot
        if scope_id is not None:
            self.instance_scopes[instance] = str(scope_id)
        return snapshot

    def update_instance(
        self,
        instance: str,
        *,
        properties: Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        ranking: int | None = None,
    ) -> PluginInstanceSnapshot:
        if instance not in self.instances:
            raise KeyError(instance)
        current = self.instances[instance]
        merged = dict(current.properties)
        if properties is not None:
            merged.update(properties)
        updated = PluginInstanceSnapshot(
            instance, current.factory, current.module, current.scope_id,
            merged,
            current.enabled if enabled is None else enabled,
            current.ranking if ranking is None else ranking,
            current.status,
        )
        self.instances[instance] = updated
        return updated

    def get_instance(self, instance: str) -> PluginInstanceSnapshot:
        return self.instances[instance]

    def list_instance(
        self,
        *,
        factory: str | None = None,
        module: str | None = None,
        scope_id: ScopeId | None = None,
        enabled: bool | None = None,
    ) -> tuple[PluginInstanceSnapshot, ...]:
        return tuple(
            item for item in self.instances.values()
            if (factory is None or item.factory == factory)
            and (module is None or item.module == module)
            and (scope_id is None or item.scope_id == scope_id)
            and (enabled is None or item.enabled == enabled)
        )

    def delete_instance(self, instance: str) -> None:
        if instance not in self.instances:
            raise KeyError(instance)
        self.instances.pop(instance)
        self.instance_scopes.pop(instance, None)
        self.killed.append(instance)

    def add_scope(
        self,
        scope_id: ScopeId,
        *,
        name: str,
        parent_id: ScopeId | None = None,
    ) -> Scope:
        if self.tree.get(scope_id) is None:
            wanted_parent = parent_id or ROOT_SCOPE_ID
            if self.tree.get(wanted_parent) is None:
                self.tree.create(wanted_parent, str(wanted_parent))
            self.tree.create(scope_id, name, wanted_parent)
        return cast(Scope, self.tree.get(scope_id))

    def scope_filter(self, scope_id: ScopeId) -> str:
        visible = self.tree.ancestors(scope_id, include_self=True)
        return "(|" + "".join(
            f"(plugin.scope_id={scope.id})" for scope in visible
        ) + ")"

    def remove_scope(self, scope_id: ScopeId, *, recursive: bool = False) -> None:
        self.tree.remove(scope_id, recursive=recursive)

    def find_service(self, specification: str, filter: str | None = None) -> Any:
        return self.services.get((specification, filter))

    def find_services(
        self, specification: str, filter: str | None = None
    ) -> list[Any]:
        service = self.find_service(specification, filter)
        return [] if service is None else [service]

    def apply_config(self, overrides: dict[str, Any]) -> dict[str, list[str]]:
        return {"applied": sorted(overrides), "restart_required": []}

    def by_factory(self, factory: str) -> list[PluginInstanceSnapshot]:
        return [
            item for item in self.instances.values() if item.factory == factory
        ]


class FakeRegistry:
    def __init__(self, agents: list[dict[str, Any]] | None = None) -> None:
        self._agents = agents if agents is not None else [agent_record()]

    def list_agents(self) -> list[dict[str, Any]]:
        return [dict(agent) for agent in self._agents]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        for agent in self._agents:
            if agent["id"] == agent_id:
                return dict(agent)
        return None

    def create_agent(
        self, agent_id: str, name: str, description: str
    ) -> dict[str, Any]:
        return agent_record(agent_id, name=name)

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        return agent_record(agent_id)

    def delete_agent(self, agent_id: str) -> None:
        return None


class FakeConfigs:
    """Minimal Configs service stub; missing default raises like the real one."""

    def __init__(self, default: dict[str, Any] | None = None) -> None:
        self._default = default

    def get(self, section: str, key: str, fallback: str | None = None) -> str | None:
        return fallback

    def get_section(self, section: str) -> dict[str, Any]:
        return {}

    def get_provider(self, name: str) -> dict[str, Any]:
        return {}

    def list_providers(self) -> list[str]:
        return []

    def get_default_provider(self) -> dict[str, Any]:
        if self._default is None:
            raise ValueError(
                "No default model is configured: add a "
                "[providers.default] section to langharness.toml"
            )
        return dict(self._default)


def make_directory(
    agents: list[dict[str, Any]] | None = None,
    scope: FakeScope | None = None,
    configs: FakeConfigs | None = None,
) -> AgentDirectoryPlugin:
    plugin = AgentDirectoryPlugin()
    plugin._registry = FakeRegistry(agents)
    plugin._scope = scope if scope is not None else FakeScope()
    if configs is not None:
        plugin._configs_service = configs
        plugin._on_configs_service_bind("_configs_service", configs, None)
    plugin._validate(cast(Any, None))
    return plugin


def only(scope: FakeScope, factory: str) -> PluginInstanceSnapshot:
    matches = scope.by_factory(factory)
    assert len(matches) == 1, f"expected one {factory}, got {len(matches)}"
    return matches[0]


def with_agent_id(
    scope: FakeScope, factory: str, agent_id: str
) -> PluginInstanceSnapshot:
    matches = [
        item for item in scope.by_factory(factory)
        if item.properties.get("plugin.agent_id") == agent_id
    ]
    assert len(matches) == 1, f"expected one {factory} for {agent_id}"
    return matches[0]


def test_directory_contract_is_pinned() -> None:
    assert SPEC_AGENT_DIRECTORY == "agent.directory"
    assert contract_for(SPEC_AGENT_DIRECTORY) is AgentDirectoryProvider
    assert validate(AgentDirectoryPlugin(), AgentDirectoryProvider) == ()
    assert contract_for(SPEC_PLUGIN_SCOPE) is ScopedPluginRegistrar


def test_validate_materializes_default_plugins_and_loop() -> None:
    plugin = make_directory()
    scope = plugin._scope

    assert {item.factory for item in scope.instances.values()} == {
        FACTORIES["tools"],
        FACTORIES["name"],
        FACTORIES["agent-loop"],
    }
    tools = only(scope, FACTORIES["tools"])
    assert tools.module == "langharness_core.plugins.tools.workspace"
    assert tools.properties["plugin.agent_id"] == "simple_agent"
    assert tools.properties["plugin.tools.root_dir"] == "."

    name = only(scope, FACTORIES["name"])
    assert name.properties["plugin.agent_name"] == "Simple Agent"

    loop = only(scope, FACTORIES["agent-loop"])
    assert loop.properties["plugin.agent_id"] == "simple_agent"
    visibility = "(|(plugin.scope_id=agent:simple_agent)(plugin.scope_id=agent)(plugin.scope_id=root))"
    assert loop.properties["requires.filters"] == {
        "_llm_provider": visibility,
        "_scoped_llm_providers": visibility,
        "_tool_providers": visibility,
        "_name_provider": visibility,
    }


def test_validate_skips_disabled_agents() -> None:
    plugin = make_directory([agent_record(enabled=False)])
    assert plugin._scope.instances == {}
    assert plugin.list_agents()[0]["materialized"] is False


def test_validate_without_dependencies_is_a_noop() -> None:
    plugin = AgentDirectoryPlugin()
    plugin._validate(cast(Any, None))
    assert plugin.list_agents() == []
    assert plugin.get_loop("simple_agent") is None


def test_list_agents_reports_materialization() -> None:
    plugin = make_directory()
    entry = plugin.list_agents()[0]
    assert entry["id"] == "simple_agent"
    assert entry["materialized"] is True
    assert entry["plugins"] == ["name", "tools"]


def test_get_loop_uses_the_agent_filter() -> None:
    plugin = make_directory()
    plugin._scope.services[(SPEC_AGENT_LOOP, "(plugin.agent_id=simple_agent)")] = "loop"

    assert plugin.get_loop("simple_agent") == "loop"
    assert plugin.get_loop("missing_agent") is None
    assert plugin.get_loop("bad agent") is None


def test_ensure_plugin_instance_creates_agent_scoped_llm() -> None:
    plugin = make_directory()
    properties = {
        "plugin.model.name": "test-model",
        "plugin.model.api_key": "key",
        "plugin.model.base_url": "https://models.example/v1",
        "plugin.model.protocol": "chat",
    }

    plugin.ensure_plugin_instance("simple_agent", "llm", properties)

    snapshot = only(plugin._scope, FACTORIES["llm"])
    assert snapshot.module == LLM_MODULE
    assert snapshot.properties == {**properties, "plugin.agent_id": "simple_agent"}
    assert snapshot.scope_id == ScopeId("agent:simple_agent")
    assert plugin.list_agents()[0]["plugins"] == ["llm", "name", "tools"]


def test_ensure_plugin_instance_is_idempotent_for_equal_configuration() -> None:
    plugin = make_directory()
    properties = {"plugin.model.name": "test-model"}

    plugin.ensure_plugin_instance("simple_agent", "llm", properties)
    plugin.ensure_plugin_instance("simple_agent", "llm", dict(properties))

    assert plugin._scope.killed == []
    assert len(plugin._scope.by_factory(FACTORIES["llm"])) == 1
    assert len(plugin._scope.instances) == 4


def test_ensure_plugin_instance_replaces_changed_configuration() -> None:
    plugin = make_directory()
    plugin.ensure_plugin_instance("simple_agent", "llm", {"plugin.model.name": "one"})
    old_uuid = only(plugin._scope, FACTORIES["llm"]).instance
    plugin.ensure_plugin_instance("simple_agent", "llm", {"plugin.model.name": "two"})

    # Replace semantics: the old component is torn down and a fresh UUID is
    # created with the new configuration.
    assert plugin._scope.killed == [old_uuid]
    snapshot = only(plugin._scope, FACTORIES["llm"])
    assert snapshot.instance != old_uuid
    assert snapshot.properties["plugin.model.name"] == "two"


def test_ensure_plugin_instance_rejects_unknown_agent() -> None:
    plugin = make_directory()
    with pytest.raises(ValueError, match="Unknown agent: ghost"):
        plugin.ensure_plugin_instance("ghost", "llm", {})


def test_ensure_plugin_instance_rejects_disabled_agent() -> None:
    plugin = make_directory([agent_record(enabled=False)])
    with pytest.raises(ValueError, match="Agent is disabled"):
        plugin.ensure_plugin_instance("simple_agent", "llm", {})


def test_ensure_plugin_instance_rejects_unknown_plugin() -> None:
    plugin = make_directory()
    with pytest.raises(ValueError, match="Unknown agent plugin: warp"):
        plugin.ensure_plugin_instance("simple_agent", "warp", {})


def test_remove_agent_removes_its_scope_after_instances() -> None:
    plugin = make_directory()

    plugin.remove_agent("simple_agent")

    assert plugin._scope.tree.get(ScopeId("agent:simple_agent")) is None
    assert plugin._scope.instances == {}


def test_reload_tears_down_and_materializes_again() -> None:
    plugin = make_directory()
    plugin.ensure_plugin_instance("simple_agent", "llm", {"plugin.model.name": "one"})

    plugin.reload("simple_agent")

    assert len(plugin._scope.killed) == 4  # loop + tools + name + llm
    assert {item.factory for item in plugin._scope.instances.values()} == {
        FACTORIES["tools"],
        FACTORIES["name"],
        FACTORIES["agent-loop"],
    }
    assert plugin.list_agents()[0]["plugins"] == ["name", "tools"]


def test_reload_without_agent_targets_every_materialized_agent() -> None:
    plugin = make_directory([agent_record("a1"), agent_record("a2")])
    plugin.reload()

    assert len(plugin._scope.killed) == 6
    assert {item.factory for item in plugin._scope.instances.values()} == {
        FACTORIES["tools"],
        FACTORIES["name"],
        FACTORIES["agent-loop"],
    }
    assert len(plugin._scope.by_factory(FACTORIES["agent-loop"])) == 2


def test_materialization_failure_is_contained() -> None:
    scope = FakeScope(fail_on=FACTORIES["name"])
    plugin = make_directory(scope=scope)

    assert plugin.list_agents()[0]["materialized"] is False
    assert len(scope.killed) == 1  # the tools instance rolled back
    assert plugin._scope.instances == {}


def test_bind_callbacks_materialize_and_release() -> None:
    plugin = AgentDirectoryPlugin()
    plugin._registry = FakeRegistry()
    plugin._scope = FakeScope()

    plugin._on_scope_bind("_scope", plugin._scope, None)
    assert len(plugin._scope.instances) == 3

    plugin._on_scope_unbind("_scope", plugin._scope, None)
    assert plugin._instances == {}
    assert plugin._loops == {}
    assert plugin._default_llm is None


def test_apply_agent_config_honours_stored_bindings() -> None:
    plugin = make_directory()

    plugin.apply_agent_config(
        "simple_agent",
        {"tools": {"enabled": True, "properties": {"plugin.tools.root_dir": "/work"}}},
    )

    assert {item.factory for item in plugin._scope.instances.values()} == {
        FACTORIES["tools"],
        FACTORIES["agent-loop"],
    }
    tools = only(plugin._scope, FACTORIES["tools"])
    assert tools.properties["plugin.tools.root_dir"] == "/work"
    assert plugin.list_agents()[0]["plugins"] == ["tools"]
    assert plugin._scope.killed  # the old name/tools/loop set was torn down


def test_apply_agent_config_skips_disabled_bindings() -> None:
    plugin = make_directory()
    plugin.apply_agent_config(
        "simple_agent",
        {
            "tools": {"enabled": False},
            "name": {"enabled": True, "properties": {"plugin.agent_name": "Custom"}},
        },
    )
    assert {item.factory for item in plugin._scope.instances.values()} == {
        FACTORIES["name"],
        FACTORIES["agent-loop"],
    }
    assert only(plugin._scope, FACTORIES["name"]).properties["plugin.agent_name"] == "Custom"


def test_apply_agent_config_ignores_unknown_plugins() -> None:
    plugin = make_directory()
    plugin.apply_agent_config("simple_agent", {"warp": {"enabled": True}})
    assert {item.factory for item in plugin._scope.instances.values()} == {
        FACTORIES["agent-loop"]
    }
    assert plugin.list_agents()[0]["plugins"] == []


def test_ensure_plugin_instance_merges_stored_binding_properties() -> None:
    plugin = make_directory()
    plugin.apply_agent_config(
        "simple_agent",
        {"llm": {"properties": {"plugin.model.name": "stored-model"}}},
    )

    plugin.ensure_plugin_instance("simple_agent", "llm", {})

    snapshot = only(plugin._scope, FACTORIES["llm"])
    assert snapshot.properties["plugin.model.name"] == "stored-model"
    assert plugin.binding_properties("simple_agent", "llm") == {
        "plugin.model.name": "stored-model"
    }


def test_ensure_plugin_instance_request_overrides_stored_defaults() -> None:
    plugin = make_directory()
    plugin.apply_agent_config(
        "simple_agent",
        {"llm": {"properties": {"plugin.model.name": "stored", "plugin.model.base_url": "u"}}},
    )

    plugin.ensure_plugin_instance("simple_agent", "llm", {"plugin.model.name": "asked"})

    snapshot = only(plugin._scope, FACTORIES["llm"])
    assert snapshot.properties == {
        "plugin.model.name": "asked",
        "plugin.model.base_url": "u",
        "plugin.agent_id": "simple_agent",
    }


def test_binding_properties_empty_for_unconfigured_agent() -> None:
    plugin = make_directory()
    assert plugin.binding_properties("simple_agent", "llm") == {}


def test_remove_agent_tears_down_instances() -> None:
    plugin = make_directory()
    plugin.ensure_plugin_instance("simple_agent", "llm", {"plugin.model.name": "m"})

    plugin.remove_agent("simple_agent")

    assert plugin._scope.instances == {}
    assert plugin._scope.killed
    assert plugin.get_loop("simple_agent") is None
    assert plugin._configs == {}


def test_plugin_info_and_reload_without_materialization() -> None:
    plugin = make_directory([agent_record(enabled=False)])
    assert plugin.get_plugin_info() == {
        "name": "agent-directory",
        "version": "1.0.0",
    }
    plugin.reload()
    assert plugin._scope.instances == {}


def test_ensure_plugin_instance_without_scope_raises() -> None:
    plugin = AgentDirectoryPlugin()
    plugin._registry = FakeRegistry()
    with pytest.raises(RuntimeError, match="dependencies"):
        plugin.ensure_plugin_instance("simple_agent", "llm", {})


def test_bind_and_unbind_callbacks_guard_services() -> None:
    plugin = AgentDirectoryPlugin()
    registry = FakeRegistry()
    scope = FakeScope()
    plugin._registry = registry
    plugin._scope = scope

    plugin._on_registry_bind("_registry", registry, None)
    plugin._on_scope_bind("_scope", scope, None)
    assert len(scope.instances) == 3

    plugin._on_registry_unbind("_registry", registry, None)
    plugin._on_scope_unbind("_scope", scope, None)
    assert plugin._instances == {}


def test_remove_agent_without_configuration_is_safe() -> None:
    plugin = make_directory()
    plugin.remove_agent("simple_agent")
    plugin.remove_agent("unmaterialized_agent")
    assert plugin._scope.instances == {}


def test_reload_of_unknown_agent_is_a_noop() -> None:
    plugin = make_directory()
    plugin.reload("ghost")
    assert {item.factory for item in plugin._scope.instances.values()} == {
        FACTORIES["tools"],
        FACTORIES["name"],
        FACTORIES["agent-loop"],
    }


def test_materialize_creates_agent_scope_default_llm_from_providers_default() -> None:
    plugin = make_directory(
        configs=FakeConfigs(
            {
                "model": "default-model",
                "api_key": "default-key",
                "base_url": "https://default.example/v1",
                "protocol": "responses",
            }
        )
    )

    default = only(plugin._scope, FACTORIES["llm"])
    assert default.module == LLM_MODULE
    assert default.properties == {
        "plugin.model.name": "default-model",
        "plugin.model.api_key": "default-key",
        "plugin.model.base_url": "https://default.example/v1",
        "plugin.model.protocol": "responses",
    }
    assert "plugin.agent_id" not in default.properties
    assert plugin._scope.instance_scopes[default.instance] == str(AGENT_SCOPE_ID)


def test_default_llm_skipped_without_configs_service() -> None:
    plugin = make_directory()
    assert plugin._scope.by_factory(FACTORIES["llm"]) == []


def test_default_llm_skipped_when_providers_default_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        plugin = make_directory(configs=FakeConfigs(None))

    assert plugin._scope.by_factory(FACTORIES["llm"]) == []
    assert "Default model unavailable" in caplog.text


def test_default_llm_created_once_across_repeated_materialization() -> None:
    plugin = make_directory(configs=FakeConfigs({"model": "default-model"}))

    plugin._materialize_all()
    plugin._materialize_all()

    assert len(plugin._scope.by_factory(FACTORIES["llm"])) == 1


def test_late_configs_bind_creates_default_llm() -> None:
    plugin = make_directory()
    assert plugin._scope.by_factory(FACTORIES["llm"]) == []

    late_configs = FakeConfigs({"model": "late-model"})
    plugin._configs_service = late_configs
    plugin._on_configs_service_bind("_configs_service", late_configs, None)

    default = only(plugin._scope, FACTORIES["llm"])
    assert default.properties["plugin.model.name"] == "late-model"


def test_agent_specific_llm_coexists_with_agent_scope_default() -> None:
    plugin = make_directory(configs=FakeConfigs({"model": "default-model"}))

    plugin.ensure_plugin_instance(
        "simple_agent", "llm", {"plugin.model.name": "agent-model"}
    )

    own = with_agent_id(plugin._scope, FACTORIES["llm"], "simple_agent")
    assert own.properties["plugin.model.name"] == "agent-model"
    assert len(plugin._scope.by_factory(FACTORIES["llm"])) == 2


DEFAULT_PROVIDER = {
    "model": "default-model",
    "api_key": "default-key",
    "base_url": "https://default.example/v1",
    "protocol": "chat",
}

DEFAULT_PROVIDER_MAPPED = {
    "plugin.model.name": "default-model",
    "plugin.model.api_key": "default-key",
    "plugin.model.base_url": "https://default.example/v1",
    "plugin.model.protocol": "chat",
}


def test_default_llm_waits_for_llm_bundle_then_creates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scope = FakeScope(modules=REQUIRED_MODULES - {LLM_MODULE})
    plugin = make_directory(configs=FakeConfigs(DEFAULT_PROVIDER), scope=scope)
    assert plugin._scope.by_factory(FACTORIES["llm"]) == []

    scope.modules.add(LLM_MODULE)
    plugin._materialize_all()

    assert len(plugin._scope.by_factory(FACTORIES["llm"])) == 1
    assert "Could not create the default LLM" not in caplog.text


def test_empty_llm_binding_inherits_providers_default() -> None:
    plugin = make_directory(configs=FakeConfigs(DEFAULT_PROVIDER))
    plugin.apply_agent_config(
        "simple_agent", {"llm": {"enabled": True, "properties": {}}}
    )

    snapshot = with_agent_id(plugin._scope, FACTORIES["llm"], "simple_agent")
    assert snapshot.properties == {**DEFAULT_PROVIDER_MAPPED, "plugin.agent_id": "simple_agent"}


def test_empty_llm_binding_without_default_is_skipped() -> None:
    plugin = make_directory(configs=FakeConfigs(None))
    plugin.apply_agent_config(
        "simple_agent", {"llm": {"enabled": True, "properties": {}}}
    )

    assert plugin._scope.by_factory(FACTORIES["llm"]) == []
    assert plugin._scope.by_factory(FACTORIES["agent-loop"])


def test_partial_llm_binding_fills_missing_fields_from_default() -> None:
    plugin = make_directory(configs=FakeConfigs(DEFAULT_PROVIDER))
    plugin.apply_agent_config(
        "simple_agent",
        {"llm": {"enabled": True, "properties": {"plugin.model.name": "stored"}}},
    )

    snapshot = with_agent_id(plugin._scope, FACTORIES["llm"], "simple_agent")
    assert snapshot.properties["plugin.model.name"] == "stored"
    assert snapshot.properties["plugin.model.api_key"] == "default-key"
    assert (
        snapshot.properties["plugin.model.base_url"]
        == "https://default.example/v1"
    )
    assert snapshot.properties["plugin.model.protocol"] == "chat"


def test_llm_binding_materialization_waits_for_llm_bundle() -> None:
    scope = FakeScope(modules=REQUIRED_MODULES - {LLM_MODULE})
    plugin = make_directory(configs=FakeConfigs(DEFAULT_PROVIDER), scope=scope)
    plugin.apply_agent_config(
        "simple_agent", {"llm": {"enabled": True, "properties": {}}}
    )

    assert plugin._scope.instances == {}
    assert "simple_agent" not in plugin._failed

    scope.modules.add(LLM_MODULE)
    plugin._materialize_all()

    assert len(plugin._scope.by_factory(FACTORIES["llm"])) == 2


def test_ensure_plugin_instance_fills_missing_fields_from_default() -> None:
    plugin = make_directory(configs=FakeConfigs(DEFAULT_PROVIDER))

    plugin.ensure_plugin_instance(
        "simple_agent", "llm", {"plugin.model.name": "request-model"}
    )

    snapshot = with_agent_id(plugin._scope, FACTORIES["llm"], "simple_agent")
    assert snapshot.properties["plugin.model.name"] == "request-model"
    assert snapshot.properties["plugin.model.api_key"] == "default-key"
    assert (
        snapshot.properties["plugin.model.base_url"]
        == "https://default.example/v1"
    )


def test_ensure_plugin_instance_skips_unconfigured() -> None:
    plugin = make_directory(configs=FakeConfigs(None))

    plugin.ensure_plugin_instance("simple_agent", "llm", {})

    assert plugin._scope.by_factory(FACTORIES["llm"]) == []
