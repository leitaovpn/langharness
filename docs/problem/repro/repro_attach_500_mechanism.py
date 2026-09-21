"""Deterministic repro of the HTTP 500: shutdown drains a handshake in flight.

    python docs/problem/repro/repro_attach_500_mechanism.py

``repro_attach_race.py`` shows the failure, but only under CPU load and only
sometimes. This script pins the mechanism instead, with no load and no
langharness code: a websocket route that is slow to call ``accept()``, and a
``should_exit`` flipped while the handshake is still in flight. That is exactly
the state ``ClientRegistry``'s timer puts a fresh attach in when the grace
window expires.

Exit code 0 = the server answered 500 and logged nothing, 1 = it did not.
"""

from __future__ import annotations

import asyncio
import io
import logging
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, WebSocket
from websockets.sync.client import connect

#: How long the route waits before accepting. The shutdown lands inside this.
ACCEPT_DELAY = 1.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


app = FastAPI()


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    # A real route reaches accept() as soon as the event loop gets to it;
    # under CPU load that turns into an arbitrary delay. Sleeping makes the
    # window deterministic instead of load-dependent.
    await asyncio.sleep(ACCEPT_DELAY)
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)  # maximally sensitive: we are proving silence
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "asyncio"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    port = free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, timeout_graceful_shutdown=10
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)

    result: dict[str, str] = {}

    def client() -> None:
        try:
            with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=10) as ws:
                ws.send("ping")
            result["outcome"] = "connected"
        except Exception as exc:  # noqa: BLE001
            result["outcome"] = f"{type(exc).__module__}.{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=client)
    worker.start()
    # Let the TCP connect and the upgrade request land, then start draining.
    time.sleep(ACCEPT_DELAY / 4)
    sink.truncate(0)
    sink.seek(0)  # drop startup noise; keep only what shutdown produces
    server.should_exit = True

    worker.join(timeout=20)
    thread.join(timeout=20)

    outcome = result.get("outcome", "<client never returned>")
    logged = sink.getvalue().strip()
    print(f"client saw: {outcome}")
    print(f"server logged during shutdown: {logged!r}")
    ok = outcome.startswith("websockets.exceptions.InvalidStatus") and "500" in outcome
    ok = ok and "ASGI" not in logged and "Traceback" not in logged
    print("OK: 500 with no server-side log" if ok else "NOT REPRODUCED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
