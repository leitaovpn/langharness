# Code Agent 计划 2b：命令执行与 job 控制 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 code agent 自己运行命令、测试与 lint——有 workspace cwd 边界、独立进程组、deadline、输出上限、环境白名单，以及归属到 `agent_id + session_id` 的后台 job。

**Architecture:** 新增 `agent.plugin.job_manager` 契约与 `JobManagerPlugin`（线程安全，管理 `Popen` 与读取线程）。把计划 3a `ProjectVerifier` 里的 argv 执行逻辑抽成共享的 `plugins/exec/runner.py`，`exec_command` 与验证器共用它——但**验证器继承完整环境**（它跑的是项目自己的门禁），只有 agent 侧的命令走白名单。命令在 LangChain 线程池里同步阻塞执行，事件循环不受影响（计划 2a 已验证）。

**Tech Stack:** Python 3.13、Pelix/iPOPO、`subprocess` + `os.killpg`、`threading`、pydantic `args_schema`、pytest。

## Global Constraints

- 质量门槛（AGENTS.md）：每个 commit 必须过 `make check`（Ruff → mypy → Pyright → clean-process import → pytest），覆盖率 ≥95%。**执行前基线 96.08%**。
- TDD：先写失败测试，再写最小实现；每个任务独立 commit。
- 生产代码 mypy strict、Pyright standard 不允许全局放宽。**`tests/` 不是 mypy 豁免区**：新测试文件要么全量注解，要么在 line 2 加 `# mypy: ignore-errors`，并按需补 `# pyright: report...=false`。
- **`self.x = param` 会被 Pyright 推断为 `str`**，赋给 Literal 别名时必须显式注解（2a/3a 各踩过一次）。
- 插件间通信只走 Pelix service spec（Protocol）；`@Provides`/`@Requires` 声明 Protocol 类。
- `PluginDescriptor` 只在 `langharness_core/plugin.py` 构造；组件实现在 `plugins/<topic>/<name>.py`。
- 工具异常一律走 `tool_error()`；`ToolErrorCode` 是 Literal，新增 code 必须同步加进去。
- 文档用中文，代码/命令用英文；commit 用 `feat:` / `test:` / `docs:`。
- 设计文档：`docs/designs/2026-09-22-code-agent-design.md` 第 6.4 节。
- 前置：计划 2a 与 3a 已合入本分支（`WorkspaceProvider`、`WorkspaceStateProvider`、`tool_error`、`ProjectVerifier`）。

## 开工前已验证的技术事实

1. **`start_new_session=True` + `os.killpg(pgid, SIGTERM)` 能收掉整棵进程树**：实测一个派生孙进程的命令，杀掉前进程组里有 2 个进程，SIGTERM 后为 0，leader 被正常回收。这是设计第 6.4 节"取消/超时后终止整组"的实现基础。
2. **读取线程 + 字节上限可以非阻塞地捕获后台输出**：对持续输出的进程用 `iter(lambda: pipe.read(64), b"")` 在线程里累积，到 200 字节后置 `truncated` 标记，实测捕获 200 字节、超限标记为真、进程正常退出。
3. **同步工具不会卡住事件循环**（计划 2a 已验证）：`BaseTool._arun` 把同步工具丢进 `run_in_executor` 线程池。因此 `wait_job` 可以直接阻塞等待。

## 关键设计决策

**argv 而不是 shell 字符串。** `exec_command` 只接受 argv 数组，不隐式过 shell。想要 shell 语义的模型显式写 `["bash", "-lc", "..."]`。理由是设计第 10 节对验证命令的同一条理由：不为了一次 shell 解析引入第二套引号/转义语义。

**环境白名单只作用于 agent 侧的命令。** 设计第 6.4 节要求"默认环境白名单，不把 API key 注入子进程"——那说的是 agent 能跑的命令。验证器跑的是项目自己的门禁（`make check` 之类），它需要宿主环境，所以 `run_command(env=None)` 表示继承，验证器传 `None`，`exec_command` 传白名单。把白名单强加给验证器会让真实门禁在 CI 上莫名失败。

**每条命令结束后递增 mutation generation。** 设计第 6.4 节："只读命令是否会写不能仅靠模型声明；首版将所有 shell command 保守视作可能修改 workspace"。因此在场与后台 job 结束时都递增，此前的验证报告随之失效——这正是我们想要的。

## 文件布局

**新建**

| 文件 | 职责 |
|---|---|
| `src/langharness_core/plugins/exec/__init__.py` | 包标记 |
| `src/langharness_core/plugins/exec/runner.py` | 共享 argv 执行器：进程组、deadline、输出上限、环境构造 |
| `src/langharness_core/plugins/exec/jobs.py` | `JobManagerPlugin`：前台/后台 job、归属、暂停/等待/终止、事件队列 |
| `src/langharness_core/plugins/exec/tools.py` | `ExecToolsPlugin`：`exec_command` / `write_stdin` / `wait_job` / `stop_job` |
| `tests/test_exec_runner.py`、`tests/test_job_manager.py`、`tests/test_exec_tools.py`、`tests/test_job_events.py`、`tests/test_exec_scope.py` | 对应测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/langharness_core/contracts.py` | `SPEC_JOB_MANAGER`、`JobState`、`JobRecord`、`JobManagerProvider` |
| `src/langharness_core/common/tool_error.py` | `ToolErrorCode` 增加 `job_not_found`、`job_not_running`、`timeout` |
| `src/langharness_core/plugins/verifier/project.py` | `_run` 改为调用 `runner.run_command`，行为不变（继承环境） |
| `src/langharness_core/plugins/loop/agent_loop.py` | 每轮 chunk 之间 drain job 事件 |
| `src/langharness_core/plugin.py` | catalog 增加 `exec` / `exec-tools`；`builtin_package()` 增加 template |
| `tests/test_imports.py` | `PUBLIC_MODULES` 增加新模块 |

---

### Task 1: Job 契约与值类型

**Files:**
- Modify: `src/langharness_core/contracts.py`
- Modify: `src/langharness_core/common/tool_error.py`
- Test: `tests/test_job_manager.py`（新建，本任务只写契约相关用例）

**Interfaces:**
- Produces（后续所有任务复用）：
  - `SPEC_JOB_MANAGER = "agent.plugin.job_manager"`
  - `JobState = Literal["running", "exited", "killed", "timed_out"]`
  - `JobRecord(id, command, cwd, pid, state, owner, started_at, exit_code)`
  - `JobManagerProvider` Protocol：`start_job`、`get_job`、`list_jobs`、`write_stdin`、`read_output`、`wait_job`、`stop_job`、`drain_events`、`shutdown_jobs`、`get_plugin_info`
  - `ToolErrorCode` 增加 `"job_not_found"`、`"job_not_running"`、`"timeout"`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_job_manager.py`：

