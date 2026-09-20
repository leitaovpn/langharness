"""Error types raised by plugin management and discovery."""

from __future__ import annotations


class PluginError(RuntimeError):
    """Base class for plugin management errors."""


class PluginNotFoundError(PluginError):
    """A plugin definition is not discovered or installed."""


class PluginAlreadyInstalledError(PluginError):
    """A plugin definition is already installed."""


class PluginIdentityConflictError(PluginError):
    """Two contributions claim the same factory from different modules."""


class PluginHasInstancesError(PluginError):
    """An operation requires a plugin definition without live instances."""


class AmbiguousPluginError(PluginError):
    """A name lookup matched more than one descriptor or instance."""

    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"Plugin name {name!r} is ambiguous; provide a factory, "
            f"registration key, or instance UUID. Candidates: "
            f"{', '.join(candidates)}"
        )


class InstanceNotFoundError(PluginError):
    """An instance UUID is unknown."""


class InstanceStateError(PluginError):
    """An operation is invalid for the instance's current state."""


class ScopeHasChildrenError(PluginError):
    """A scope cannot be removed while it has child scopes."""


class ScopeHasInstancesError(PluginError):
    """A scope cannot be removed while it has plugin instances."""
