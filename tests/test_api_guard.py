"""Unit tests for the API-server guard owned by the unified bootstrap."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

import os
import signal
import sys

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

import langharness.api_guard as api_guard_module
from langharness.api_guard import APIGuard


def test_api_guard_is_running_accepts_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 401

    monkeypatch.setattr(api_guard_module.httpx, "get", lambda *a, **k: Response())
    assert APIGuard().is_running() is True


def test_api_guard_is_running_rejects_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 503

    monkeypatch.setattr(api_guard_module.httpx, "get", lambda *a, **k: Response())
    assert APIGuard().is_running() is False


def test_api_guard_starts_server_with_auto_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class FakeProcess:
        def terminate(self) -> None:
            pass

        def poll(self) -> None:
            return None

    process = FakeProcess()
    commands = []
    monkeypatch.setattr(
        api_guard_module.subprocess,
        "Popen",
        lambda command: commands.append(command) or process,
    )
    monkeypatch.setattr(api_guard_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(api_guard_module.time, "monotonic", lambda: 0.0)
    exits = []
    monkeypatch.setattr(api_guard_module.atexit, "register", exits.append)

    guard = APIGuard(config_dir=str(tmp_path))
    states = [False, False, True]
    guard.is_running = lambda: states.pop(0)  # type: ignore[method-assign]
    guard.ensure_api_server()
    assert guard._process is process
    assert commands == [
        [
            sys.executable,
            "-m",
            "langharness",
            "--mode",
            "server",
            "--server-ip",
            "127.0.0.1",
            "--server-port",
            "11534",
            "--auto-shutdown",
            "--config-dir",
            str(tmp_path),
        ]
    ]
    assert exits == []


def test_api_guard_frozen_spawns_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class FakeProcess:
        def terminate(self) -> None:
            pass

        def poll(self) -> None:
            return None

    commands = []
    monkeypatch.setattr(
        api_guard_module.subprocess,
        "Popen",
        lambda command: commands.append(command) or FakeProcess(),
    )
    monkeypatch.setattr(api_guard_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(api_guard_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(api_guard_module.time, "monotonic", lambda: 0.0)

    guard = APIGuard(config_dir=str(tmp_path))
    states = [False, False, True]
    guard.is_running = lambda: states.pop(0)  # type: ignore[method-assign]
    guard.ensure_api_server()
    assert commands == [
        [
            sys.executable,
            "--mode",
            "server",
            "--server-ip",
            "127.0.0.1",
            "--server-port",
            "11534",
            "--auto-shutdown",
            "--config-dir",
            str(tmp_path),
        ]
    ]


def test_api_guard_waits_out_stale_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    lock = tmp_path / "api_server.lock"
    lock.write_text("99999999")  # pid far beyond pid_max: always dead
    monkeypatch.setattr(
        api_guard_module.subprocess,
        "Popen",
        lambda command: type(
            "P",
            (),
            {
                "terminate": lambda self: None,
                "poll": lambda self: None,
            },
        )(),
    )
    monkeypatch.setattr(api_guard_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(api_guard_module.time, "monotonic", lambda: 0.0)

    guard = APIGuard(config_dir=str(tmp_path))
    states = [False, False, True]
    guard.is_running = lambda: states.pop(0)  # type: ignore[method-assign]
    guard.ensure_api_server()
    assert not lock.exists()  # stale lock removed, then released on success


def test_api_guard_raises_when_lock_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    lock = tmp_path / "api_server.lock"
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(api_guard_module.time, "sleep", lambda _: None)
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(api_guard_module.time, "monotonic", lambda: next(ticks))

    guard = APIGuard(config_dir=str(tmp_path), startup_timeout=0.1)
    guard.is_running = lambda: False  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="another client"):
        guard.ensure_api_server()


def test_api_guard_terminates_on_startup_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

        def poll(self) -> None:
            return None

    process = FakeProcess()
    monkeypatch.setattr(
        api_guard_module.subprocess, "Popen", lambda command: process
    )
    monkeypatch.setattr(api_guard_module.time, "sleep", lambda _: None)
    ticks = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(api_guard_module.time, "monotonic", lambda: next(ticks))

    guard = APIGuard(config_dir=str(tmp_path))
    guard.is_running = lambda: False  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="did not become ready"):
        guard.ensure_api_server()
    assert process.terminated is True


def test_api_guard_attach_and_detach_lifeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWS:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

        def __iter__(self):
            raise ConnectionClosedError(None, None)

    fake_ws = FakeWS()
    connected = []
    monkeypatch.setattr(
        api_guard_module,
        "connect",
        lambda url, **kwargs: connected.append((url, kwargs)) or fake_ws,
    )
    exits = []
    monkeypatch.setattr(api_guard_module.atexit, "register", exits.append)

    guard = APIGuard("http://127.0.0.1:9123", token="tok")
    guard.attach_client()

    url, kwargs = connected[0]
    assert url == "ws://127.0.0.1:9123/clients/attach"
    assert kwargs["additional_headers"] == {"Authorization": "Bearer tok"}
    assert exits == [guard.detach_client]

    guard.detach_client()
    assert fake_ws.closed is True
    guard.detach_client()  # idempotent


def test_api_guard_attach_failure_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url, **kwargs):
        raise OSError("refused")

    monkeypatch.setattr(api_guard_module, "connect", boom)
    guard = APIGuard()
    guard.attach_client()
    assert guard._ws is None


@pytest.mark.parametrize(
    "exception",
    [ConnectionClosedOK(None, None), ConnectionClosedError(None, None)],
)
def test_api_guard_drain_handles_closed_lifeline(
    monkeypatch: pytest.MonkeyPatch, exception
) -> None:
    class FakeWS:
        def close(self) -> None:
            pass

        def __iter__(self):
            raise exception

    monkeypatch.setattr(api_guard_module, "connect", lambda url, **kwargs: FakeWS())
    guard = APIGuard()
    guard.attach_client()
    assert guard._ws is not None


def test_api_guard_signal_handler_detaches_and_reinvokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingWS:
        def close(self) -> None:
            pass

        def __iter__(self):
            return iter(())

    handlers: dict = {}
    monkeypatch.setattr(
        api_guard_module.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    sent = []
    monkeypatch.setattr(
        api_guard_module.os,
        "kill",
        lambda pid, signum: sent.append((pid, signum)),
    )
    monkeypatch.setattr(api_guard_module.atexit, "register", lambda fn: None)
    monkeypatch.setattr(
        api_guard_module, "connect", lambda url, **kwargs: BlockingWS()
    )

    guard = APIGuard()
    guard.attach_client()

    handler = handlers[signal.SIGINT]
    handler(signal.SIGINT, None)
    assert sent == [(os.getpid(), signal.SIGINT)]
