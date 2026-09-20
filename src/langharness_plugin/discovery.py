"""Discovery of side-effect-free plugin declarations through entry points."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, cast

from langharness_plugin.contracts import SPEC_TOOL_EXPORT_TARGET
from langharness_plugin.package import PluginPackage
from langharness_plugin.registry import (
    PLUGIN_METADATA_ATTR,
    PluginDescriptor,
    PluginMetadata,
    validate_descriptor,
)

ENTRY_POINT_GROUP = "langharness.plugins"
VALID_TARGETS = {"root", "ui", "server", "agent", "agent_instance"}


class PluginDiscoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveryFailure:
    entry_point: str
    value: str
    detail: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    packages: tuple[PluginPackage, ...]
    failures: tuple[DiscoveryFailure, ...]


def _installed_entry_points() -> Iterable[EntryPoint]:
    return entry_points(group=ENTRY_POINT_GROUP)


def descriptor_discovery(
    loader: Callable[[], Iterable[Any]],
) -> tuple[dict[tuple[str, str], PluginDescriptor], tuple[str, ...]]:
    """Scan entry points for plugin classes and build static descriptors.

    Returns a (module, factory)-keyed map plus per-problem warnings. A bad
    entry point never blocks the rest.
    """
    warnings: list[str] = []
    descriptors: dict[tuple[str, str], PluginDescriptor] = {}
    for entry_point in loader():
        try:
            loaded = entry_point.load()
            cls = (
                loaded
                if isinstance(loaded, type)
                else loaded()
                if callable(loaded)
                else None
            )
            if not isinstance(cls, type):
                warnings.append(
                    f"{entry_point.name!r}: entry point must be a class or a "
                    f"callable returning a class, got {type(loaded).__name__}"
                )
                continue
            metadata = getattr(cls, PLUGIN_METADATA_ATTR, None)
            if not isinstance(metadata, PluginMetadata):
                warnings.append(
                    f"{entry_point.name!r}: class is missing @plugin_metadata"
                )
                continue
            if metadata.module is not None and metadata.module != cls.__module__:
                warnings.append(
                    f"{entry_point.name!r}: metadata module "
                    f"{metadata.module!r} does not match {cls.__module__!r}"
                )
                continue
            descriptor = PluginDescriptor(
                name=metadata.name,
                version=metadata.version,
                module=cls.__module__,
                factory=metadata.factory,
                specification=metadata.specification,
                description=metadata.description,
                swap_policy=metadata.swap_policy,
            )
            validate_descriptor(descriptor)
            key = (descriptor.module, descriptor.factory)
            if key in descriptors:
                warnings.append(
                    f"{entry_point.name!r}: duplicate (module, factory) {key}; "
                    "keeping the first entry point"
                )
                continue
            descriptors[key] = descriptor
        except Exception as exc:
            warnings.append(f"{entry_point.name!r}: {exc}")
    return descriptors, tuple(warnings)


class PluginDiscovery:
    def __init__(
        self,
        loader: Callable[[], Iterable[Any]] = _installed_entry_points,
    ) -> None:
        self._loader = loader

    def scan(self) -> DiscoveryResult:
        packages: list[PluginPackage] = []
        failures: list[DiscoveryFailure] = []
        for entry_point in self._loader():
            try:
                factory = entry_point.load()
                if not callable(factory):
                    raise PluginDiscoveryError(
                        f"Entry point {entry_point.name!r} must be callable"
                    )
                package = factory()
                self._validate_package(package)
                packages.append(cast(PluginPackage, package))
            except Exception as exc:
                failures.append(
                    DiscoveryFailure(
                        str(entry_point.name), str(entry_point.value), str(exc)
                    )
                )
        self._validate_catalog(packages)
        return DiscoveryResult(tuple(packages), tuple(failures))

    def discover(self) -> tuple[PluginPackage, ...]:
        result = self.scan()
        if result.failures:
            raise PluginDiscoveryError(result.failures[0].detail)
        return result.packages

    @staticmethod
    def _validate_package(package: Any) -> None:
        if not isinstance(package, PluginPackage):
            raise PluginDiscoveryError("Entry point did not return PluginPackage")
        if not package.id.strip() or not package.version.strip():
            raise PluginDiscoveryError("Plugin package id and version must be non-empty")
        contribution_ids: set[str] = set()
        for contribution in package.contributions:
            if contribution.id in contribution_ids:
                raise PluginDiscoveryError(
                    f"Duplicate contribution {contribution.id!r} in {package.id!r}"
                )
            contribution_ids.add(contribution.id)
            if contribution.target not in VALID_TARGETS:
                raise PluginDiscoveryError(
                    f"Invalid contribution target: {contribution.target!r}"
                )
            validate_descriptor(contribution.descriptor)
            if (
                contribution.tool_exports
                and contribution.descriptor.specification != SPEC_TOOL_EXPORT_TARGET
            ):
                raise PluginDiscoveryError(
                    "Tool exports require the plugin.tool_export.target specification"
                )

    @staticmethod
    def _validate_catalog(packages: list[PluginPackage]) -> None:
        package_ids: set[str] = set()
        for package in packages:
            if package.id in package_ids:
                raise PluginDiscoveryError(f"Duplicate plugin package: {package.id!r}")
            package_ids.add(package.id)