```python
"""Contract tests for the job manager service."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_JOB_MANAGER,
    JobManagerProvider,
    JobRecord,
)
from langharness_plugin.validation import contract_for


def sample_job(**overrides: object) -> JobRecord:
    values: dict[str, object] = {
        "id": "j1",
        "command": ("echo", "hi"),
        "cwd": "/tmp",
        "pid": 1234,
        "state": "running",
        "owner": "alpha::s1",
        "started_at": "2026-09-22T00:00:00+00:00",
        "exit_code": None,
    }
    values.update(overrides)
    return JobRecord(**values)  # type: ignore[arg-type]


def test_specification_is_pinned() -> None:
    assert contract_for(SPEC_JOB_MANAGER) is JobManagerProvider
    assert SPEC_JOB_MANAGER in ALL_PLUGIN_SPECS


def test_job_record_is_frozen_and_hashable() -> None:
    assert sample_job() == sample_job()
    assert hash(sample_job()) == hash(sample_job())


def test_a_running_job_has_no_exit_code() -> None:
    assert sample_job().exit_code is None


def test_a_finished_job_carries_its_exit_code() -> None:
    assert sample_job(state="exited", exit_code=0).exit_code == 0


def test_the_owner_is_part_of_identity() -> None:
    assert sample_job(owner="alpha::s1") != sample_job(owner="beta::s1")


def test_job_error_codes_are_representable() -> None:
    from langharness_core.common.tool_error import tool_error

    for code in ("job_not_found", "job_not_running", "timeout"):
        assert tool_error(code, "x")["error"]["code"] == code
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_job_manager.py -q`
Expected: FAIL — `ImportError: cannot import name 'SPEC_JOB_MANAGER'`

- [ ] **Step 3: 写实现**

在 `src/langharness_core/common/tool_error.py` 的 `ToolErrorCode` 里加入三个新码（放在
`"blocker_not_established"` 之后）：

```python
    "job_not_found",
    "job_not_running",
    "timeout",
```

在 `src/langharness_core/contracts.py` 中，`SPEC_VERIFIER` 之后加：

```python
SPEC_JOB_MANAGER = "agent.plugin.job_manager"
```

把 `SPEC_JOB_MANAGER` 追加进 `ALL_PLUGIN_SPECS`（紧跟 `SPEC_VERIFIER`）。

在 `VerificationReport` 之后加值类型：

```python
JobState = Literal["running", "exited", "killed", "timed_out"]


@dataclass(frozen=True, slots=True)
class JobRecord:
    """One command the agent started, and where it got to.

    ``owner`` is ``"<agent_id>::<session_id>"``: a job belongs to the
    conversation that started it, not to the process.
    """

    id: str
    command: tuple[str, ...]
    cwd: str
    pid: int
    state: JobState
    owner: str
    started_at: str
    exit_code: int | None
```

在 `VerificationProvider` 之后加 Protocol：

```python
@service_contract(SPEC_JOB_MANAGER)
@runtime_checkable
class JobManagerProvider(Protocol):
    """Contract implemented by every ``agent.plugin.job_manager`` service."""

    def start_job(
        self, command: list[str], *, cwd: str, owner: str
    ) -> JobRecord: ...

    def get_job(self, job_id: str) -> JobRecord | None: ...

    def list_jobs(self, owner: str | None = None) -> list[JobRecord]: ...

    def write_stdin(self, job_id: str, data: str) -> JobRecord: ...

    def read_output(self, job_id: str, offset: int = 0) -> tuple[str, int]: ...

    def wait_job(self, job_id: str, timeout_seconds: float) -> JobRecord: ...

    def stop_job(self, job_id: str) -> JobRecord: ...

    def drain_events(self) -> list[dict[str, Any]]: ...

    def shutdown_jobs(self) -> None: ...

    def get_plugin_info(self) -> dict[str, str]: ...
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_job_manager.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/contracts.py src/langharness_core/common/tool_error.py \
        tests/test_job_manager.py
git commit -m "feat: add the job manager contract"
```

---

### Task 2: 共享 argv 执行器，并让验证器改用它

**Files:**
- Create: `src/langharness_core/plugins/exec/__init__.py`
- Create: `src/langharness_core/plugins/exec/runner.py`
- Modify: `src/langharness_core/plugins/verifier/project.py`
- Test: `tests/test_exec_runner.py`（新建）

**Interfaces:**
- Consumes: Task 1 无依赖。
- Produces（Task 3/4/5 复用）：
  - `runner.MAX_OUTPUT_BYTES = 65_536`
  - `runner.DEFAULT_ENV_ALLOWLIST: tuple[str, ...]`
  - `runner.SECRET_MARKERS: tuple[str, ...]`
  - `runner.build_environment(allowlist: Sequence[str] | None = None, source: Mapping[str, str] | None = None) -> dict[str, str]`
  - `runner.CommandOutcome(command, cwd, exit_code, output, truncated, duration_ms)`
  - `runner.run_command(command, *, cwd, timeout_seconds, max_output_bytes=MAX_OUTPUT_BYTES, env=None) -> CommandOutcome`
  - `runner.terminate_process_group(process: subprocess.Popen[bytes]) -> None`

**环境白名单的语义：** 只有**名字在白名单里、且不含秘密标记**的变量才传给子进程。秘密标记是
`KEY` / `TOKEN` / `SECRET` / `PASSWORD` / `CREDENTIAL`，因此即便有人把 `MY_API_KEY`
写进白名单也不会泄漏——两道判断是刻意冗余的。

`env=None` 表示继承宿主环境，验证器用它；`exec_command` 传白名单。

