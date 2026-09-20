"""Tests for class-based descriptor discovery and package validation."""
# mypy: ignore-errors
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_plugin.discovery import (
    DiscoveryResult,
    PluginDiscovery,
    descriptor_discovery,
)
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import plugin_metadata, validate_descriptor


class EntryPoint:
    """Minimal fake matching the loader contract used by discovery."""

    def __init__(self, target, name: str = "dynamic") -> None:
        self.target = target
        self.name = name
        self.value = f"{name}:load"

    def load(self):
        return self.target

DESCRIPTION = (
    "Echoes text. Implements test.echo. Use for round-trip checks. "
    "No properties. Uninstall when no longer needed."
)


@plugin_metadata(
    name="echo",
    version="1.0.0",
    factory="echo-factory",
    specification="test.echo",
    description=DESCRIPTION,
)
class Echo:
    pass


@plugin_metadata(
    name="echo2",
    version="1.0.0",
    factory="echo2-factory",
    specification="test.echo",
    description=DESCRIPTION,
    module="wrong.module.path",
)
class MismatchedModule:
    pass


@plugin_metadata(
    name="broken",
    version="",
    factory="broken-factory",
    specification="test.echo",
    description="",
)
class Broken:
    pass


class MissingMetadata:
    pass


def entries(*values):
    return [EntryPoint(item, f"ep-{index}") for index, item in enumerate(values)]


def test_class_entry_point_produces_descriptor() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(Echo))
    assert len(descriptors) == 1
    descriptor = descriptors[(Echo.__module__, "echo-factory")]
    assert descriptor.name == "echo"
    assert descriptor.module == Echo.__module__
    validate_descriptor(descriptor)  # must not raise


def test_callable_entry_point_returning_class() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(lambda: Echo))
    assert (Echo.__module__, "echo-factory") in descriptors


def test_duplicate_module_factory_first_wins() -> None:
    other = type("Other", (Echo,), {})
    descriptors, warnings = descriptor_discovery(lambda: entries(Echo, other))
    assert len(descriptors) == 1
    assert any("duplicate" in warning for warning in warnings)


def test_module_mismatch_skipped_with_warning() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(MismatchedModule))
    assert descriptors == {}
    assert any("module" in warning for warning in warnings)


def test_invalid_fields_skipped_with_warning() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(Broken))
    assert descriptors == {}
    assert warnings


def test_missing_metadata_skipped_with_warning() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(MissingMetadata))
    assert descriptors == {}
    assert warnings


def test_non_class_callable_result_skipped() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(lambda: 42))
    assert descriptors == {}
    assert warnings


def test_one_bad_entry_point_does_not_block_others() -> None:
    descriptors, warnings = descriptor_discovery(lambda: entries(Broken, Echo))
    assert (Echo.__module__, "echo-factory") in descriptors


def static_descriptor(**overrides):
    fields = dict(
        name="x",
        version="1.0.0",
        module="m",
        factory="f",
        specification="s",
        description=DESCRIPTION,
    )
    fields.update(overrides)
    from langharness_plugin.registry import PluginDescriptor

    return PluginDescriptor(**fields)


def test_package_validation_requires_static_descriptor() -> None:
    package = PluginPackage(
        "p", "1.0.0", (PluginContribution("c", "server", static_descriptor()),)
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert isinstance(result, DiscoveryResult)
    assert result.failures == ()


def test_package_catalog_allows_duplicate_names_different_factories() -> None:
    first = static_descriptor(name="same", module="m.a", factory="f-a")
    second = static_descriptor(name="same", module="m.b", factory="f-b")
    package = PluginPackage(
        "p",
        "1.0.0",
        (
            PluginContribution("a", "server", first),
            PluginContribution("b", "server", second),
        ),
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert len(result.packages) == 1
    assert len(result.packages[0].contributions) == 2


def test_package_catalog_allows_same_factory_within_package() -> None:
    # One package may contribute two install requests over the same
    # definition: a template install plus an agent-instance request.
    first = static_descriptor(name="a", module="m", factory="f")
    second = static_descriptor(name="b", module="m", factory="f")
    package = PluginPackage(
        "p",
        "1.0.0",
        (
            PluginContribution("a", "agent", first),
            PluginContribution("b", "agent_instance", second),
        ),
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert len(result.packages) == 1
    assert len(result.packages[0].contributions) == 2


def test_package_catalog_allows_same_factory_across_packages() -> None:
    # Two packages may contribute install requests over the same definition
    # (e.g. a server install plus an agent-instance request); the registry
    # rejects real identity conflicts at install time.
    first = static_descriptor(name="a", module="m", factory="f")
    second = static_descriptor(name="b", module="m", factory="f")
    package_a = PluginPackage(
        "p.a", "1.0.0", (PluginContribution("a", "server", first),)
    )
    package_b = PluginPackage(
        "p.b", "1.0.0", (PluginContribution("b", "agent_instance", second),)
    )
    discovery = PluginDiscovery(
        lambda: entries(lambda: package_a, lambda: package_b)
    )
    result = discovery.scan()
    assert len(result.packages) == 2
    assert result.failures == ()


def test_discover_raises_on_failures() -> None:
    from langharness_plugin.discovery import PluginDiscoveryError

    discovery = PluginDiscovery(lambda: entries(lambda: 42))
    with pytest.raises(PluginDiscoveryError):
        discovery.discover()


def test_scan_reports_non_callable_entry_points() -> None:
    discovery = PluginDiscovery(lambda: entries(42))
    result = discovery.scan()
    assert result.packages == ()
    assert result.failures
    assert "callable" in result.failures[0].detail


def test_scan_reports_non_package_results() -> None:
    discovery = PluginDiscovery(lambda: entries(lambda: "not-a-package"))
    result = discovery.scan()
    assert result.packages == ()
    assert result.failures


def test_scan_reports_empty_package_identity() -> None:
    package = PluginPackage("", "", ())
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert result.failures


def test_scan_reports_duplicate_contribution_ids() -> None:
    package = PluginPackage(
        "p", "1.0.0",
        (
            PluginContribution("dup", "server", static_descriptor(factory="f-a")),
            PluginContribution("dup", "server", static_descriptor(factory="f-b")),
        ),
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert result.failures


def test_scan_reports_invalid_targets() -> None:
    package = PluginPackage(
        "p", "1.0.0", (PluginContribution("c", "nowhere", static_descriptor()),)
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert result.failures


def test_scan_reports_tool_exports_with_wrong_specification() -> None:
    from langharness_plugin.package import ToolExport

    package = PluginPackage(
        "p", "1.0.0",
        (
            PluginContribution(
                "c", "server", static_descriptor(),
                tool_exports=(ToolExport("t", "d", "m", object),),
            ),
        ),
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert result.failures


def test_scan_reports_duplicate_package_ids() -> None:
    package = PluginPackage(
        "p", "1.0.0", (PluginContribution("c", "server", static_descriptor()),)
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package, lambda: package))
    with pytest.raises(Exception, match="[Dd]uplicate"):
        discovery.scan()


def test_scan_reports_invalid_descriptors_as_failures() -> None:
    bad = static_descriptor(description="")
    package = PluginPackage(
        "p", "1.0.0", (PluginContribution("c", "server", bad),)
    )
    discovery = PluginDiscovery(lambda: entries(lambda: package))
    result = discovery.scan()
    assert result.packages == ()
    assert result.failures
