"""One-shot Node process runtime for PTC programs."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import time
from collections.abc import Callable, Mapping
from importlib.resources import files
from typing import Any

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import PTCRuntimeProvider

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000
MAX_OUTPUT_BYTES = 65_536
MAX_PROTOCOL_LINE_BYTES = 1_048_576
KILL_GRACE_SECONDS = 0.5


def _encode_line(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()


@ComponentFactory("ptc-runtime-plugin-factory")
@Provides(PTCRuntimeProvider)
@Property("_plugin_name", "plugin.name", "ptc-runtime")
@Property("_plugin_version", "plugin.version", "1.0.0")
class NodePTCRuntime:
    """Runs one JavaScript/TypeScript program in a fresh Node process."""

    def __init__(self) -> None:
        self._plugin_name = "ptc-runtime"
        self._plugin_version = "1.0.0"

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    async def execute(
        self,
        *,
        code: str,
        sdk: str,
        dispatch: Callable[[str, Any], Any],
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> dict[str, Any]:
        if not code.strip():
            raise ValueError("PTC code must not be empty")
        if timeout_ms <= 0:
            raise ValueError("PTC timeout_ms must be positive")
        deadline_ms = min(int(timeout_ms), MAX_TIMEOUT_MS)
        bootstrap = str(
            files("langharness_core.plugins.presentation").joinpath(
                "node_bootstrap.mjs"
            )
        )
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            "node",
            "--experimental-strip-types",
            bootstrap,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        logs: list[str] = []
        output_bytes = 0
        stderr_task = asyncio.create_task(process.stderr.read(MAX_OUTPUT_BYTES + 1))
        try:
            process.stdin.write(
                _encode_line({"type": "start", "code": code, "sdk": sdk})
            )
            await process.stdin.drain()
            async with asyncio.timeout(deadline_ms / 1000):
                while True:
                    try:
                        line = await process.stdout.readline()
                    except ValueError as exc:
                        raise OverflowError(
                            "PTC runtime output limit exceeded"
                        ) from exc
                    if not line:
                        await process.wait()
                        stderr = (await stderr_task).decode(errors="replace")
                        raise RuntimeError(
                            f"PTC runtime exited without a result ({process.returncode}): "
                            f"{stderr[:1000]}"
                        )
                    output_bytes += len(line)
                    if output_bytes > MAX_OUTPUT_BYTES:
                        raise OverflowError("PTC runtime output limit exceeded")
                    if len(line) > MAX_PROTOCOL_LINE_BYTES:
                        raise OverflowError("PTC runtime protocol line is too large")
                    message = json.loads(line)
                    kind = message.get("type")
                    if kind == "tool_call":
                        await self._dispatch_call(process, message, dispatch)
                    elif kind == "done":
                        process.stdin.close()
                        await process.stdin.wait_closed()
                        await process.wait()
                        stderr_task.cancel()
                        if message.get("error"):
                            error = message["error"]
                            return {
                                "logs": logs + list(message.get("logs") or []),
                                "error": error,
                                "isolation": "process-only",
                                "duration_ms": int((time.monotonic() - started) * 1000),
                            }
                        return {
                            "logs": logs + list(message.get("logs") or []),
                            "result": message.get("result"),
                            "isolation": "process-only",
                            "duration_ms": int((time.monotonic() - started) * 1000),
                        }
                    elif kind == "log":
                        logs.append(str(message.get("text", "")))
                    else:
                        raise ValueError(f"Unknown PTC protocol message: {kind!r}")
        except TimeoutError:
            await self._terminate(process)
            return {
                "logs": logs,
                "error": {"name": "TimeoutError", "message": "PTC deadline exceeded"},
                "isolation": "process-only",
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        except asyncio.CancelledError:
            await self._terminate(process)
            raise
        except BaseException:
            await self._terminate(process)
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()

    async def _dispatch_call(
        self,
        process: asyncio.subprocess.Process,
        message: dict[str, Any],
        dispatch: Callable[[str, Any], Any],
    ) -> None:
        assert process.stdin is not None
        call_id = str(message.get("id", ""))
        try:
            if inspect.iscoroutinefunction(dispatch):
                result = await dispatch(
                    str(message.get("name", "")), message.get("args")
                )
            else:
                result = await asyncio.to_thread(
                    dispatch, str(message.get("name", "")), message.get("args")
                )
                if inspect.isawaitable(result):
                    result = await result
            response: dict[str, Any] = {
                "type": "tool_result",
                "id": call_id,
                "result": result,
            }
        except Exception as exc:
            response = {
                "type": "tool_result",
                "id": call_id,
                "error": {"code": "tool_call_failed", "message": str(exc)},
            }
        process.stdin.write(_encode_line(response))
        await process.stdin.drain()

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_SECONDS)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
