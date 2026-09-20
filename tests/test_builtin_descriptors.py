"""Every builtin package descriptor conforms to the seven-field contract."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_api.plugin import builtin_package as api_package
from langharness_cli.plugin import builtin_package as cli_package
from langharness_config.plugin import builtin_package as config_package
from langharness_core.plugin import builtin_package as core_package
from langharness_logging.plugin import builtin_package as log_package
from langharness_plugin.registry import validate_descriptor


@pytest.mark.parametrize(
    "package",
    [api_package(), cli_package(), config_package(), core_package(), log_package()],
)
def test_all_builtin_descriptors_are_valid_static_definitions(package) -> None:
    for contribution in package.contributions:
        descriptor = contribution.descriptor
        validate_descriptor(descriptor)
        assert descriptor.description.strip()
        assert not hasattr(descriptor, "instance")
        assert not hasattr(descriptor, "scope")
        assert not hasattr(descriptor, "properties")


def test_logging_package_has_two_instances_of_one_definition() -> None:
    package = log_package()
    factories = {item.descriptor.factory for item in package.contributions}
    assert factories == {"file-log-plugin-factory"}
    assert {item.id for item in package.contributions} == {"server-log", "cli-log"}


def test_config_descriptors_are_static_only() -> None:
    from langharness_config.plugin import config_descriptors

    for descriptor in config_descriptors():
        validate_descriptor(descriptor)
