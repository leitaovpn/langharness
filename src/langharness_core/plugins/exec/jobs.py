
"""Background commands the agent owns, and the process groups behind them.

One job is one ``Popen`` plus one reader thread. Every mutation of the job
table happens under a lock, because tools reach this from LangChain's thread
pool rather than from the event loop.
"""

from __future__ import annotations

import subprocess
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pelix.ipopo.decorators import (
    ComponentFactory,
    Invalidate,
    Property,
    Provides,
)

from langharness_core.contracts import JobManagerProvider, JobRecord, JobState
from langharness_core.plugins.exec.runner import (
    DEFAULT_ENV_ALLOWLIST,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    READ_CHUNK,
    build_environment,
    terminate_process_group,
)

DEFAULT_MAX_JOBS = 8


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Job:
    """Mutable bookkeeping for one running command."""

    def __init__(self, record: JobRecord) -> None:
        self.record = record
        self.output = bytearray()
        self.truncated = False
        self.lock = threading.Lock()
        self.process: subprocess.Popen[bytes] | None = None
        self.reader: threading.Thread | None = None
        self.stopping = False


@ComponentFactory("job-manager-plugin-factory")
@Provides(JobManagerProvider)
@Property("_plugin_name", "plugin.name", "job-manager")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_max_output_bytes", "plugin.exec.max_output_bytes", MAX_OUTPUT_BYTES)
@Property("_default_timeout", "plugin.exec.default_timeout_seconds", int(DEFAULT_TIMEOUT_SECONDS))
@Property("_max_jobs", "plugin.exec.max_jobs", DEFAULT_MAX_JOBS)
class JobManagerPlugin:
    """Starts commands, tracks their output, and reaps their process groups."""

    def __init__(self) -> None:
        self._plugin_name = "job-manager"
        self._plugin_version = "1.0.0"
        self._max_output_bytes = MAX_OUTPUT_BYTES
        self._default_timeout = int(DEFAULT_TIMEOUT_SECONDS)
        self._max_jobs = DEFAULT_MAX_JOBS
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._events: list[dict[str, Any]] = []

    def start_job(self, command: list[str], *, cwd: str, owner: str) -> JobRecord:
        argv = [str(part) for part in command]
        with self._lock:
            live = [item for item in self._jobs.values() if _is_live(item)]
            if len(live) >= self._max_jobs:
                raise RuntimeError(
                    f"too many running jobs ({len(live)} of {self._max_jobs}); "
                    "stop one before starting another"
                )
            job_id = uuid4().hex
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=build_environment(DEFAULT_ENV_ALLOWLIST),
            )
            record = JobRecord(
                id=job_id,
                command=tuple(argv),
                cwd=cwd,
                pid=process.pid,
                state="running",
                owner=owner,
                started_at=_now(),
                exit_code=None,
            )
            job = _Job(record)
            job.process = process
            job.reader = threading.Thread(
                target=self._pump, args=(job,), daemon=True
            )
            self._jobs[job_id] = job
            job.reader.start()
            self._events.append(
                {"type": "job_started", "job_id": job_id, "command": argv, "cwd": cwd}
            )
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.record if job is not None else None

    def list_jobs(self, owner: str | None = None) -> list[JobRecord]:
        with self._lock:
            records = [item.record for item in self._jobs.values()]
        if owner is not None:
            records = [item for item in records if item.owner == owner]
        return records

    def write_stdin(self, job_id: str, data: str) -> JobRecord:
        with self._lock:
            job = self._require(job_id)
            process = job.process
            if job.record.state != "running" or process is None or process.stdin is None:
                raise RuntimeError(f"job {job_id!r} is not running")
            try:
                process.stdin.write(data.encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise RuntimeError(f"job {job_id!r} closed its input: {exc}") from exc
            return job.record

    def read_output(self, job_id: str, offset: int = 0) -> tuple[str, int]:
        with self._lock:
            job = self._require(job_id)
            with job.lock:
                raw = bytes(job.output)
        text = raw.decode("utf-8", errors="replace")
        return text[max(offset, 0) :], len(text)

    def wait_job(self, job_id: str, timeout_seconds: float) -> JobRecord:
        with self._lock:
            job = self._require(job_id)
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while time.monotonic() < deadline:
            if job.record.state != "running":
                return job.record
            time.sleep(0.05)
        return job.record

    def stop_job(self, job_id: str) -> JobRecord:
        with self._lock:
            job = self._require(job_id)
            process = job.process
            was_running = job.record.state == "running"
            if was_running:
                # Record the intent before killing anything: the reader thread
                # sees the closed pipe and settles first, and a deliberate stop
                # must not be filed as an ordinary exit.
                job.stopping = True
        if process is not None and was_running:
            terminate_process_group(process)
            self._settle(job, process, "killed")
        return job.record

    def drain_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events, self._events = self._events, []
        return events

    def shutdown_jobs(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            process = job.process
            if process is not None and job.record.state == "running":
                terminate_process_group(process)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _pump(self, job: _Job) -> None:
        """Reader thread: append output until the pipe closes, respecting the cap."""
        process = job.process
        if process is None or process.stdout is None:
            return
        stream = process.stdout
        while True:
            chunk = stream.read(READ_CHUNK)
            if not chunk:
                break
            with job.lock:
                room = self._max_output_bytes - len(job.output)
                if room <= 0:
                    job.truncated = True
                    continue
                job.output.extend(chunk[:room])
                if len(chunk) > room:
                    job.truncated = True
                text = chunk[:room].decode("utf-8", errors="replace")
            if text:
                with self._lock:
                    self._events.append(
                        {"type": "job_output", "job_id": job.record.id, "text": text}
                    )
        process.wait()
        self._settle(job, process, "exited")

    def _settle(
        self, job: _Job, process: subprocess.Popen[bytes], state: JobState
    ) -> None:
        with self._lock:
            if job.record.state != "running":
                return
            if state == "exited" and job.stopping:
                state = "killed"
            job.record = JobRecord(
                id=job.record.id,
                command=job.record.command,
                cwd=job.record.cwd,
                pid=job.record.pid,
                state=state,
                owner=job.record.owner,
                started_at=job.record.started_at,
                exit_code=process.returncode,
            )
            self._events.append(
                {
                    "type": "job_finished",
                    "job_id": job.record.id,
                    "state": state,
                    "exit_code": process.returncode,
                }
            )

    def _require(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown job {job_id!r}")
        return job

    @Invalidate
    def _invalidate(self, bundle_context: Any) -> None:
        self.shutdown_jobs()


def _is_live(job: _Job) -> bool:
    return job.record.state == "running"