**这次重构不得改变验证器的可观测行为**（它跑的是项目自己的门禁，需要完整环境）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_exec_runner.py`：

```python
"""Tests for the shared argv command runner."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from langharness_core.plugins.exec import runner


def test_a_command_reports_output_and_exit_code(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "print('hello')"], cwd=tmp_path, timeout_seconds=10
    )
    assert outcome.exit_code == 0
    assert outcome.output.strip() == "hello"
    assert outcome.truncated is False
    assert outcome.duration_ms >= 0
    assert outcome.command == (sys.executable, "-c", "print('hello')")


def test_stderr_is_captured_alongside_stdout(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom')"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert "boom" in outcome.output


def test_a_non_zero_exit_is_a_result_not_an_error(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "import sys; sys.exit(4)"],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert outcome.exit_code == 4


def test_output_is_capped_and_flagged(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "print('x' * 5000)"],
        cwd=tmp_path,
        timeout_seconds=10,
        max_output_bytes=100,
    )
    assert outcome.truncated is True
    assert len(outcome.output.encode()) <= 100


def test_a_hanging_command_times_out_and_is_killed(tmp_path: Path) -> None:
    outcome = runner.run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout_seconds=0.5,
    )
    assert outcome.exit_code == -1
    assert outcome.timed_out is True
    assert "timed out" in outcome.output.lower()


def test_a_missing_binary_is_reported_not_raised(tmp_path: Path) -> None:
    outcome = runner.run_command(
        ["/nonexistent/binary"], cwd=tmp_path, timeout_seconds=5
    )
    assert outcome.exit_code == -1
    assert "could not be run" in outcome.output


def test_the_whole_process_group_dies_with_the_command(tmp_path: Path) -> None:
    # A command that leaves a grandchild behind must not leak it on timeout.
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    outcome = runner.run_command(
        [sys.executable, "-c", script], cwd=tmp_path, timeout_seconds=0.8
    )
    assert outcome.timed_out is True
    assert not _group_alive(outcome.pid)


def test_cwd_is_honoured(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    outcome = runner.run_command(
        [sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=nested,
        timeout_seconds=10,
    )
    assert Path(outcome.output.strip()).resolve() == nested.resolve()


def test_default_environment_withholds_secrets(
    monkeypatch: object,
) -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/x",
        "MY_API_KEY": "sk-secret",
        "GITHUB_TOKEN": "ghp_secret",
        "DB_PASSWORD": "hunter2",
        "SOME_CREDENTIALS": "x",
    }
    env = runner.build_environment(source=source)
    assert env == {"PATH": "/usr/bin", "HOME": "/home/x"}


def test_an_allowlisted_name_is_still_refused_when_it_looks_secret() -> None:
    env = runner.build_environment(
        ["PATH", "MY_API_KEY"], source={"PATH": "/usr/bin", "MY_API_KEY": "sk-secret"}
    )
    assert env == {"PATH": "/usr/bin"}


def test_a_runner_with_no_allowlist_inherits_everything() -> None:
    source = {"PATH": "/usr/bin", "MY_API_KEY": "sk-secret"}
    assert runner.build_environment(None, source=source) == source


def test_the_default_allowlist_covers_what_a_toolchain_needs() -> None:
    for name in ("PATH", "HOME", "LANG", "TERM", "TMPDIR", "VIRTUAL_ENV"):
        assert name in runner.DEFAULT_ENV_ALLOWLIST


def _group_alive(pid: int) -> bool:
    import subprocess

    try:
        os.getpgid(pid)
    except ProcessLookupError:
        return False
    listing = subprocess.run(
        ["pgrep", "-g", str(pid)], capture_output=True, text=True, check=False
    )
    return bool(listing.stdout.split())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_exec_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.exec'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/exec/__init__.py`：

```python
"""Command execution and job control plugins."""
```

`src/langharness_core/plugins/exec/runner.py`：

```python
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



```
把 `src/langharness_core/plugins/verifier/project.py` 的 `_run` 换成走共享执行器，
**行为保持不变**（继承环境、同样的 timeout 语义）：

```python
    def _run(self, command: list[str]) -> VerificationCheck:
        outcome = run_command(
            command,
            cwd=self._workspace.get_workspace() if self._workspace else ".",
            timeout_seconds=float(self._timeout_seconds),
        )
        self._last_output = outcome.output
        return VerificationCheck(
            name=" ".join(command),
            command=tuple(command),
            exit_code=outcome.exit_code,
            stdout_digest=digest(outcome.output),
            duration_ms=outcome.duration_ms,
        )
```

并在文件顶部加 `from langharness_core.plugins.exec.runner import run_command`。

**导入清理是明确的**：重构后 `subprocess` 与 `time` 在 `project.py` 里只剩 `_run`
在用（第 10–11 行的导入、第 135/137/147/153 行的调用），因此 `import subprocess` 与
`import time` 两行都要删掉。Ruff 的 `F401` 会把没删干净的直接拦下来。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_exec_runner.py tests/test_project_verifier.py -q`
Expected: PASS（`test_exec_runner.py` 14 passed，`test_project_verifier.py` 原有 11 个保持不变通过）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。验证器的既有测试必须**全部照旧通过**——这次重构是可观测行为等价的。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/exec src/langharness_core/plugins/verifier/project.py \
        tests/test_exec_runner.py
git commit -m "feat: share one argv runner between the agent and the verifier"
```

---

### Task 3: Job 管理器

**Files:**
- Create: `src/langharness_core/plugins/exec/jobs.py`
- Test: `tests/test_job_manager.py`（追加）

**Interfaces:**
- Consumes: Task 1 的契约；Task 2 的 `terminate_process_group`、`DEFAULT_ENV_ALLOWLIST`、`build_environment`。
- Produces（Task 4/5/6 复用）：
  - 工厂 `job-manager-plugin-factory`，模块 `langharness_core.plugins.exec.jobs`
  - 属性 `plugin.exec.max_output_bytes`（默认 65536）、`plugin.exec.default_timeout_seconds`（默认 120）、`plugin.exec.max_jobs`（默认 8）
  - `JobManagerPlugin`，实现 `JobManagerProvider`

**线程模型：** 一个后台 job = 一个 `Popen` + 一个读取线程（把输出追加进受锁保护的
buffer）。`read_output(offset)` 返回增量，`drain_events()` 返回待发事件并清空。
`shutdown_jobs()` 在组件 `@Invalidate` 时收掉所有还活着的组。

**事件形状**（设计第 9 节）：`{"type": "job_started"|"job_output"|"job_finished", "job_id", ...}`。

- [ ] **Step 1: 写失败测试**

先**扩充文件顶部的导入块**（Task 1 建的文件已有 contracts 与 validation 的导入）：

```python
import os
import sys
import time
from pathlib import Path

import pytest

from langharness_core.plugins.exec.jobs import JobManagerPlugin
```

（`subprocess` 不在这一块里：`_group_alive` 自己局部导入它，模块级再导一次会被
Ruff 判成 `F401` + `F811`。`os` 则必须是模块级的——`_group_alive` 用到它。）

再在文件**末尾追加**下面的测试函数。导入必须并进顶部那一块：Ruff 的 `E402`
（select 里的 `E4`）会拦下出现在代码之后的 import。

```python


def manager(tmp_path: Path) -> JobManagerPlugin:
    plugin = JobManagerPlugin()
    return plugin


def shell(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_command_that_exits_is_recorded_as_exited(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print('hi')"), cwd=str(tmp_path), owner="a::s")
    finished = plugin.wait_job(record.id, 10)
    assert finished.state == "exited"
    assert finished.exit_code == 0
    assert finished.owner == "a::s"


def test_output_can_be_read_incrementally(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print('one')"), cwd=str(tmp_path), owner="a::s")
    plugin.wait_job(record.id, 10)
    text, offset = plugin.read_output(record.id, 0)
    assert "one" in text
    assert offset == len(text)
    again, offset2 = plugin.read_output(record.id, offset)
    assert again == ""
    assert offset2 == offset


def test_an_unknown_job_is_none(tmp_path: Path) -> None:
    assert manager(tmp_path).get_job("nope") is None


def test_jobs_are_listed_per_owner(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    plugin.start_job(shell("print(1)"), cwd=str(tmp_path), owner="a::s")
    plugin.start_job(shell("print(2)"), cwd=str(tmp_path), owner="b::s")
    assert len(plugin.list_jobs("a::s")) == 1
    assert len(plugin.list_jobs()) == 2
    assert plugin.list_jobs("a::s")[0].owner == "a::s"


def test_stopping_a_job_kills_its_process_group(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    record = plugin.start_job(shell(script), cwd=str(tmp_path), owner="a::s")
    time.sleep(0.6)
    stopped = plugin.stop_job(record.id)
    assert stopped.state == "killed"
    time.sleep(0.3)
    assert not _group_alive(record.pid)


def test_waiting_past_the_deadline_returns_the_running_job(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(
        shell("import time; time.sleep(30)"), cwd=str(tmp_path), owner="a::s"
    )
    still = plugin.wait_job(record.id, 0.3)
    assert still.state == "running"
    plugin.stop_job(record.id)


def test_write_stdin_reaches_the_process(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(
        shell("import sys; print(sys.stdin.readline().strip())"),
        cwd=str(tmp_path),
        owner="a::s",
    )
    plugin.write_stdin(record.id, "piped\n")
    plugin.wait_job(record.id, 10)
    text, _ = plugin.read_output(record.id, 0)
    assert "piped" in text


def test_writing_to_a_finished_job_is_refused(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print(1)"), cwd=str(tmp_path), owner="a::s")
    plugin.wait_job(record.id, 10)
    with pytest.raises(RuntimeError):
        plugin.write_stdin(record.id, "late\n")


def test_unknown_jobs_raise_lookups_errors(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    with pytest.raises(KeyError):
        plugin.write_stdin("nope", "x")
    with pytest.raises(KeyError):
        plugin.wait_job("nope", 1)
    with pytest.raises(KeyError):
        plugin.stop_job("nope")


def test_events_are_queued_and_drained_once(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(shell("print('evt')"), cwd=str(tmp_path), owner="a::s")
    plugin.wait_job(record.id, 10)
    events = plugin.drain_events()
    kinds = [item["type"] for item in events]
    assert kinds[0] == "job_started"
    assert "job_finished" in kinds
    assert plugin.drain_events() == []
    assert events[0]["job_id"] == record.id


def test_the_job_cap_is_enforced(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    plugin._max_jobs = 2
    plugin.start_job(shell("import time; time.sleep(5)"), cwd=str(tmp_path), owner="a::s")
    plugin.start_job(shell("import time; time.sleep(5)"), cwd=str(tmp_path), owner="a::s")
    with pytest.raises(RuntimeError):
        plugin.start_job(shell("print(1)"), cwd=str(tmp_path), owner="a::s")
    plugin.shutdown_jobs()


def test_shutdown_kills_every_live_job(tmp_path: Path) -> None:
    plugin = manager(tmp_path)
    record = plugin.start_job(
        shell("import time; time.sleep(30)"), cwd=str(tmp_path), owner="a::s"
    )
    plugin.shutdown_jobs()
    time.sleep(0.3)
    assert not _group_alive(record.pid)
    assert plugin.list_jobs() == []


def test_the_manager_conforms_to_its_contract(tmp_path: Path) -> None:
    from langharness_core.contracts import JobManagerProvider
    from langharness_plugin.validation import validate

    assert validate(manager(tmp_path), JobManagerProvider) == ()


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert manager(tmp_path).get_plugin_info() == {
        "name": "job-manager",
        "version": "1.0.0",
    }
```

`_group_alive` 辅助函数也放在文件顶部（与 `tests/test_exec_runner.py` 中的同名实现
一致，直接复制过来），它是模块级函数，不要塞进某个测试里。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_job_manager.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.exec.jobs'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/exec/jobs.py`：

```python
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

    def snapshot(self) -> JobRecord:
        return self.record


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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_job_manager.py -q`
Expected: PASS（20 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/exec/jobs.py tests/test_job_manager.py
git commit -m "feat: manage background command jobs and their process groups"
```

---

### Task 4: `exec_command` 工具

**Files:**
- Create: `src/langharness_core/plugins/exec/tools.py`
- Test: `tests/test_exec_tools.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `run_command`、`DEFAULT_ENV_ALLOWLIST`、`build_environment`；Task 3 的 `JobManagerPlugin`；计划 2a 的 `WorkspaceProvider`、`WorkspaceStateProvider`、`resolve_workspace_path`、`tool_error`。
- Produces（Task 5 继续往同一组件加工具）：
  - 工厂 `exec-tools-plugin-factory`，模块 `langharness_core.plugins.exec.tools`
  - 属性 `plugin.exec.default_timeout_seconds`（默认 120）、`plugin.exec.max_timeout_seconds`（默认 600）
  - `ExecToolsPlugin`，`@Provides(ToolProvider)`，`@RequiresBest` job/workspace/state，`@ScopedDependencies` 同三个字段

**前台与后台：** `background=False`（默认）走 `run_command` 阻塞执行，结果直接返回；
`background=True` 交给 job manager，工具立刻返回 `job_id` 与 `state`。

**mutation generation：** 两条路径在命令结束后都调用
`WorkspaceStateProvider.record_mutation`，`path` 记命令的 workspace 相对 cwd，
`before_sha256`/`after_sha256` 都为 `None`——它表达的是"workspace 可能被改过"，
而不是某个文件的确切前后内容。

**超时上限：** `timeout_seconds` 超过 `plugin.exec.max_timeout_seconds` 时钳到上限并在
结果里说明，不报错——模型要的是命令跑完，不是为参数争辩。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_exec_tools.py`：

```python
"""Tests for the exec_command tool."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import sys
from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.exec.jobs import JobManagerPlugin
from langharness_core.plugins.exec.tools import ExecToolsPlugin
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path) -> ExecToolsPlugin:
    instance = ExecToolsPlugin()
    root = WorkspaceRootPlugin()
    root._root = str(tmp_path)
    instance._workspace = root
    instance._state = WorkspaceStatePlugin()
    instance._jobs = JobManagerPlugin()
    return instance


