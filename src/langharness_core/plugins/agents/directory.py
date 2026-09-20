"""Agent directory plugin: one plugin set per agent."""

from __future__ import annotations

import logging
from typing import Any

from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
    Validate,
)

from langharness_config.contracts import Configs
from langharness_core.common.ids import validate_id
from langharness_core.contracts import (
    SPEC_AGENT_LOOP,
    AgentDirectoryProvider,
    AgentRegistryProvider,
)
from langharness_core.plugin import (
    AGENT_PLUGIN_CATALOG,
    DEFAULT_AGENT_PLUGINS,
    agent_binding_properties,
    agent_filter,
    agent_loop_descriptor,
    agent_loop_properties,
    agent_plugin_descriptor,
    agent_required_modules,
    agent_scoped_specifications,
    default_llm_properties,
)
from langharness_plugin.contracts import ScopedPluginRegistrar
from langharness_plugin.registry import PluginInstanceSnapshot
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id
from langharness_plugin.validation import ContractGuard

LOGGER = logging.getLogger("langharness.agent")


@ComponentFactory("agent-directory-plugin-factory")
@Provides(AgentDirectoryProvider)
@Property("_plugin_name", "plugin.name", "agent-directory")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_registry", AgentRegistryProvider, optional=True, immediate_rebind=True)
@RequiresBest("_scope", ScopedPluginRegistrar, optional=True, immediate_rebind=True)
@RequiresBest("_configs_service", Configs, optional=True, immediate_rebind=True)
class AgentDirectoryPlugin:
    """Materializes and resolves the plugin instances of every agent."""

    def __init__(self) -> None:
        self._plugin_name = "agent-directory"
        self._plugin_version = "1.0.0"
        self._registry: Any = None
        self._scope: Any = None
        self._configs_service: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_registry": ContractGuard(self, "_registry", AgentRegistryProvider),
            "_scope": ContractGuard(self, "_scope", ScopedPluginRegistrar),
            "_configs_service": ContractGuard(self, "_configs_service", Configs),
        }
        self._instances: dict[str, dict[str, PluginInstanceSnapshot]] = {}
        self._loops: dict[str, PluginInstanceSnapshot] = {}
        self._failed: set[str] = set()
        self._configs: dict[str, dict[str, Any]] = {}
        self._default_llm: PluginInstanceSnapshot | None = None
        self._llm_default_properties: dict[str, Any] | None = None

    @Validate
    def _validate(self, bundle_context: Any) -> None:
        self._materialize_all()

    @BindField("_registry", if_valid=True)
    def _on_registry_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._materialize_all()

    @UnbindField("_registry")
    def _on_registry_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_scope", if_valid=True)
    def _on_scope_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._materialize_all()

    @UnbindField("_scope")
    def _on_scope_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._instances.clear()
        self._loops.clear()
        self._default_llm = None

    @BindField("_configs_service", if_valid=True)
    def _on_configs_service_bind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        if not self._guards[field].admit(service):
            return
        self._materialize_all()

    @UnbindField("_configs_service")
    def _on_configs_service_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        self._guards[field].release(service)

    def list_agents(self) -> list[dict[str, Any]]:
        if self._registry is None:
            return []
        self._materialize_all()
        agents: list[dict[str, Any]] = []
        for agent in self._registry.list_agents():
            entry = dict(agent)
            plugins = self._instances.get(agent["id"], {})
            entry["materialized"] = agent["id"] in self._instances
            entry["plugins"] = sorted(plugins)
            agents.append(entry)
        return agents

    def get_loop(self, agent_id: str) -> Any | None:
        try:
            wanted = validate_id(agent_id, field="agent_id")
        except ValueError:
            return None
        self._materialize_all()
        if self._scope is None or wanted not in self._instances:
            return None
        return self._scope.find_service(SPEC_AGENT_LOOP, filter=agent_filter(wanted))

    def binding_properties(self, agent_id: str, plugin: str) -> dict[str, Any]:
        """Stored properties of one agent binding, empty when unconfigured."""
        stored = self._configs.get(agent_id, {}).get(plugin, {})
        properties = stored.get("properties")
        return dict(properties) if isinstance(properties, dict) else {}

    def apply_agent_config(self, agent_id: str, plugins: dict[str, Any]) -> None:
        """Replace one agent's plugin bindings with the given configuration."""
        wanted = validate_id(agent_id, field="agent_id")
        self._configs[wanted] = {
            str(name): dict(entry) for name, entry in plugins.items()
        }
        self._failed.discard(wanted)
        self._teardown(wanted)
        agent = self._lookup(wanted)
        if agent is not None and agent.get("enabled", False):
            self._materialize(agent)

    def remove_agent(self, agent_id: str) -> None:
        """Tear down one agent's plugin set and forget its configuration."""
        wanted = validate_id(agent_id, field="agent_id")
        self._configs.pop(wanted, None)
        self._failed.discard(wanted)
        self._teardown(wanted)
        if self._scope is not None:
            try:
                self._scope.remove_scope(agent_instance_scope_id(wanted))
            except KeyError:
                LOGGER.debug("Scope %s was already gone", wanted)

    def ensure_plugin_instance(
        self, agent_id: str, plugin: str, properties: dict[str, Any]
    ) -> None:
        agent = self._require_agent(agent_id)
        self._materialize_all()
        if plugin not in AGENT_PLUGIN_CATALOG:
            raise ValueError(f"Unknown agent plugin: {plugin}")
        merged = dict(self.binding_properties(agent["id"], plugin))
        if plugin == "llm":
            merged = {**self._llm_defaults(), **merged, **properties}
            if not merged.get("plugin.model.name") and not merged.get(
                "plugin.model.instance"
            ):
                LOGGER.debug(
                    "Agent %s has no usable llm configuration; "
                    "it inherits the scope default",
                    agent["id"],
                )
                return
        else:
            merged.update(properties)
        descriptor = agent_plugin_descriptor(plugin)
        scope_id = agent_instance_scope_id(agent["id"])
        wanted = agent_binding_properties(agent["id"], plugin, merged)
        current = self._instances.get(agent["id"], {}).get(plugin)
        if current is not None:
            unchanged = current.scope_id == scope_id and all(
                dict(current.properties).get(key) == value
                for key, value in wanted.items()
            )
            if unchanged:
                return
            # Replace semantics: agent instances are derived state, so the
            # old component is torn down and a fresh UUID is created.
            self._scope.delete_instance(current.instance)
        snapshot = self._scope.create_instance(
            descriptor.factory, descriptor.module, scope_id, properties=wanted
        )
        self._instances.setdefault(agent["id"], {})[plugin] = snapshot

    def reload(self, agent_id: str | None = None) -> None:
        targets = [agent_id] if agent_id is not None else list(self._instances)
        for target in targets:
            self._failed.discard(target)
            self._teardown(target)
            if agent_id is not None:
                agent = self._lookup(target)
                if agent is not None and agent.get("enabled", False):
                    self._materialize(agent)
        if agent_id is None:
            self._materialize_all()

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _materialize_all(self) -> None:
        if self._registry is None or self._scope is None:
            return
        self._ensure_default_llm()
        for agent in self._registry.list_agents():
            agent_id = agent["id"]
            if not agent.get("enabled", False):
                continue
            if agent_id in self._instances or agent_id in self._failed:
                continue
            self._materialize(agent)

    def _llm_defaults(self) -> dict[str, Any]:
        """providers.default mapped to llm plugin properties, cached."""
        if self._configs_service is None:
            return {}
        if self._llm_default_properties is None:
            self._llm_default_properties = self._read_llm_defaults()
        return dict(self._llm_default_properties)

    def _read_llm_defaults(self) -> dict[str, Any]:
        try:
            provider = self._configs_service.get_default_provider()
        except ValueError as exc:
            LOGGER.warning("Default model unavailable: %s", exc)
            return {}
        return {
            "plugin.model.name": provider.get("model", ""),
            "plugin.model.api_key": provider.get("api_key", ""),
            "plugin.model.base_url": provider.get("base_url", ""),
            "plugin.model.protocol": provider.get("protocol", "chat"),
        }

    def _ensure_default_llm(self) -> None:
        """Instantiate the agent-scope fallback LLM from providers.default."""
        if self._scope is None or self._default_llm is not None:
            return
        if AGENT_PLUGIN_CATALOG["llm"][0] not in self._scope.installed_modules():
            LOGGER.debug("Default LLM waits for the llm bundle")
            return
        properties = self._llm_defaults()
        if not properties:
            return
        descriptor = agent_plugin_descriptor("llm")
        try:
            snapshot = self._scope.create_instance(
                descriptor.factory,
                descriptor.module,
                AGENT_SCOPE_ID,
                properties=default_llm_properties(properties),
            )
        except Exception as exc:
            LOGGER.warning("Could not create the default LLM: %s", exc)
            return
        self._default_llm = snapshot

    def _bindings(self, agent_id: str) -> dict[str, dict[str, Any]]:
        stored = self._configs.get(agent_id)
        if stored is None:
            return {plugin: {"enabled": True} for plugin in DEFAULT_AGENT_PLUGINS}
        return {
            name: dict(entry)
            for name, entry in stored.items()
            if entry.get("enabled", True)
        }

    def _materialize(self, agent: dict[str, Any]) -> None:
        agent_id = agent["id"]
        if self._scope is None:
            return
        self._ensure_default_llm()
        bindings = self._bindings(agent_id)
        required = list(agent_required_modules())
        if "llm" in bindings:
            required.append(AGENT_PLUGIN_CATALOG["llm"][0])
        missing = [
            module
            for module in required
            if module not in self._scope.installed_modules()
        ]
        if missing:
            LOGGER.debug("Agent %s waits for bundles: %s", agent_id, missing)
            return
        created: dict[str, PluginInstanceSnapshot] = {}
        try:
            scope_id = agent_instance_scope_id(agent_id)
            self._scope.add_scope(
                scope_id,
                name=agent.get("name") or agent_id,
                parent_id=AGENT_SCOPE_ID,
            )
            for plugin in bindings:
                if plugin not in AGENT_PLUGIN_CATALOG:
                    LOGGER.warning(
                        "Ignoring unknown agent plugin %s for %s", plugin, agent_id
                    )
                    continue
                properties = self._binding_properties(agent, plugin)
                if properties is None:
                    continue
                descriptor = agent_plugin_descriptor(plugin)
                snapshot = self._scope.create_instance(
                    descriptor.factory, descriptor.module, scope_id,
                    properties=properties,
                )
                created[plugin] = snapshot
            loop_descriptor = agent_loop_descriptor()
            loop = self._scope.create_instance(
                loop_descriptor.factory,
                loop_descriptor.module,
                scope_id,
                properties=agent_loop_properties(
                    agent_id,
                    agent_scoped_specifications(),
                    visibility_filter=self._scope.scope_filter(scope_id),
                ),
            )
        except Exception as exc:
            LOGGER.warning("Could not materialize agent %s: %s", agent_id, exc)
            for snapshot in created.values():
                self._safe_kill(snapshot.instance)
            try:
                self._scope.remove_scope(agent_instance_scope_id(agent_id))
            except (KeyError, ValueError):
                LOGGER.debug("Could not roll back scope %s", agent_id)
            self._failed.add(agent_id)
            return
        self._failed.discard(agent_id)
        self._loops[agent_id] = loop
        self._instances[agent_id] = created

    def _binding_properties(
        self, agent: dict[str, Any], plugin: str
    ) -> dict[str, Any] | None:
        properties = dict(self.binding_properties(agent["id"], plugin))
        if plugin == "llm":
            properties = {**self._llm_defaults(), **properties}
            if not properties.get("plugin.model.name") and not properties.get(
                "plugin.model.instance"
            ):
                LOGGER.debug(
                    "Agent %s has an unconfigured llm binding; "
                    "it inherits the scope default",
                    agent["id"],
                )
                return None
        if plugin == "name":
            properties.setdefault("plugin.agent_name", agent.get("name") or agent["id"])
        return agent_binding_properties(agent["id"], plugin, properties)

    def _teardown(self, agent_id: str) -> None:
        loop = self._loops.pop(agent_id, None)
        if loop is not None:
            self._safe_kill(loop.instance)
        for snapshot in self._instances.pop(agent_id, {}).values():
            self._safe_kill(snapshot.instance)

    def _safe_kill(self, instance: str) -> None:
        if self._scope is None:
            return
        try:
            self._scope.delete_instance(instance)
        except Exception:
            LOGGER.debug("Scoped instance %s was already gone", instance)

    def _require_agent(self, agent_id: str) -> dict[str, Any]:
        wanted = validate_id(agent_id, field="agent_id")
        if self._registry is None or self._scope is None:
            raise RuntimeError("Agent directory dependencies are unavailable")
        agent = self._registry.get_agent(wanted)
        if agent is None:
            raise ValueError(f"Unknown agent: {wanted}")
        if not agent.get("enabled", False):
            raise ValueError(f"Agent is disabled: {wanted}")
        return dict(agent)

    def _lookup(self, agent_id: str) -> dict[str, Any] | None:
        if self._registry is None:
            return None
        try:
            wanted = validate_id(agent_id, field="agent_id")
        except ValueError:
            return None
        found = self._registry.get_agent(wanted)
        return dict(found) if found is not None else None
