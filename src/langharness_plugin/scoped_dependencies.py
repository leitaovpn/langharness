"""Class-level declaration of scoped iPOPO dependency fields."""

from __future__ import annotations

import importlib
from typing import Any

SCOPED_FIELDS_ATTR = "__scoped_dependencies__"


def ScopedDependencies(*fields: str) -> Any:
    """Class decorator marking which Requires/RequiresBest fields are scoped.

    Only declared fields receive the automatic scope visibility filter at
    instance creation; everything else keeps global service lookup.
    """

    def decorate(cls: Any) -> Any:
        setattr(cls, SCOPED_FIELDS_ATTR, frozenset(fields))
        return cls

    return decorate


def scoped_fields(cls: Any) -> frozenset[str]:
    """The scoped field names declared on one class."""
    return frozenset(getattr(cls, SCOPED_FIELDS_ATTR, ()))


def scoped_fields_from_module(module_name: str) -> frozenset[str]:
    """Discover scoped fields by importing the plugin's module.

    iPOPO decorators wrap the class in place, so scanning ``vars(module)``
    for a type carrying SCOPED_FIELDS_ATTR finds the decorated class.
    """
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return frozenset()
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == module_name:
            fields = getattr(value, SCOPED_FIELDS_ATTR, None)
            if fields is not None:
                return frozenset(fields)
    return frozenset()
