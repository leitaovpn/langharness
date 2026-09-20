"""Tests for the plugin identity model in registry.py."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from types import MappingProxyType

import pytest

from langharness_plugin.registry import (
    PLUGIN_METADATA_ATTR,
    SWAP_POLICIES,
    PluginDescriptor,
    PluginInstanceRecord,
    PluginMetadata,
    PluginRegistrationKey,
    plugin_metadata,
    validate_descriptor,
)
from langharness_scope import ROOT_SCOPE_ID, ScopeId

DESCRIPTION = (
    "Provides an LLM service for agents. Implements agent.plugin.llm. "
    "Use it whenever an agent loop needs a model. Properties: plugin.model.name. "
    "Requires a restart for property changes. Uninstall when no agent needs it."
)


def descriptor(**overrides) -> PluginDescriptor:
    defaults = dict(
        name="llm",
        version="1.0.0",
        module="langharness_core.plugins.llm.llm",
        factory="llm-plugin-factory",
        specification="agent.plugin.llm",
        description=DESCRIPTION,
    )
    defaults.update(overrides)
    return PluginDescriptor(**defaults)


class TestDescriptorFields:
    def test_descriptor_has_exactly_seven_fields(self) -> None:
        assert len(PluginDescriptor.__dataclass_fields__) == 7
        assert descriptor().to_dict() == {
            "name": "llm",
            "version": "1.0.0",
            "module": "langharness_core.plugins.llm.llm",
            "factory": "llm-plugin-factory",
            "specification": "agent.plugin.llm",
            "description": DESCRIPTION,
            "swap_policy": "restart",
        }

    def test_from_dict_rejects_runtime_fields(self) -> None:
        data = descriptor().to_dict()
        data["instance"] = "llm"
        data["scope"] = "server"
        data["properties"] = {}
        with pytest.raises(ValueError, match="Unknown descriptor fields"):
            PluginDescriptor.from_dict(data)

    def test_from_dict_requires_description(self) -> None:
        data = descriptor().to_dict()
        del data["description"]
        with pytest.raises(ValueError, match="Missing descriptor fields"):
            PluginDescriptor.from_dict(data)

    def test_descriptor_is_frozen(self) -> None:
        with pytest.raises(Exception):
            descriptor().name = "other"  # type: ignore[misc]


class TestDescriptorValidation:
    def test_valid_descriptor_passes(self) -> None:
        validate_descriptor(descriptor())

    @pytest.mark.parametrize(
        "field", ["name", "version", "module", "factory", "specification", "description"]
    )
    def test_empty_text_field_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            validate_descriptor(descriptor(**{field: "  "}))

    def test_invalid_swap_policy_rejected(self) -> None:
        with pytest.raises(ValueError, match="swap_policy"):
            validate_descriptor(descriptor(swap_policy="instant"))


class TestInstanceRecord:
    def test_record_round_trip(self) -> None:
        record = PluginInstanceRecord(
            "abc123",
            "llm-plugin-factory",
            "langharness_core.plugins.llm.llm",
            ScopeId("agent:a"),
            {"plugin.model.name": "gpt"},
            enabled=False,
            ranking=3,
            status="disabled",
        )
        assert PluginInstanceRecord.from_dict(record.to_dict()) == record

    def test_record_defaults(self) -> None:
        record = PluginInstanceRecord("u", "f", "m", ROOT_SCOPE_ID, {})
        assert record.enabled is True
        assert record.ranking == 0
        assert record.status == "active"


class TestRegistrationKey:
    def test_key_is_structured_not_joined(self) -> None:
        key = PluginRegistrationKey("f", "m", ScopeId("server"))
        assert key.factory == "f" and key.module == "m"
        assert key.scope_id == ScopeId("server")
        assert key == PluginRegistrationKey("f", "m", ScopeId("server"))
        assert hash(key) == hash(PluginRegistrationKey("f", "m", ScopeId("server")))


class TestSnapshots:
    def test_instance_snapshot_properties_are_read_only(self) -> None:
        from langharness_plugin.registry import PluginInstanceSnapshot

        snapshot = PluginInstanceSnapshot(
            "u", "f", "m", ROOT_SCOPE_ID, {"k": "v"}, True, 0, "active"
        )
        assert isinstance(snapshot.properties, MappingProxyType)
        assert snapshot.properties["k"] == "v"

    def test_definition_snapshot_defaults_to_installed(self) -> None:
        from langharness_plugin.registry import PluginDefinitionSnapshot

        snapshot = PluginDefinitionSnapshot(descriptor(), True, descriptor().module, 0, ())
        assert snapshot.status == "installed"


class TestPluginMetadata:
    def test_decorator_stores_metadata_on_class(self) -> None:
        @plugin_metadata(
            name="echo",
            version="1.0.0",
            factory="echo-factory",
            specification="test.echo",
            description="Echo plugin.",
        )
        class Echo:
            pass

        metadata = getattr(Echo, PLUGIN_METADATA_ATTR)
        assert isinstance(metadata, PluginMetadata)
        assert metadata.factory == "echo-factory"

    def test_metadata_module_defaults_to_none(self) -> None:
        metadata = PluginMetadata(
            name="e", version="1", factory="f", specification="s", description="d"
        )
        assert metadata.module is None
        assert SWAP_POLICIES == ("hot", "restart")


class TestPluginRegistry:
    def test_registry_keyed_by_factory_allows_duplicate_names(self) -> None:
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry(
            [
                descriptor(factory="f-a", module="m.a"),
                descriptor(factory="f-b", module="m.b"),
            ]
        )
        assert registry.get("f-a") is not None
        assert registry.get("f-b") is not None
        assert len(registry.list()) == 2

    def test_registry_rejects_duplicate_factory(self) -> None:
        from langharness_plugin.errors import PluginIdentityConflictError
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry([descriptor(factory="f", module="m.a")])
        with pytest.raises(PluginIdentityConflictError, match="f"):
            registry.add(descriptor(factory="f", module="m.b"))

    def test_registry_get_by_name_raises_ambiguity(self) -> None:
        from langharness_plugin.errors import AmbiguousPluginError
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry(
            [
                descriptor(factory="f-a", module="m.a"),
                descriptor(factory="f-b", module="m.b"),
            ]
        )
        with pytest.raises(AmbiguousPluginError) as caught:
            registry.get_by_name("llm")
        assert set(caught.value.candidates) == {"f-a", "f-b"}

    def test_registry_get_by_name_unique_returns_descriptor(self) -> None:
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry([descriptor(factory="f-a", module="m.a")])
        assert registry.get_by_name("llm").factory == "f-a"
        with pytest.raises(KeyError):
            registry.get_by_name("missing")

    def test_registry_remove_by_factory(self) -> None:
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry([descriptor(factory="f-a", module="m.a")])
        removed = registry.remove("f-a")
        assert removed.factory == "f-a"
        assert registry.get("f-a") is None
        with pytest.raises(KeyError):
            registry.remove("f-a")

    def test_registry_add_validates_descriptor(self) -> None:
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry()
        with pytest.raises(ValueError, match="non-empty"):
            registry.add(descriptor(description="  "))



class TestRegistryPersistence:
    def test_save_and_load_round_trip(self, tmp_path) -> None:
        from langharness_plugin.registry import PluginRegistry

        registry = PluginRegistry(
            [descriptor(factory="f-a", module="m.a")]
        )
        path = tmp_path / "registry.json"
        registry.save(path)
        loaded = PluginRegistry.load(path)
        assert loaded.get("f-a") is not None
        assert loaded.list() == registry.list()

    def test_load_rejects_unsupported_version(self, tmp_path) -> None:
        import json

        from langharness_plugin.registry import PluginRegistry

        path = tmp_path / "registry.json"
        path.write_text(json.dumps({"version": 99, "plugins": []}))
        with pytest.raises(ValueError, match="Unsupported"):
            PluginRegistry.load(path)