def tools(instance: ExecToolsPlugin) -> dict[str, object]:
    return {item.name: item for item in instance.get_tools()}


def run(instance: ExecToolsPlugin, **args: object) -> dict:
    return tools(instance)["exec_command"].invoke(args)


def test_no_workspace_means_no_tools() -> None:
    assert ExecToolsPlugin().get_tools() == []


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_a_command_runs_in_the_workspace(tmp_path: Path) -> None:
    result = run(
        build(tmp_path), command=[sys.executable, "-c", "import os; print(os.getcwd())"]
    )
    assert result["exit_code"] == 0
    assert Path(result["output"].strip()).resolve() == tmp_path.resolve()


def test_a_relative_cwd_is_resolved_against_the_workspace(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    result = run(
        build(tmp_path),
        command=[sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd="sub",
    )
    assert Path(result["output"].strip()).resolve() == (tmp_path / "sub").resolve()


def test_a_cwd_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    result = run(build(tmp_path), command=["echo", "hi"], cwd="../..")
    assert result["error"]["code"] == "path_outside_workspace"


def test_a_command_bumps_the_mutation_generation(tmp_path: Path) -> None:
    instance = build(tmp_path)
    assert instance._state.get_generation() == 0
    run(instance, command=[sys.executable, "-c", "print(1)"])
    assert instance._state.get_generation() == 1
    assert instance._state.mutations()[0].tool == "exec_command"


def test_a_failing_command_still_bumps_the_generation(tmp_path: Path) -> None:
    # Design 6.4: whether a command wrote is not the model's call to make.
    instance = build(tmp_path)
    run(instance, command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    assert instance._state.get_generation() == 1


def test_the_timeout_is_clamped_to_the_ceiling(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._max_timeout_seconds = 5
    result = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=600,
    )
    assert result["error"]["code"] == "timeout"
    assert result["error"]["details"]["limit_seconds"] == 5


def test_a_timed_out_command_reports_the_timeout(tmp_path: Path) -> None:
    instance = build(tmp_path)
    result = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=1,
    )
    assert result["error"]["code"] == "timeout"
    assert result["error"]["retryable"] is True


def test_the_environment_withholds_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "sk-secret")
    instance = build(tmp_path)
    result = run(
        instance,
        command=[sys.executable, "-c", "import os; print(os.environ.get('MY_API_KEY'))"],
    )
    assert result["output"].strip() == "None"


def test_background_returns_a_job_id_immediately(tmp_path: Path) -> None:
    instance = build(tmp_path)
    result = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        background=True,
    )
    assert result["state"] == "running"
    assert result["job_id"]
    # A background command counts as a workspace mutation on completion, not
    # on start; the running job is what the agent must wait for.
    instance._jobs.shutdown_jobs()


