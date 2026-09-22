"""Tests for the per-step prompt middleware."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import SystemMessage

from langharness_core.plugins.prompts.middleware import PromptMiddleware
from langharness_core.plugins.prompts.sections import AssembledPrompt


class FakeRequest:
    """Stands in for ``ModelRequest``; only ``override`` is exercised here."""

    def __init__(self, state: dict[str, Any], system_message: Any = None) -> None:
        self.state = state
        self.system_message = system_message

    def override(self, **overrides: Any) -> FakeRequest:
        return FakeRequest(
            self.state, overrides.get("system_message", self.system_message)
        )


def request(system_message: Any = None) -> FakeRequest:
    return FakeRequest({"messages": []}, system_message)


def recording_handler(seen: list) -> Any:
    def handler(req: FakeRequest) -> str:
        seen.append(req.system_message)
        return "response"

    return handler


def build(text: str, empty: bool = False) -> Any:
    def builder(state: Any) -> AssembledPrompt:
        return AssembledPrompt(text="" if empty else text, traces=())

    return builder


def test_sync_path_sets_the_system_message() -> None:
    seen: list = []
    middleware = PromptMiddleware(build("assembled"))
    assert middleware.wrap_model_call(request(), recording_handler(seen)) == "response"
    assert seen[0] == SystemMessage(content="assembled")


def test_async_path_sets_the_system_message() -> None:
    seen: list = []

    async def handler(req: FakeRequest) -> str:
        seen.append(req.system_message)
        return "response"

    middleware = PromptMiddleware(build("assembled"))
    assert asyncio.run(middleware.awrap_model_call(request(), handler)) == "response"
    assert seen[0] == SystemMessage(content="assembled")


def test_empty_prompt_leaves_the_request_untouched() -> None:
    seen: list = []
    existing = SystemMessage(content="pre-existing")
    middleware = PromptMiddleware(build("", empty=True))
    middleware.wrap_model_call(
        request(system_message=existing), recording_handler(seen)
    )
    assert seen[0] is existing


def test_non_empty_prompt_replaces_an_existing_system_message() -> None:
    seen: list = []
    middleware = PromptMiddleware(build("assembled"))
    middleware.wrap_model_call(
        request(system_message=SystemMessage(content="old")), recording_handler(seen)
    )
    assert seen[0] == SystemMessage(content="assembled")
