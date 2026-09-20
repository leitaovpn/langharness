"""Tests for scoped dependency declarations and module introspection."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from langharness_plugin.scoped_dependencies import (
    SCOPED_FIELDS_ATTR,
    ScopedDependencies,
    scoped_fields,
    scoped_fields_from_module,
)


@ScopedDependencies("_llm_provider", "_tool_providers")
class AgentLoop:
    pass


class PlainPlugin:
    pass


def test_decorator_records_fields_as_frozenset() -> None:
    assert getattr(AgentLoop, SCOPED_FIELDS_ATTR) == frozenset(
        {"_llm_provider", "_tool_providers"}
    )
    assert scoped_fields(AgentLoop) == frozenset(
        {"_llm_provider", "_tool_providers"}
    )


def test_undecorated_class_has_no_scoped_fields() -> None:
    assert not hasattr(PlainPlugin, SCOPED_FIELDS_ATTR)
    assert scoped_fields(PlainPlugin) == frozenset()


def test_module_introspection_finds_decorated_class() -> None:
    assert scoped_fields_from_module(__name__) == frozenset(
        {"_llm_provider", "_tool_providers"}
    )


def test_module_introspection_tolerates_bad_module() -> None:
    assert scoped_fields_from_module("no.such.module.anywhere") == frozenset()