def test_output_is_truncated_and_flagged(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._max_output_bytes = 50
    result = run(instance, command=[sys.executable, "-c", "print('x' * 500)"])
    assert result["truncated"] is True
    assert len(result["output"].encode()) <= 50


def test_presentations_name_only_arguments_the_tools_take(tmp_path: Path) -> None:
    presentations = build(tmp_path).get_tool_presentations()
    assert presentations["exec_command"] == "exec_command({command})"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_exec_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.exec.tools'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/exec/tools.py`：

```python
"""The commands the code agent may run, and how they are bounded."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    JobManagerProvider,
    ToolProvider,
    WorkspaceProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.exec.runner import (
    DEFAULT_ENV_ALLOWLIST,
    build_environment,
    run_command,
)
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
    to_tool_error,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
DEFAULT_MAX_OUTPUT_BYTES = 65_536


class ExecCommandArgs(BaseModel):
    command: list[str] = Field(
        min_length=1,
        description=(
            "argv array, run without a shell. Use ['bash', '-lc', '...'] when "
            "you actually need shell semantics."
        ),
    )
    cwd: str = Field(
        default=".", description="Workspace-relative working directory."
    )
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS, ge=1, description="Wall-clock limit."
    )
    background: bool = Field(
        default=False,
        description="Start it as a job and return immediately with a job id.",
    )



@ComponentFactory("exec-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "exec-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property(
    "_default_timeout_seconds",
    "plugin.exec.default_timeout_seconds",
    DEFAULT_TIMEOUT_SECONDS,
)
@Property(
    "_max_timeout_seconds", "plugin.exec.max_timeout_seconds", MAX_TIMEOUT_SECONDS
)
@Property("_max_output_bytes", "plugin.exec.max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)
@RequiresBest("_jobs", JobManagerProvider, optional=True, immediate_rebind=True)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_jobs", "_workspace", "_state")
class ExecToolsPlugin:
    """Runs commands inside the workspace, in the foreground or as a job."""

    def __init__(self) -> None:
        self._plugin_name = "exec-tools"
        self._plugin_version = "1.0.0"
        self._default_timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self._max_timeout_seconds = MAX_TIMEOUT_SECONDS
        self._max_output_bytes = DEFAULT_MAX_OUTPUT_BYTES
        self._jobs: Any = None
        self._workspace: Any = None
        self._state: Any = None
        self._agent_id = ""
        self._guards: dict[str, ContractGuard] = {
            "_jobs": ContractGuard(self, "_jobs", JobManagerProvider),
            "_workspace": ContractGuard(self, "_workspace", WorkspaceProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

    @BindField("_jobs", if_valid=True)
    def _on_jobs_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_jobs")
    def _on_jobs_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_workspace", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_workspace")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_state", if_valid=True)
    def _on_state_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_state")
    def _on_state_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._workspace is None or self._jobs is None:
            return []
        return [self._exec_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        return {"exec_command": "exec_command({command})"}

    def _resolve_cwd(self, raw: str) -> Any:
        return resolve_workspace_path(self._workspace.get_workspace(), raw or ".")

    def _note_mutation(self, cwd_relative: str, tool: str) -> None:
        """Every command is treated as a possible write (design 6.4).

        Whether a command wrote is not something the model gets to declare, so
        the generation advances either way and any earlier verification report
        stops being fresh.
        """
        if self._state is not None:
            self._state.record_mutation(cwd_relative, tool)

    def _exec_tool(self) -> StructuredTool:
        plugin = self

        def exec_command(
            command: list[str],
            cwd: str = ".",
            timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
            background: bool = False,
        ) -> dict[str, Any]:
            try:
                target = plugin._resolve_cwd(cwd)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            argv = [str(part) for part in command]
            if background:
                try:
                    record = plugin._jobs.start_job(
                        argv,
                        cwd=str(target.absolute),
                        owner=plugin._agent_id or "local",
                    )
                except (RuntimeError, OSError) as exc:
                    return tool_error("internal_error", str(exc), retryable=True)
                return {
                    "job_id": record.id,
                    "state": record.state,
                    "command": list(record.command),
                    "cwd": target.relative,
                }
            limit = min(int(timeout_seconds), int(plugin._max_timeout_seconds))
            outcome = run_command(
                argv,
                cwd=target.absolute,
                timeout_seconds=float(limit),
                max_output_bytes=int(plugin._max_output_bytes),
                env=build_environment(DEFAULT_ENV_ALLOWLIST),
            )
            plugin._note_mutation(target.relative, "exec_command")
            if outcome.timed_out:
                return tool_error(
                    "timeout",
                    f"command exceeded its {limit}s deadline and was killed",
                    retryable=True,
                    limit_seconds=limit,
                    command=argv,
                )
            return {
                "command": list(outcome.command),
                "cwd": target.relative,
                "exit_code": outcome.exit_code,
                "output": outcome.output,
                "truncated": outcome.truncated,
                "duration_ms": outcome.duration_ms,
            }

        return StructuredTool.from_function(
            func=exec_command,
            name="exec_command",
            description=(
                "Run a command inside the workspace and return its output and "
                "exit code. `command` is an argv array executed WITHOUT a "
                "shell, so ['bash', '-lc', '...'] is how you ask for shell "
                "semantics explicitly. The working directory must stay inside "
                "the workspace. Output is capped and the cap is reported via "
                "'truncated'. The command inherits a filtered environment: "
                "API keys and other secrets are not passed to it, so a command "
                "that needs one will not find it. Every command counts as a "
                "possible workspace change, so any verification you ran before "
                "it is no longer fresh — re-run verification before claiming "
                "completion. Set background=true for a long-running command; "
                "you then get a job id back immediately and drive it with "
                "wait_job / write_stdin / stop_job. Returns {\"command\": "
                "[str], \"cwd\": str, \"exit_code\": int, \"output\": str, "
                "\"truncated\": bool, \"duration_ms\": int}, or {\"job_id\": "
                "str, \"state\": str, \"command\": [str], \"cwd\": str} for a "
                "background job, or {\"error\": {code, message, retryable, "
                "details}}."
            ),
            args_schema=ExecCommandArgs,
        )

```

**所有权怎么来的**：`owner` 取 `plugin._agent_id`（未配置时兜底 `"local"`），
由 agent 目录在实例化时通过 `plugin.agent_id` 属性注入；cwd 的相对路径直接用
`target.relative`，不需要额外的路径换算辅助。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_exec_tools.py tests/test_job_manager.py -q`
Expected: PASS（13 + 20 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/exec/tools.py tests/test_exec_tools.py
git commit -m "feat: add the exec_command tool with a workspace cwd and a filtered environment"
```

---

### Task 5: `write_stdin` / `wait_job` / `stop_job`

**Files:**
- Modify: `src/langharness_core/plugins/exec/tools.py`
- Test: `tests/test_exec_tools.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `JobManagerProvider`。
- Produces：三个工具与它们的结果形状（见下）。

**结果形状：**
- `wait_job` → `{"job_id", "state", "exit_code", "output", "truncated", "new_output"}`，其中
  `output` 是**自上次调用以来的增量**（工具实例记住每个 job 的读偏移），`new_output` 布尔。
- `write_stdin` → `{"job_id", "state", "written": int}`
- `stop_job` → `{"job_id", "state", "exit_code"}`

**为什么 wait_job 要返回增量而不是全量：** 一个跑了几分钟的后台 job 可能有几十 KB
输出，每次轮询都全量回灌会把上下文撑爆。增量 + `truncated` 才是能长期用的形状。

**`wait_job` 的上限：** 设计第 6.1 节要求"等待不得超过交互更新周期"，因此
`timeout_seconds` 钳到一个较小的上限（`WAIT_CEILING_SECONDS = 30`），超时就返回
`state="running"` 的当前快照，让模型决定继续等还是干别的。

- [ ] **Step 1: 写失败测试**

在 `tests/test_exec_tools.py` 末尾追加：

```python
WAIT_CEILING = 30


def test_wait_job_returns_output_since_the_last_call(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "print('first')"],
        background=True,
    )
    first = wait(instance, started["job_id"], 10)
    assert first["state"] == "exited"
    assert "first" in first["output"]
    again = wait(instance, started["job_id"], 1)
    assert again["output"] == ""
    assert again["new_output"] is False


