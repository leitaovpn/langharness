
"""Running one argv command under a deadline, a byte cap and a process group.

Shared by the agent's own ``exec_command`` and by the host's project verifier.
The two differ only in environment: the verifier runs the project's own gates
and inherits the host environment, while anything the agent asks for is
filtered through an allowlist first.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAX_OUTPUT_BYTES = 65_536
DEFAULT_TIMEOUT_SECONDS = 120.0
KILL_GRACE_SECONDS = 3.0
READ_CHUNK = 4096

DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "PWD",
    "TZ",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "GOPATH",
    "GOROOT",
    "CARGO_HOME",
    "RUSTUP_HOME",
)
SECRET_MARKERS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What one command produced, including how it was stopped."""

    command: tuple[str, ...]
    cwd: str
    pid: int
    exit_code: int
    output: str
    truncated: bool
    duration_ms: int
    timed_out: bool


def build_environment(
    allowlist: Sequence[str] | None = DEFAULT_ENV_ALLOWLIST,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Subprocess environment: allowlisted names, never anything secret.

    Two independent gates on purpose. An allowlist alone would leak the moment
    someone adds ``MY_API_KEY`` to it, so the secret-marker check stays as a
    second line of defence.

    The allowlist is the *default* rather than an opt-in, so the safe thing
    happens when a caller says nothing. Inheriting the host environment takes
    an explicit ``None``, which only the host-side verifier should ask for: it
    runs the project's own gates and needs the environment they expect.
    """
    origin = os.environ if source is None else source
    if allowlist is None:
        return dict(origin)
    permitted: dict[str, str] = {}
    for name in allowlist:
        if name not in origin:
            continue
        if any(marker in name.upper() for marker in SECRET_MARKERS):
            continue
        permitted[name] = origin[name]
    return permitted


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the whole group, escalating to SIGKILL if it will not go.

    The command runs in its own session, so the group id is the leader's pid.
    Killing only the leader would leave any grandchildren running.
    """
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            continue
        return


def run_command(
    command: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    env: Mapping[str, str] | None = None,
) -> CommandOutcome:
    """Run one command to completion, a deadline, or a byte cap."""
    started = time.monotonic()
    argv = [str(part) for part in command]
    resolved_cwd = str(cwd)
    try:
        process = subprocess.Popen(
            argv,
            cwd=resolved_cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=None if env is None else dict(env),
        )
    except OSError as exc:
        return CommandOutcome(
            command=tuple(argv),
            cwd=resolved_cwd,
            pid=-1,
            exit_code=-1,
            output=f"command could not be run: {exc}",
            truncated=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=False,
        )
    raw, overflow, timed_out = _read_capped(process, max_output_bytes, timeout_seconds)
    exit_code = -1 if timed_out else process.returncode
    text = raw.decode("utf-8", errors="replace")
    if timed_out:
        text = f"{text}\ncommand timed out after {timeout_seconds}s"
    return CommandOutcome(
        command=tuple(argv),
        cwd=resolved_cwd,
        pid=process.pid,
        exit_code=exit_code,
        output=text,
        truncated=overflow,
        duration_ms=int((time.monotonic() - started) * 1000),
        timed_out=timed_out,
    )


def _read_capped(
    process: subprocess.Popen[bytes], cap: int, timeout_seconds: float
) -> tuple[bytes, bool, bool]:
    """Collect output until the process ends or the deadline passes.

    Returns ``(output, truncated, timed_out)``. A reader thread does the
    collecting so the wait below can honour the deadline: the alternative,
    polling the pipe on this thread, cannot distinguish "slow" from "finished"
    without more machinery than the problem deserves.
    """
    collected = bytearray()
    overflow = threading.Event()
    stream = process.stdout
    assert stream is not None

    def pump() -> None:
        while True:
            chunk = stream.read(READ_CHUNK)
            if not chunk:
                return
            room = cap - len(collected)
            if room <= 0:
                overflow.set()
                continue
            collected.extend(chunk[:room])
            if len(chunk) > room:
                overflow.set()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_group(process)
    reader.join(timeout=KILL_GRACE_SECONDS)
    return bytes(collected[:cap]), overflow.is_set(), timed_out



