"""Real graph and SQLite checkpoint round-trip for context compression."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig

from langharness_core.plugins.checkpointer.sqlite import SQLiteCheckpointerPlugin
from langharness_core.plugins.context.middleware import (
    ContextCompressionMiddlewarePlugin,
)


class AnswerModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "context-checkpoint-test"

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def test_compressed_history_survives_sqlite_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "checkpoint.sqlite3")
    middleware = ContextCompressionMiddlewarePlugin()
    middleware._budget_bytes = 900
    first = SQLiteCheckpointerPlugin()
    first._path = path

    async def first_run() -> None:
        graph = create_agent(
            AnswerModel(), tools=[], middleware=middleware.get_middlewares(),
            checkpointer=first.get_checkpointer(),
        )
        config: RunnableConfig = {"configurable": {"thread_id": "same-thread"}}
        await graph.ainvoke({"messages": [HumanMessage(content="goal: keep this")]}, config)
        await graph.ainvoke(
            {"messages": [HumanMessage(content="old unrelated " + "x" * 2000)]},
            config,
        )
        assert first._connection is not None
        await first._connection.close()

    asyncio.run(first_run())
    second = SQLiteCheckpointerPlugin()
    second._path = path

    async def reopened() -> list[str]:
        graph = create_agent(
            AnswerModel(), tools=[], middleware=middleware.get_middlewares(),
            checkpointer=second.get_checkpointer(),
        )
        config: RunnableConfig = {"configurable": {"thread_id": "same-thread"}}
        await graph.ainvoke({"messages": [HumanMessage(content="new turn")]}, config)
        tuple_ = await second.get_checkpointer().aget_tuple(config)
        assert tuple_ is not None
        contents = [
            str(message.content)
            for message in tuple_.checkpoint["channel_values"]["messages"]
        ]
        assert second._connection is not None
        await second._connection.close()
        return contents

    contents = asyncio.run(reopened())
    assert "goal: keep this" in contents
    assert "new turn" in contents
    assert not any("old unrelated" in content for content in contents)


def test_compressed_history_stream_updates_checkpoint(tmp_path: Path) -> None:
    path = str(tmp_path / "stream-checkpoint.sqlite3")
    middleware = ContextCompressionMiddlewarePlugin()
    middleware._budget_bytes = 900
    plugin = SQLiteCheckpointerPlugin()
    plugin._path = path

    async def run() -> tuple[list[Any], list[str]]:
        graph = create_agent(
            AnswerModel(), tools=[], middleware=middleware.get_middlewares(),
            checkpointer=plugin.get_checkpointer(),
        )
        config: RunnableConfig = {"configurable": {"thread_id": "stream-thread"}}
        chunks: list[Any] = []
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="goal: stream this")]},
            config,
            stream_mode="updates",
        ):
            chunks.append(chunk)
        tuple_ = await plugin.get_checkpointer().aget_tuple(config)
        assert tuple_ is not None
        contents = [
            str(message.content)
            for message in tuple_.checkpoint["channel_values"]["messages"]
        ]
        assert plugin._connection is not None
        await plugin._connection.close()
        return chunks, contents

    chunks, contents = asyncio.run(run())
    assert chunks
    assert "goal: stream this" in contents
