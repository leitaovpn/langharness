"""Repro: the client lifeline attach fails with HTTP 500 under CPU load.

    python docs/problem/repro/repro_attach_race.py            # 40 rounds, 8 burners
    python docs/problem/repro/repro_attach_race.py 100 16     # rounds, burners

Mirrors ``tests/test_server_lifecycle_e2e.py::test_server_exits_after_last_client_leaves``
(spawn server with --auto-shutdown, wait for /health, attach two clients) but
keeps the server's stdout/stderr instead of DEVNULL, so the traceback behind
the 500 is visible. CPU burners widen the window between the health check and
the attach; the failure does not reproduce on an idle machine.

Exit code 0 = never failed, 1 = reproduced (and the server log is printed).
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

from websockets.sync.client import connect

ROOT = pathlib.Path(__file__).resolve().parents[3]
#: The window the test used to run with, and the one that failed: it was spent
#: before the test could attach. The test now runs at 2.0s (see ``GRACE`` in
#: tests/test_server_lifecycle_e2e.py); override with REPRO_GRACE to compare.
GRACE = float(os.environ.get("REPRO_GRACE", "0.3"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def burners(count: int) -> list[subprocess.Popen]:
    """CPU load, so the gap between the health check and the attach stretches."""
    code = "x = 0\nwhile True:\n    x = (x * 31 + 7) % 1000003\n"
    return [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL)
        for _ in range(count)
    ]


def wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("API server did not become ready")


def one_round(index: int, tmp_path: pathlib.Path) -> str | None:
    """Return the failure text, or None if the round passed."""
    port = free_port()
    env = os.environ.copy()
    env["LANG_HARNESS_DIR"] = str(tmp_path)
    paths = [str(ROOT / "src"), *env.get("PYTHONPATH", "").split(os.pathsep)]
    env["PYTHONPATH"] = os.pathsep.join(p for p in paths if p)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "langharness",
            "--mode", "server",
            "--server-ip", "127.0.0.1",
            "--server-port", str(port),
            "--config-dir", str(tmp_path),
            "--auto-shutdown", "--auto-shutdown-grace", str(GRACE),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        # The registry armed its timer at lifespan startup, before /health
        # answered. Everything from here has to fit in what is left of GRACE.
        started = time.monotonic()
        first = connect(
            f"ws://127.0.0.1:{port}/clients/attach",
            additional_headers={"Authorization": "Bearer secret"},
        )
        second = connect(
            f"ws://127.0.0.1:{port}/clients/attach",
            additional_headers={"Authorization": "Bearer secret"},
        )
        print(f"round {index}: health->attach {time.monotonic() - started:.3f}s"
              f" (grace {GRACE}s)")
        first.close()
        time.sleep(max(0.8, GRACE * 2))
        if process.poll() is not None:
            return f"round {index}: server exited while a client was attached"
        second.close()
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            return f"round {index}: server did not exit after the last client left"
        return None
    except Exception as exc:  # noqa: BLE001
        return f"round {index}: {type(exc).__module__}.{type(exc).__name__}: {exc}"
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            out = process.communicate(timeout=10)[0] or ""
        except subprocess.TimeoutExpired:
            process.kill()
            out = process.communicate(timeout=10)[0] or ""
        if out.strip():
            tail = out.rstrip().splitlines()[-40:]
            print(f"─── server output, round {index}, last {len(tail)} lines " + "─" * 30)
            print("\n".join(tail))


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    load = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    print(f"{rounds} rounds, {load} cpu burners, grace={GRACE}s", flush=True)
    jobs = burners(load)
    failures: list[str] = []
    try:
        for index in range(rounds):
            with tempfile.TemporaryDirectory(prefix="attach-race-") as work:
                failure = one_round(index, pathlib.Path(work))
            if failure:
                print(f"\nFAIL: {failure}", flush=True)
                failures.append(failure)
                if len(failures) >= 3:
                    break
            else:
                print(f"round {index}: ok", flush=True)
    finally:
        for job in jobs:
            job.kill()
    if failures:
        print(f"\nreproduced {len(failures)} time(s)")
        return 1
    print("\nnot reproduced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
