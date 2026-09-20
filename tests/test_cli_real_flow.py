"""Real CLI integration tests for dynamic plugins and LLM flows."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fixtures.dynamic_plugin import create_dynamic_plugin
from fixtures.fake_llm_server import FakeLLMServer
from langharness_core.common.ids import thread_key

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLI = Path(sys.executable).with_name("langharness")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def cli_command(*args: str) -> list[str]:
    if CLI.exists():
        return [str(CLI), *args]
    return [sys.executable, "-m", "langharness_cli", *args]


def make_env(tmp_path: Path, plugin_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    paths = [str(plugin_root), str(SRC)]
    if existing:
        paths.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["LANG_HARNESS_DIR"] = str(tmp_path)
    return env


def wait_for_health(base_url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=1.0)
            if response.status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    return False


@contextmanager
def running_api(
    tmp_path: Path, plugin_root: Path
) -> Iterator[tuple[str, int, subprocess.Popen[str]]]:
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "langharness_api.common.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=make_env(tmp_path, plugin_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        if not wait_for_health(base_url):
            raise RuntimeError("API server did not become ready")
        yield base_url, port, process
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def run_cli(
    tmp_path: Path,
    plugin_root: Path,
    args: list[str],
    *,
    input_text: str | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cli_command("--dir", str(tmp_path), *args),
        cwd=ROOT,
        env=make_env(tmp_path, plugin_root),
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer secret"}


def runtime_plugins(base_url: str) -> list[dict[str, Any]]:
    response = httpx.get(
        f"{base_url}/plugins/runtime",
        headers=auth_headers(),
        timeout=10.0,
    )
    response.raise_for_status()
    return list(response.json().get("plugins") or [])


def write_fake_provider(tmp_path: Path, fake: FakeLLMServer) -> None:
    (tmp_path / "langharness.toml").write_text(
        "\n".join(
            [
                "[providers.fake]",
                'protocol = "chat"',
                f'base_url = "{fake.base_url}"',
                'model = "fake-model"',
                'api_key = "test-key"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def create_agent(base_url: str, agent_id: str, name: str) -> None:
    response = httpx.post(
        f"{base_url}/agents",
        headers=auth_headers(),
        json={"id": agent_id, "name": name, "description": f"{name} description"},
        timeout=10.0,
    )
    response.raise_for_status()


def run_interactive(
    tmp_path: Path,
    plugin_root: Path,
    port: int,
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    lines: list[str],
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        tmp_path,
        plugin_root,
        [
            "--provider",
            "fake",
            "interactive",
            "--server-port",
            str(port),
            "--token",
            "secret",
            "--user-id",
            user_id,
            "--agent-id",
            agent_id,
            "--session-id",
            session_id,
        ],
        input_text="\n".join([*lines, "exit", ""]),
        timeout=timeout,
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict)
        )
    return str(content or "")


def _request_by_last_user(
    requests: list[dict[str, Any]], last_user: str
) -> dict[str, Any]:
    for request in requests:
        user_messages = [
            message
            for message in request.get("messages") or []
            if message.get("role") == "user"
        ]
        if user_messages and _message_text(user_messages[-1]) == last_user:
            return request
    raise AssertionError(f"Request ending with {last_user!r} not found")


def _user_texts(request: dict[str, Any]) -> list[str]:
    return [
        _message_text(message)
        for message in request.get("messages") or []
        if message.get("role") == "user"
    ]


async def _checkpoint_texts(path: Path, thread: str) -> list[str]:
    connection = aiosqlite.connect(path)
    saver = AsyncSqliteSaver(connection)
    try:
        checkpoint = await saver.aget_tuple(
            {"configurable": {"thread_id": thread}}
        )
    finally:
        await connection.close()
    if checkpoint is None:
        return []
    messages = checkpoint.checkpoint["channel_values"]["messages"]
    return [str(getattr(message, "content", "")) for message in messages]


async def _session_rows(path: Path, user_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(path) as connection:
        connection.row_factory = aiosqlite.Row
        async with connection.execute(
            "SELECT user_id, session_id, turns, last_agent_id, agents_used "
            "FROM sessions WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def test_real_cli_dynamic_discover_install_enable_disable_uninstall(
    tmp_path: Path,
) -> None:
    plugin_root = create_dynamic_plugin(tmp_path / "dynamic")

    with running_api(tmp_path, plugin_root) as (base_url, port, _server):
        discover = run_cli(
            tmp_path,
            plugin_root,
            ["plugins", "discover", "--server-port", str(port), "--token", "secret"],
        )
        assert discover.returncode == 0, discover.stderr
        # Non-interactive discover renders a rich table, not JSON.
        assert "Discovered plugins" in discover.stdout
        assert "real.echo" in discover.stdout

        install = run_cli(
            tmp_path,
            plugin_root,
            [
                "plugins",
                "install",
                "real.echo",
                "echo",
                "--scope",
                "server",
                "--server-port",
                str(port),
                "--token",
                "secret",
            ],
        )
        assert install.returncode == 0, install.stderr
        assert "Plugin result" in install.stdout
        assert "real-echo" in install.stdout
        assert "server" in install.stdout
        assert any(
        item.get("factory") == "real-echo-factory"
        for item in runtime_plugins(base_url)
    )

        disable = run_cli(
            tmp_path,
            plugin_root,
            [
                "plugins",
                "disable",
                "real-echo",
                "--scope",
                "server",
                "--server-port",
                str(port),
                "--token",
                "secret",
            ],
        )
        assert disable.returncode == 0, disable.stderr
        assert "disabled" in disable.stdout
        disabled = next(
        item for item in runtime_plugins(base_url)
        if item.get("factory") == "real-echo-factory"
    )
        assert disabled.get("enabled") is False

        enable = run_cli(
            tmp_path,
            plugin_root,
            [
                "plugins",
                "enable",
                "real-echo",
                "--scope",
                "server",
                "--server-port",
                str(port),
                "--token",
                "secret",
            ],
        )
        assert enable.returncode == 0, enable.stderr
        assert "enabled" in enable.stdout
        enabled = next(
        item for item in runtime_plugins(base_url)
        if item.get("factory") == "real-echo-factory"
    )
        assert enabled.get("enabled") is True

        uninstall = run_cli(
            tmp_path,
            plugin_root,
            [
                "plugins",
                "uninstall",
                "real-echo",
                "--scope",
                "server",
                "--server-port",
                str(port),
                "--token",
                "secret",
            ],
        )
        assert uninstall.returncode == 0, uninstall.stderr
        assert "Plugin result" in uninstall.stdout
        assert all(
        item.get("factory") != "real-echo-factory"
        for item in runtime_plugins(base_url)
    )


def test_real_cli_llm_tool_call_with_dynamic_echo(tmp_path: Path) -> None:
    plugin_root = create_dynamic_plugin(tmp_path / "dynamic")
    fake = FakeLLMServer()
    fake.start()
    try:
        write_fake_provider(tmp_path, fake)
        with running_api(tmp_path, plugin_root) as (base_url, port, _server):
            install = httpx.post(
                f"{base_url}/plugins/install",
                headers=auth_headers(),
                json={
                    "package_id": "real.echo",
                    "contribution_id": "echo",
                    "scope_id": "server",
                },
                timeout=10.0,
            )
            install.raise_for_status()

            result = run_interactive(
                tmp_path,
                plugin_root,
                port,
                user_id="tool_user",
                agent_id="simple_agent",
                session_id="tool_session",
                lines=["USE_TOOL please"],
            )

            assert result.returncode == 0, result.stderr
            assert any(
                message.get("role") == "tool"
                for message in fake.messages
            )
            assert "final:" in result.stdout
    finally:
        fake.stop()


def test_real_cli_memory_isolated_by_session(tmp_path: Path) -> None:
    plugin_root = create_dynamic_plugin(tmp_path / "dynamic")
    fake = FakeLLMServer()
    fake.start()
    try:
        write_fake_provider(tmp_path, fake)
        with running_api(tmp_path, plugin_root) as (base_url, port, _server):
            first = run_interactive(
                tmp_path,
                plugin_root,
                port,
                user_id="memory_user",
                agent_id="simple_agent",
                session_id="session-a",
                lines=["alpha-session-one", "What did I say?"],
            )
            assert first.returncode == 0, first.stderr

            second = run_interactive(
                tmp_path,
                plugin_root,
                port,
                user_id="memory_user",
                agent_id="simple_agent",
                session_id="session-b",
                lines=["Do you know the previous session?"],
            )
            assert second.returncode == 0, second.stderr

        same_session_request = _request_by_last_user(fake.requests, "What did I say?")
        assert "alpha-session-one" in _user_texts(same_session_request)

        other_session_request = _request_by_last_user(
            fake.requests, "Do you know the previous session?"
        )
        assert "alpha-session-one" not in _user_texts(other_session_request)

        checkpoint_path = tmp_path / "langharness_checkpoints.sqlite3"
        session_a_texts = asyncio.run(
            _checkpoint_texts(
                checkpoint_path,
                thread_key("memory_user", "session-a", "simple_agent"),
            )
        )
        session_b_texts = asyncio.run(
            _checkpoint_texts(
                checkpoint_path,
                thread_key("memory_user", "session-b", "simple_agent"),
            )
        )
        assert "alpha-session-one" in session_a_texts
        assert "alpha-session-one" not in session_b_texts
    finally:
        fake.stop()


def test_real_cli_memory_isolated_by_agent_and_session_index_tracks_agents(
    tmp_path: Path,
) -> None:
    plugin_root = create_dynamic_plugin(tmp_path / "dynamic")
    fake = FakeLLMServer()
    fake.start()
    try:
        write_fake_provider(tmp_path, fake)
        with running_api(tmp_path, plugin_root) as (base_url, port, _server):
            create_agent(base_url, "agent_b", "Agent B")

            result = run_interactive(
                tmp_path,
                plugin_root,
                port,
                user_id="agent_memory_user",
                agent_id="simple_agent",
                session_id="shared-session",
                lines=[
                    "alpha-agent-a-secret",
                    "/agent agent_b",
                    "Do you know agent-a-secret?",
                ],
            )
            assert result.returncode == 0, result.stderr

        switched_request = _request_by_last_user(
            fake.requests, "Do you know agent-a-secret?"
        )
        assert "alpha-agent-a-secret" not in _user_texts(switched_request)

        checkpoint_path = tmp_path / "langharness_checkpoints.sqlite3"
        agent_a_texts = asyncio.run(
            _checkpoint_texts(
                checkpoint_path,
                thread_key(
                    "agent_memory_user", "shared-session", "simple_agent"
                ),
            )
        )
        agent_b_texts = asyncio.run(
            _checkpoint_texts(
                checkpoint_path,
                thread_key("agent_memory_user", "shared-session", "agent_b"),
            )
        )
        assert "alpha-agent-a-secret" in agent_a_texts
        assert "alpha-agent-a-secret" not in agent_b_texts

        session_rows = asyncio.run(
            _session_rows(tmp_path / "sessions.sqlite3", "agent_memory_user")
        )
        assert len(session_rows) == 1
        assert session_rows[0]["session_id"] == "shared-session"
        assert session_rows[0]["last_agent_id"] == "agent_b"
        assert "simple_agent" in json.loads(session_rows[0]["agents_used"])
        assert "agent_b" in json.loads(session_rows[0]["agents_used"])
    finally:
        fake.stop()


@pytest.mark.real_llm
def test_real_llm_smoke(tmp_path: Path) -> None:
    provider = os.environ.get("LANG_HARNESS_REAL_LLM_PROVIDER")
    if not provider:
        pytest.skip("set LANG_HARNESS_REAL_LLM_PROVIDER to run real LLM smoke")

    source = Path.home() / ".langharness" / "langharness.toml"
    if not source.exists():
        pytest.skip(f"real provider config not found: {source}")
    shutil.copy(source, tmp_path / "langharness.toml")

    plugin_root = create_dynamic_plugin(tmp_path / "dynamic")
    with running_api(tmp_path, plugin_root) as (base_url, port, _server):
        result = run_cli(
            tmp_path,
            plugin_root,
            [
                "--provider",
                provider,
                "interactive",
                "--server-port",
                str(port),
                "--token",
                "secret",
                "--user-id",
                "real_user",
                "--agent-id",
                "simple_agent",
                "--session-id",
                "real-session",
            ],
            input_text="say langharness-ok\nexit\n",
            timeout=120.0,
        )
        assert result.returncode == 0, result.stderr
        assert "Agent stream failed" not in result.stdout
        assert "Agent stream failed" not in result.stderr