def test_wait_job_on_a_running_job_returns_the_current_snapshot(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    snapshot = wait(instance, started["job_id"], 1)
    assert snapshot["state"] == "running"
    assert snapshot["exit_code"] is None
    stop(instance, started["job_id"])


def test_wait_job_clamps_its_window(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    import time as clock

    begun = clock.monotonic()
    wait(instance, started["job_id"], 600)
    assert clock.monotonic() - begun < WAIT_CEILING + 5
    stop(instance, started["job_id"])


def test_wait_job_on_an_unknown_job_is_an_error(tmp_path: Path) -> None:
    assert wait(build(tmp_path), "nope", 1)["error"]["code"] == "job_not_found"


def test_stop_job_kills_the_group_and_reports_it(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    stopped = stop(instance, started["job_id"])
    assert stopped["state"] == "killed"


def test_stop_job_on_an_unknown_job_is_an_error(tmp_path: Path) -> None:
    assert (
        stop(build(tmp_path), "nope")["error"]["code"] == "job_not_found"
    )


def test_write_stdin_feeds_the_process(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance,
        command=[sys.executable, "-c", "import sys; print(sys.stdin.readline().strip())"],
        background=True,
    )
    assert write(instance, started["job_id"], "hello\n")["written"] == 6
    assert "hello" in wait(instance, started["job_id"], 10)["output"]


def test_write_stdin_to_a_finished_job_is_an_error(tmp_path: Path) -> None:
    instance = build(tmp_path)
    started = run(
        instance, command=[sys.executable, "-c", "print(1)"], background=True
    )
    wait(instance, started["job_id"], 10)
    result = write(instance, started["job_id"], "late\n")
    assert result["error"]["code"] == "job_not_running"


def test_background_jobs_are_owned_by_the_agent(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._agent_id = "alpha::s1"
    started = run(
        instance,
        command=[sys.executable, "-c", "import time; time.sleep(20)"],
        background=True,
    )
    assert instance._jobs.get_job(started["job_id"]).owner == "alpha::s1"
    stop(instance, started["job_id"])


def test_presentations_cover_every_job_tool(tmp_path: Path) -> None:
    presentations = build(tmp_path).get_tool_presentations()
    assert presentations["wait_job"] == "wait_job({job_id})"
    assert presentations["stop_job"] == "stop_job({job_id})"
    # write_stdin is absent: its payload reads better as the bare name.
    assert "write_stdin" not in presentations


def wait(instance: ExecToolsPlugin, job_id: str, timeout: int) -> dict:
    return tools(instance)["wait_job"].invoke(
        {"job_id": job_id, "timeout_seconds": timeout}
    )


def stop(instance: ExecToolsPlugin, job_id: str) -> dict:
    return tools(instance)["stop_job"].invoke({"job_id": job_id})


def write(instance: ExecToolsPlugin, job_id: str, data: str) -> dict:
    return tools(instance)["write_stdin"].invoke({"job_id": job_id, "data": data})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_exec_tools.py -q -k "job"`
Expected: FAIL — `KeyError: 'wait_job'`

- [ ] **Step 3: 写实现**

在 `tools.py` 里补 args 模型、三个工具方法，并把它们加进 `get_tools()` 与
`get_tool_presentations()`：

```python
WAIT_CEILING_SECONDS = 30


class JobIdArgs(BaseModel):
    job_id: str = Field(min_length=1, description="Job id from exec_command.")


class WaitJobArgs(JobIdArgs):
    timeout_seconds: int = Field(
        default=WAIT_CEILING_SECONDS,
        ge=1,
        le=WAIT_CEILING_SECONDS,
        description="How long to wait before returning the current snapshot.",
    )


class WriteStdinArgs(JobIdArgs):
    data: str = Field(description="Text to send to the process's standard input.")
```

组件里加一个每实例的读偏移表：`self._offsets: dict[str, int] = {}`。

```python
    def _wait_tool(self) -> StructuredTool:
        plugin = self

        def wait_job(job_id: str, timeout_seconds: int = WAIT_CEILING_SECONDS) -> dict[str, Any]:
            try:
                record = plugin._jobs.wait_job(
                    job_id, min(int(timeout_seconds), WAIT_CEILING_SECONDS)
                )
                text, offset = plugin._jobs.read_output(
                    job_id, plugin._offsets.get(job_id, 0)
                )
            except KeyError:
                return tool_error("job_not_found", f"unknown job {job_id!r}")
            plugin._offsets[job_id] = offset
            if record.state != "running":
                plugin._note_mutation(".", "wait_job")
            return {
                "job_id": job_id,
                "state": record.state,
                "exit_code": record.exit_code,
                "output": text,
                "truncated": False,
                "new_output": bool(text),
            }

        return StructuredTool.from_function(
            func=wait_job,
            name="wait_job",
            description=(
                "Wait up to timeout_seconds for a background job, then return "
                "its current snapshot. 'output' is only what arrived SINCE "
                "YOUR LAST CALL to this tool, so polling does not re-flood the "
                "conversation. If 'state' is still 'running' the job is not "
                "finished; decide whether to keep waiting or do something else "
                "rather than spinning here. Returns {\"job_id\": str, "
                "\"state\": 'running'|'exited'|'killed'|'timed_out', "
                "\"exit_code\": int|null, \"output\": str, \"truncated\": bool, "
                "\"new_output\": bool}, or {\"error\": {code, message, "
                "retryable, details}}."
            ),
            args_schema=WaitJobArgs,
        )

    def _stdin_tool(self) -> StructuredTool:
        plugin = self

        def write_stdin(job_id: str, data: str) -> dict[str, Any]:
            try:
                record = plugin._jobs.write_stdin(job_id, data)
            except KeyError:
                return tool_error("job_not_found", f"unknown job {job_id!r}")
            except RuntimeError as exc:
                return tool_error("job_not_running", str(exc), retryable=True)
            return {"job_id": record.id, "state": record.state, "written": len(data)}

        return StructuredTool.from_function(
            func=write_stdin,
            name="write_stdin",
            description=(
                "Send text to a background job's standard input, for commands "
                "that prompt or read a stream. Returns {\"job_id\": str, "
                "\"state\": str, \"written\": int}, or {\"error\": {code, "
                "message, retryable, details}} when the job is unknown or has "
                "already stopped taking input."
            ),
            args_schema=WriteStdinArgs,
        )

    def _stop_tool(self) -> StructuredTool:
        plugin = self

        def stop_job(job_id: str) -> dict[str, Any]:
            try:
                record = plugin._jobs.stop_job(job_id)
            except KeyError:
                return tool_error("job_not_found", f"unknown job {job_id!r}")
            plugin._note_mutation(".", "stop_job")
            return {
                "job_id": record.id,
                "state": record.state,
                "exit_code": record.exit_code,
            }

        return StructuredTool.from_function(
            func=stop_job,
            name="stop_job",
            description=(
                "Stop a background job and its whole process group, including "
                "any children it spawned. Use it when a command is stuck or no "
                "longer needed; the process does not survive this call. "
                "Returns {\"job_id\": str, \"state\": 'killed', \"exit_code\": "
                "int|null}, or {\"error\": {code, message, retryable, "
                "details}}."
            ),
            args_schema=JobIdArgs,
        )
```

`get_tools()` 返回四个工具；`get_tool_presentations()` 加：

```python
            "wait_job": "wait_job({job_id})",
            "stop_job": "stop_job({job_id})",
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_exec_tools.py -q`
Expected: PASS（23 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/exec/tools.py tests/test_exec_tools.py
git commit -m "feat: drive background jobs with wait_job, write_stdin and stop_job"
```

---

### Task 6: 把 job 事件接进 agent loop

**Files:**
- Modify: `src/langharness_core/plugins/loop/agent_loop.py`
- Test: `tests/test_job_events.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `JobManagerProvider.drain_events()`。
- Produces：`PluginAgentLoop._drain_job_events() -> list[dict[str, Any]]`

**为什么用"顺带 drain"而不是并发合并：** job 跑在工具线程里，事件进的是一个线程安全的
队列。要在 `async for ... in graph.astream(...)` 的同时把队列也流出去，就得并发跑两个
异步迭代器再合并——复杂且难测。这里改成在**每个 graph chunk 之间** drain 一次：长命令
期间输出不实时，但下一轮模型调用前一定送出去。粒度换来的是可测性。这一点必须在
docstring 里写清楚，不要假装是实时的。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_job_events.py`：

```python
"""Tests for job event forwarding through the agent loop."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

from types import SimpleNamespace

from langharness_core.plugins.loop.agent_loop import PluginAgentLoop


class FakeJobs:
    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = batches
        self.calls = 0

    def drain_events(self) -> list[dict]:
        self.calls += 1
        if self._batches:
            return self._batches.pop(0)
        return []


def test_no_job_manager_means_no_events() -> None:
    loop = PluginAgentLoop()
    assert loop._drain_job_events() == []


def test_queued_events_are_returned_once() -> None:
    loop = PluginAgentLoop()
    loop._job_manager = FakeJobs([[{"type": "job_started", "job_id": "j1"}]])
    assert loop._drain_job_events() == [{"type": "job_started", "job_id": "j1"}]
    assert loop._drain_job_events() == []


def test_a_failing_job_manager_does_not_break_the_stream() -> None:
    class Boom:
        def drain_events(self) -> list[dict]:
            raise RuntimeError("queue exploded")

    loop = PluginAgentLoop()
    loop._job_manager = Boom()
    assert loop._drain_job_events() == []


def test_the_loop_declares_the_job_field_as_scoped() -> None:
    from langharness_plugin.scoped_dependencies import scoped_fields

    assert "_job_manager" in scoped_fields(PluginAgentLoop)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_job_events.py -q`
Expected: FAIL — `AttributeError: 'PluginAgentLoop' object has no attribute '_job_manager'`

- [ ] **Step 3: 写实现**

在 `agent_loop.py` 中：

1. 导入 `JobManagerProvider`（加进 `langharness_core.contracts` 的导入块）。
2. 依赖声明（放在 `_workspace_provider` 之后）：

```python
@RequiresBest("_job_manager", JobManagerProvider, optional=True, immediate_rebind=True)
```

3. `@ScopedDependencies(...)` 字段列表加入 `"_job_manager"`。
4. `__init__` 加 `self._job_manager: Any = None` 与守卫：

```python
            "_job_manager": ContractGuard(self, "_job_manager", JobManagerProvider),
```

5. 绑定回调（与 `_workspace_provider` 同形，best 语义、unbind 置 `_graph = None`）。
6. 新增方法：

```python
    def _drain_job_events(self) -> list[dict[str, Any]]:
        """Take whatever background-job events have queued up.

        Drained between graph chunks rather than concurrently: output from a
        long-running job surfaces on the next chunk, not the instant it is
        produced. That is a deliberate trade of immediacy for a stream that
        stays a single, testable iterator.
        """
        if self._job_manager is None:
            return []
        try:
            return list(self._job_manager.drain_events())
        except Exception:
            LOGGER.exception("Job event drain failed; continuing without events")
            return []
```

7. 在 `astream` 的 `async for stream_type, chunk in self._graph.astream(...)` 循环体
   最前面加：

```python
            for job_event in self._drain_job_events():
                yield job_event
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_job_events.py tests/test_prompt_integration.py -q`
Expected: PASS

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。既有 loop 测试（`test_e2e.py`、`test_agent_loop_*`）必须照旧通过——
没有绑定 job manager 时 `_drain_job_events()` 返回空列表，行为完全不变。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/loop/agent_loop.py tests/test_job_events.py
git commit -m "feat: forward background-job events through the agent stream"
```

---

### Task 7: Catalog 装配与作用域 e2e

**Files:**
- Modify: `src/langharness_core/plugin.py`
- Modify: `tests/test_imports.py`
- Test: `tests/test_exec_scope.py`（新建）

**Interfaces:**
- Consumes: Task 3/4/5 的组件。
- Produces：`AGENT_PLUGIN_CATALOG["exec"]`、`["exec-tools"]` 与对应的 `builtin_package()` contribution。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_exec_scope.py`：

```python
"""Two agents must not see or kill each other's jobs."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from langharness_core.contracts import SPEC_JOB_MANAGER, SPEC_TOOL, SPEC_WORKSPACE
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id

DESCRIPTION = (
    "Exec scope test plugin. Implements a test specification. Properties vary "
    "per instance. Requires a restart for property changes. Uninstall when "
    "tests finish."
)


def descriptor(
    name: str, module: str, factory: str, specification: str
) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=DESCRIPTION,
    )


BUNDLES = (
    descriptor(
        "job-manager",
        "langharness_core.plugins.exec.jobs",
        "job-manager-plugin-factory",
        SPEC_JOB_MANAGER,
    ),
    descriptor(
        "exec-tools",
        "langharness_core.plugins.exec.tools",
        "exec-tools-plugin-factory",
        SPEC_TOOL,
    ),
    descriptor(
        "workspace",
        "langharness_core.plugins.workspace.root",
        "workspace-root-plugin-factory",
        SPEC_WORKSPACE,
    ),
)


def start_agent(manager: PluginManager, agent_id: str, workspace: Path) -> None:
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    manager.create_instance(
        "job-manager-plugin-factory",
        "langharness_core.plugins.exec.jobs",
        scope,
    )
    manager.create_instance(
        "exec-tools-plugin-factory",
        "langharness_core.plugins.exec.tools",
        scope,
    )
    manager.create_instance(
        "workspace-root-plugin-factory",
        "langharness_core.plugins.workspace.root",
        scope,
        properties={"plugin.workspace.root_dir": str(workspace)},
    )


def test_job_managers_are_per_agent(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        for name in ("alpha", "beta"):
            workspace = tmp_path / name
            workspace.mkdir()
            start_agent(manager, name, workspace)

        alpha: list[Any] = manager.find_services(
            SPEC_JOB_MANAGER, filter="(plugin.scope_id=agent:alpha)"
        )
        beta: list[Any] = manager.find_services(
            SPEC_JOB_MANAGER, filter="(plugin.scope_id=agent:beta)"
        )
        assert len(alpha) == 1
        assert len(beta) == 1

        job = alpha[0].start_job(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            cwd=str(tmp_path / "alpha"),
            owner="alpha::s1",
        )
        assert len(alpha[0].list_jobs()) == 1
        assert beta[0].list_jobs() == []
        # beta cannot reach alpha's job, let alone stop it.
        assert beta[0].get_job(job.id) is None
        alpha[0].stop_job(job.id)
    finally:
        manager.stop()


def test_exec_tools_bind_their_own_agents_services(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        workspace = tmp_path / "alpha"
        workspace.mkdir()
        start_agent(manager, "alpha", workspace)
        providers: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:alpha)"
        )
        assert len(providers) == 1
        jobs = manager.find_service(
            SPEC_JOB_MANAGER, filter="(plugin.scope_id=agent:alpha)"
        )
        assert providers[0]._jobs is jobs
        names = {item.name for item in providers[0].get_tools()}
        assert names == {"exec_command", "write_stdin", "wait_job", "stop_job"}
    finally:
        manager.stop()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_exec_scope.py -q`
Expected: PASS 或 FAIL 都可以——它直接安装描述符，不经过 catalog。真正的交付物是
下一步的 catalog 装配，因此本步骤至少要确认测试本身是绿的。

- [ ] **Step 3: 写实现**

在 `src/langharness_core/plugin.py` 中：

1. 从 `langharness_core.contracts` 导入 `SPEC_JOB_MANAGER`。
2. `AGENT_PLUGIN_CATALOG` 追加：

```python
    "exec": (
        "langharness_core.plugins.exec.jobs",
        "job-manager-plugin-factory",
        SPEC_JOB_MANAGER,
    ),
    "exec-tools": (
        "langharness_core.plugins.exec.tools",
        "exec-tools-plugin-factory",
        SPEC_TOOL,
    ),
```

3. `AGENT_PLUGIN_DESCRIPTIONS` 追加两条同句式说明。
4. `builtin_package()` 追加两个 `agent` template contribution。
5. `tests/test_imports.py` 的 `PUBLIC_MODULES` 追加：

```python
    "langharness_core.plugins.exec.runner",
    "langharness_core.plugins.exec.jobs",
    "langharness_core.plugins.exec.tools",
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_exec_scope.py tests/test_imports.py -q`
Expected: PASS

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过，覆盖率 ≥95%。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugin.py tests/test_imports.py tests/test_exec_scope.py
git commit -m "feat: wire command execution into the agent catalog"
```

---

## 完成后的验收

计划 2b 落地后应能观测到：

1. agent 能在 workspace 内跑命令，cwd 越界被拒（Task 4）；
2. 子进程拿不到 `*_API_KEY` / `*_TOKEN` / `*_PASSWORD` 之类的环境变量，白名单里
   误加一个也不会泄漏（Task 2 的两道判断）；
3. 超时与 `stop_job` 都收掉**整个进程组**，不留孤儿子进程（Task 2/3，已用真实
   派生孙进程的命令验证）；
4. 输出超限被结构化标记且不撑爆上下文；`wait_job` 只回增量（Task 4/5）；
5. 每条命令都递增 mutation generation，因此此前的验证报告立即失效（Task 4）；
6. 两个 agent 的 job 互不可见、互不可停（Task 7）；
7. 后台 job 的 `job_started` / `job_output` / `job_finished` 事件出现在 `astream`
   输出里（Task 6）。

计划 3b（run controller 自动续跑、`ask_user`、独立 `verify` 工具、新流事件、
`code` preset、三协议 e2e、真实模型临时仓库验收）的骨架见
`docs/plans/2026-09-22-code-agent-phase1-roadmap.md`。
