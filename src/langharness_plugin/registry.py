"""Plugin identity model: descriptors, instance records, and catalogs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from langharness_plugin.errors import AmbiguousPluginError, PluginIdentityConflictError
from langharness_scope import ScopeId

SWAP_POLICIES = ("hot", "restart")

PLUGIN_METADATA_ATTR = "__plugin_metadata__"

InstanceStatus = Literal["active", "disabled", "failed", "missing"]
DefinitionStatus = Literal["installed", "upgrade_available", "missing"]

_DESCRIPTOR_FIELDS = (
    "name",
    "version",
    "module",
    "factory",
    "specification",
    "description",
    "swap_policy",
)


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    """Static definition of one discoverable, installable plugin.

    Strictly seven fields: runtime state (instance UUID, scope, properties,
    enabled, ranking) never lives here. ``description`` is an AI-facing
    operation manual and does not participate in identity.
    """

    name: str
    version: str
    module: str
    factory: str
    specification: str
    description: str
    swap_policy: Literal["hot", "restart"] = "restart"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "module": self.module,
            "factory": self.factory,
            "specification": self.specification,
            "description": self.description,
            "swap_policy": self.swap_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginDescriptor:
        unknown = set(data) - set(_DESCRIPTOR_FIELDS)
        if unknown:
            raise ValueError(f"Unknown descriptor fields: {sorted(unknown)}")
        required = {
            "name",
            "version",
            "module",
            "factory",
            "specification",
            "description",
        }
        missing = required - set(data)
        if missing:
            raise ValueError(f"Missing descriptor fields: {sorted(missing)}")
        return cls(
            name=data["name"],
            version=data["version"],
            module=data["module"],
            factory=data["factory"],
            specification=data["specification"],
            description=data["description"],
            swap_policy=data.get("swap_policy", "restart"),
        )


def validate_descriptor(descriptor: PluginDescriptor) -> None:
    """Reject descriptors with empty, missing, or illegal fixed fields."""
    for text in (
        descriptor.name,
        descriptor.version,
        descriptor.module,
        descriptor.factory,
        descriptor.specification,
        descriptor.description,
    ):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Plugin descriptor text fields must be non-empty strings")
    if descriptor.swap_policy not in SWAP_POLICIES:
        raise ValueError(
            f"Plugin descriptor 'swap_policy' must be one of {SWAP_POLICIES}"
        )


@dataclass(frozen=True, slots=True)
class PluginInstanceRecord:
    """Runtime record binding one component instance to its definition."""

    instance: str  # UUID string, iPOPO component name
    factory: str  # from PluginDescriptor
    module: str  # from PluginDescriptor
    scope_id: ScopeId  # normalized scope; default root
    properties: dict[str, Any]  # effective properties actually injected
    enabled: bool = True
    ranking: int = 0
    status: InstanceStatus = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "factory": self.factory,
            "module": self.module,
            "scope_id": str(self.scope_id),
            "properties": self.properties,
            "enabled": self.enabled,
            "ranking": self.ranking,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginInstanceRecord:
        return cls(
            instance=str(data["instance"]),
            factory=str(data["factory"]),
            module=str(data["module"]),
            scope_id=ScopeId(str(data["scope_id"])),
            properties=dict(data.get("properties", {})),
            enabled=bool(data.get("enabled", True)),
            ranking=int(data.get("ranking", 0)),
            status=str(data.get("status", "active")),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class PluginRegistrationKey:
    """Structured scoped registration identity; never joined into a string."""

    factory: str
    module: str
    scope_id: ScopeId


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    descriptors: tuple[PluginDescriptor, ...]
    warnings: tuple[str, ...]
    discovered_at: datetime


@dataclass(frozen=True, slots=True)
class PluginDefinitionSnapshot:
    descriptor: PluginDescriptor
    installed: bool
    bundle_module: str
    instance_count: int
    scopes: tuple[ScopeId, ...]
    status: DefinitionStatus = "installed"


@dataclass(frozen=True, slots=True)
class PluginInstanceSnapshot:
    instance: str
    factory: str
    module: str
    scope_id: ScopeId
    properties: Mapping[str, Any]  # effective properties, read-only
    enabled: bool
    ranking: int
    status: InstanceStatus

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "properties", MappingProxyType(dict(self.properties))
        )


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """Class-level discovery metadata attached via ``@plugin_metadata``."""

    name: str
    version: str
    factory: str
    specification: str
    description: str
    swap_policy: Literal["hot", "restart"] = "restart"
    module: str | None = None  # when set, must equal the class's __module__


def plugin_metadata(**kwargs: Any) -> Any:
    """Class decorator storing ``PluginMetadata`` under PLUGIN_METADATA_ATTR."""

    def decorate(cls: Any) -> Any:
        setattr(cls, PLUGIN_METADATA_ATTR, PluginMetadata(**kwargs))
        return cls

    return decorate


class PluginRegistry:
    """Validated catalog of plugin definitions, keyed by factory."""

    def __init__(
        self,
        descriptors: list[PluginDescriptor] | tuple[PluginDescriptor, ...] = (),
    ) -> None:
        self._descriptors: dict[str, PluginDescriptor] = {}
        for descriptor in descriptors:
            self.add(descriptor)

    def add(self, descriptor: PluginDescriptor) -> None:
        validate_descriptor(descriptor)
        existing = self._descriptors.get(descriptor.factory)
        if existing is not None:
            raise PluginIdentityConflictError(
                f"Factory {descriptor.factory!r} is already registered by "
                f"module {existing.module!r}"
            )
        self._descriptors[descriptor.factory] = descriptor

    def get(self, factory: str) -> PluginDescriptor | None:
        return self._descriptors.get(factory)

    def get_by_name(self, name: str) -> PluginDescriptor:
        matches = [
            descriptor
            for descriptor in self._descriptors.values()
            if descriptor.name == name
        ]
        if not matches:
            raise KeyError(name)
        if len(matches) > 1:
            raise AmbiguousPluginError(
                name, sorted(descriptor.factory for descriptor in matches)
            )
        return matches[0]

    def list(self) -> list[PluginDescriptor]:
        return [self._descriptors[factory] for factory in sorted(self._descriptors)]

    def remove(self, factory: str) -> PluginDescriptor:
        if factory not in self._descriptors:
            raise KeyError(factory)
        return self._descriptors.pop(factory)

    def save(self, path: Path) -> None:
        data = {
            "version": 1,
            "plugins": [descriptor.to_dict() for descriptor in self.list()],
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> PluginRegistry:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != 1:
            raise ValueError("Unsupported plugin registry version")
        return cls(
            [PluginDescriptor.from_dict(item) for item in raw.get("plugins", [])]
        )
