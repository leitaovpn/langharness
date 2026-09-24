"""Tests for the optional reviewer capability."""

from langharness_core.contracts import ReviewerProvider
from langharness_core.plugin import agent_plugin_template_descriptor
from langharness_core.plugins.reviewer.provider import OptionalReviewer
from langharness_plugin.validation import validate


def test_reviewer_is_disabled_and_never_claims_completion() -> None:
    reviewer = OptionalReviewer()

    assert validate(reviewer, ReviewerProvider) == ()
    assert reviewer.is_enabled() is False
    assert reviewer.review({"goal": "x"}) == {
        "status": "disabled",
        "independent": True,
        "completion_authority": "verifier",
    }


def test_enabled_placeholder_only_returns_independent_metadata() -> None:
    reviewer = OptionalReviewer()
    reviewer._enabled = True

    result = reviewer.review({"goal": "x", "artifact": "a"})

    assert result["status"] == "unimplemented"
    assert result["completion_authority"] == "verifier"
    assert result["subject_keys"] == ["artifact", "goal"]


def test_reviewer_is_catalogued_as_an_optional_agent_plugin() -> None:
    descriptor = agent_plugin_template_descriptor("reviewer")
    assert descriptor.factory == "reviewer-plugin-factory"
    assert descriptor.specification == "agent.plugin.reviewer"
