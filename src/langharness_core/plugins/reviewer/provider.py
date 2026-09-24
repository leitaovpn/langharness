"""A disabled-by-default reviewer service.

The service is deliberately small: a future model-backed reviewer can replace
this implementation behind the same contract. Review output is evidence only;
the goal verifier remains the sole completion authority.
"""

from __future__ import annotations

from typing import Any

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import ReviewerProvider


@ComponentFactory("reviewer-plugin-factory")
@Provides(ReviewerProvider)
@Property("_plugin_name", "plugin.name", "reviewer")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_enabled", "plugin.reviewer.enabled", False)
class OptionalReviewer:
    """Return explicit disabled evidence unless an implementation is enabled."""

    def __init__(self) -> None:
        self._plugin_name = "reviewer"
        self._plugin_version = "1.0.0"
        self._enabled = False

    def is_enabled(self) -> bool:
        return bool(self._enabled)

    def review(self, subject: dict[str, Any]) -> dict[str, Any]:
        if not self.is_enabled():
            return {
                "status": "disabled",
                "independent": True,
                "completion_authority": "verifier",
            }
        return {
            "status": "unimplemented",
            "independent": True,
            "completion_authority": "verifier",
            "subject_keys": sorted(subject),
        }

    def get_plugin_info(self) -> dict[str, str]:
        return {
            "name": self._plugin_name,
            "version": self._plugin_version,
            "enabled": str(self.is_enabled()).lower(),
        }
