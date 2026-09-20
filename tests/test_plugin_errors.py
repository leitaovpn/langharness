"""Tests for plugin management error types."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import pytest

from langharness_plugin.errors import (
    AmbiguousPluginError,
    InstanceNotFoundError,
    PluginError,
    PluginHasInstancesError,
    PluginIdentityConflictError,
    PluginNotFoundError,
    ScopeHasChildrenError,
    ScopeHasInstancesError,
)
from langharness_scope.errors import ScopeNotFoundError


def test_all_plugin_errors_subclass_plugin_error() -> None:
    errors = (
        PluginNotFoundError,
        PluginHasInstancesError,
        PluginIdentityConflictError,
        AmbiguousPluginError,
        InstanceNotFoundError,
        ScopeHasChildrenError,
        ScopeHasInstancesError,
    )
    for error in errors:
        assert issubclass(error, PluginError)


def test_scope_not_found_error_is_reusable_from_scope_module() -> None:
    with pytest.raises(ScopeNotFoundError):
        raise ScopeNotFoundError("missing")


def test_ambiguous_error_carries_candidates() -> None:
    error = AmbiguousPluginError("llm", ["a-factory", "b-factory"])
    assert error.name == "llm"
    assert error.candidates == ["a-factory", "b-factory"]
    assert "a-factory" in str(error) and "b-factory" in str(error)
