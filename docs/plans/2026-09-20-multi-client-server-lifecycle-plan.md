# 多客户端 Server 生命周期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 多个 CLI 进程共享一个 server,最后一个客户端断开后 server 在宽限期内无新连接时自动优雅退出。

**Architecture:** 每个 CLI 进程开一条 WebSocket"命脉"到 server;server 用 `ClientRegistry` 统计活跃连接,归零后经宽限期触发 uvicorn `should_exit`。`APIGuard` 不再杀 server,只负责拉起(带 `--auto-shutdown`、启动锁)和维护命脉。

**Tech Stack:** Python 3.13、FastAPI/uvicorn 0.52、websockets>=16(sync client)、httpx、pytest + pytest-asyncio。

**Spec:** [2026-09-20-multi-client-server-lifecycle-design.md](../designs/2026-09-20-multi-client-server-lifecycle-design.md)

## Global Constraints

- 宽限期默认 10.0s,由 `--auto-shutdown-grace` 覆盖;`--auto-shutdown` 默认关。
- `timeout_graceful_shutdown=10`(卡死请求兜底)。
- 认证 token 默认 `"secret"`,经 `Authorization: Bearer <token>` 头传递。
- 每个任务必须通过 `make check`(ruff + mypy + pyright + test_imports + 全量 pytest,coverage 95% 下限)。
- 测试命令统一用 `.venv/bin/python -m pytest`(Makefile 的 `PYTHON ?= .venv/bin/python`)。
- pytest 无 `asyncio_mode=auto`,异步测试必须标 `@pytest.mark.asyncio`。
- 提交信息风格:小写前缀(`feat:`、`test:`、`refactor:`)。

## File Structure

**Create:**

- `src/langharness_api/common/client_registry.py` — 客户端计数与自动关闭调度
- `src/langharness_api/plugins/routes/lifecycle.py` — WS 命脉路由插件
- `tests/test_client_registry.py` — registry 单测
- `tests/test_server_runtime.py` — runtime 接线与 lifespan 单测
- `tests/test_server_lifecycle_e2e.py` — 真实进程端到端测试

**Modify:**

- `src/langharness_api/contracts.py` — `ServerServerProvider.server` 签名
- `src/langharness_api/plugins/server/app.py` — `build_app` 挂 lifespan
- `src/langharness_api/plugins/server/runtime.py` — uvicorn Server 手动实例化 + registry 接线
- `src/langharness_api/plugin.py` — 新 descriptor + builtin_package 贡献
- `src/langharness/bootstrap.py` — 两个新 CLI 参数、token 扫描、guard 接线
- `src/langharness/api_guard.py` — 重写:启动锁、auto-shutdown、命脉 WS、退出钩子
- `tests/test_api_guard.py` — 适配新行为
- `tests/test_bootstrap.py` — 适配 guard spy 与新参数
- `pyproject.toml` — `websockets` 依赖

---

### Task 1: ClientRegistry 组件

**Files:**

- Create: `src/langharness_api/common/client_registry.py`
- Test: `tests/test_client_registry.py`

**Interfaces:**

- Consumes: 无(首个任务)
- Produces: `ClientRegistry(grace: float = 10.0)`,方法 `set_shutdown_callback(callback: Callable[[], None]) -> None`、`set_enabled(enabled: bool) -> None`、`mark_ready() -> None`、`attach(client: Any) -> None`、`detach(client: Any) -> None`、`active_count() -> int`

- [ ] **Step 1: 写失败测试**

`tests/test_client_registry.py`:

```python
"""Unit tests for the client registry driving server auto-shutdown."""

from __future__ import annotations

import asyncio

import pytest

from langharness_api.common.client_registry import ClientRegistry


@pytest.mark.asyncio
async def test_attach_and_detach_count_clients() -> None:
    registry = ClientRegistry()
    client = object()
    assert registry.active_count() == 0
    registry.attach(client)
    assert registry.active_count() == 1
    registry.attach(client)
    assert registry.active_count() == 1
    registry.detach(client)
    assert registry.active_count() == 0


@pytest.mark.asyncio
async def test_last_detach_schedules_shutdown_after_grace() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()

    first, second = object(), object()
    registry.attach(first)
    registry.attach(second)
    registry.detach(first)
    await asyncio.sleep(0.05)
    assert shutdowns == []
    registry.detach(second)
    await asyncio.sleep(0.05)
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_attach_during_grace_cancels_shutdown() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.05)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()

    first, second = object(), object()
    registry.attach(first)
    registry.detach(first)
    registry.attach(second)  # immediately re-attach, within grace
    await asyncio.sleep(0.1)
    assert shutdowns == []


@pytest.mark.asyncio
async def test_not_ready_never_schedules_until_marked_ready() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))

    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert shutdowns == []
    registry.mark_ready()  # empty at ready -> schedules once
    await asyncio.sleep(0.05)
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_disabled_registry_never_shuts_down() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(False)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))
    registry.mark_ready()

    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert shutdowns == []


@pytest.mark.asyncio
async def test_missing_shutdown_callback_is_safe() -> None:
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.mark_ready()

    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_client_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_api.common.client_registry'`

- [ ] **Step 3: 实现 ClientRegistry**

`src/langharness_api/common/client_registry.py`:

```python
"""Client connection registry that drives server auto-shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class ClientRegistry:
    """Counts attached client websockets and schedules shutdown at zero.

    A shutdown is scheduled only when all three conditions hold: the server
    is ready (``mark_ready`` ran), auto-shutdown is enabled, and a shutdown
    callback was provided. After the last client detaches, the callback runs
    once the grace window elapses with no new client attaching.
    """

    def __init__(self, grace: float = 10.0) -> None:
        self._grace = grace
        self._clients: set[Any] = set()
        self._ready = False
        self._enabled = False
        self._shutdown_callback: Callable[[], None] | None = None
        self._timer: asyncio.Task[None] | None = None

    def set_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self._shutdown_callback = callback

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def mark_ready(self) -> None:
        self._ready = True
        if not self._clients:
            self._schedule_shutdown()

    def attach(self, client: Any) -> None:
        self._clients.add(client)
        self._cancel_timer()

    def detach(self, client: Any) -> None:
        self._clients.discard(client)
        if not self._clients:
            self._schedule_shutdown()

    def active_count(self) -> int:
        return len(self._clients)

    def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    def _schedule_shutdown(self) -> None:
        if not self._ready or not self._enabled or self._shutdown_callback is None:
            return
        if self._timer is not None and not self._timer.done():
            return
        self._timer = asyncio.create_task(self._run_timer())

    async def _run_timer(self) -> None:
        await asyncio.sleep(self._grace)
        if not self._clients and self._ready and self._enabled:
            assert self._shutdown_callback is not None
            self._shutdown_callback()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_client_registry.py -q`
Expected: PASS(6 passed)

- [ ] **Step 5: 提交**

```bash
git add src/langharness_api/common/client_registry.py tests/test_client_registry.py
git commit -m "feat(api): add client registry for auto-shutdown"
```

---

### Task 2: 命脉 WebSocket 路由插件

**Files:**

- Create: `src/langharness_api/plugins/routes/lifecycle.py`
- Modify: `src/langharness_api/plugin.py`(descriptor + builtin_package)
- Test: `tests/test_server_lifecycle_unit.py`

**Interfaces:**

- Consumes: `ClientRegistry`(Task 1),经 `app.state.client_registry` 访问
- Produces: `LifecycleRoutePlugin`(factory `api-lifecycle-route-factory`,实现 `RouteProvider`),路由 `GET /clients/attach`

- [ ] **Step 1: 写失败测试**

`tests/test_server_lifecycle_unit.py`:

```python
"""Unit tests for the client lifecycle websocket route."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from langharness_api.common.client_registry import ClientRegistry
from langharness_api.plugins.routes.lifecycle import LifecycleRoutePlugin


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_attach_websocket_registers_client() -> None:
    registry = ClientRegistry()
    app = FastAPI()
    app.state.client_registry = registry
    app.include_router(LifecycleRoutePlugin().get_router())
    with TestClient(app) as client:
        assert registry.active_count() == 0
        with client.websocket_connect("/clients/attach"):
            wait_until(lambda: registry.active_count() == 1)
        wait_until(lambda: registry.active_count() == 0)


def test_missing_registry_still_accepts_connection() -> None:
    app = FastAPI()
    app.include_router(LifecycleRoutePlugin().get_router())
    with TestClient(app) as client:
        with client.websocket_connect("/clients/attach"):
            pass
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_server_lifecycle_unit.py -q`
Expected: FAIL — `ImportError: cannot import name 'LifecycleRoutePlugin'`

- [ ] **Step 3: 实现路由插件**

`src/langharness_api/plugins/routes/lifecycle.py`:

```python
"""Client lifecycle WebSocket route plugin."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_api.contracts import RouteProvider


@ComponentFactory("api-lifecycle-route-factory")
@Provides(RouteProvider)
@Property("_plugin_name", "plugin.name", "lifecycle")
@Property("_plugin_version", "plugin.version", "1.0.0")
class LifecycleRoutePlugin:
    """Serves the client lifeline websocket used for auto-shutdown."""

    def __init__(self) -> None:
        self._plugin_name = "lifecycle"
        self._plugin_version = "1.0.0"

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.websocket("/clients/attach")
        async def attach(websocket: WebSocket) -> None:
            registry = getattr(websocket.app.state, "client_registry", None)
            await websocket.accept()
            if registry is not None:
                registry.attach(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                if registry is not None:
                    registry.detach(websocket)

        return router

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
```

- [ ] **Step 4: 在 plugin.py 注册 descriptor**

Modify `src/langharness_api/plugin.py`,在 `api_health_descriptor` 之后插入:

```python
def api_lifecycle_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="api-lifecycle",
        version="1.0.0",
        module="langharness_api.plugins.routes.lifecycle",
        factory="api-lifecycle-route-factory",
        specification=SPEC_ROUTE,
        description=ROUTE_DESCRIPTION,
    )
```

并在 `builtin_package` 的 contributions 元组中、`api_health` 一行之后加入:

```python
            PluginContribution(
                "api-lifecycle", "server", api_lifecycle_descriptor()
            ),
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_server_lifecycle_unit.py tests/test_builtin_descriptors.py -q`
Expected: PASS(test_builtin_descriptors 的 parametrized 校验自动覆盖新 descriptor)

- [ ] **Step 6: 提交**

```bash
git add src/langharness_api/plugins/routes/lifecycle.py src/langharness_api/plugin.py tests/test_server_lifecycle_unit.py
git commit -m "feat(api): serve the client lifeline websocket route"
```

---

### Task 3: server 运行时接线与启动参数

**Files:**

- Modify: `src/langharness_api/contracts.py:66`(ServerServerProvider.server 签名)
- Modify: `src/langharness_api/plugins/server/app.py:127-150`(build_app 挂 lifespan)
- Modify: `src/langharness_api/plugins/server/runtime.py:51-56`(server 实现)
- Modify: `src/langharness/bootstrap.py`(parse_options 两参数 + mode server 传参)
- Modify: `tests/test_bootstrap.py:265-270`(fake server 签名)
- Test: `tests/test_server_runtime.py`、`tests/test_bootstrap.py` 追加

**Interfaces:**

- Consumes: `ClientRegistry`(Task 1)、`_lifespan`(本任务 app.py 产出)
- Produces: `ServerServerService.server(self, host: str, port: int, auto_shutdown: bool = False, grace: float = 10.0) -> None`;`parse_options` 产出 `options.auto_shutdown`(bool)与 `options.auto_shutdown_grace`(float)

- [ ] **Step 1: 写失败测试**

`tests/test_server_runtime.py`:

```python
"""Unit tests for the server process service shutdown wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import langharness_api.plugins.server.runtime as runtime_module
from langharness_api.common.client_registry import ClientRegistry
from langharness_api.plugins.server.app import _lifespan
from langharness_api.plugins.server.runtime import ServerServerService


class FakeApi:
    def build_app(self) -> FastAPI:
        return FastAPI()


class FakeUvicornServer:
    def __init__(self, config: SimpleNamespace) -> None:
        self.config = config
        self.should_exit = False
        self.ran = False

    def run(self) -> None:
        self.ran = True


@pytest.fixture
def faked_uvicorn(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeUvicornServer, list]:
    fake_server = FakeUvicornServer(SimpleNamespace())
    configs: list = []

    def fake_config(app, **kwargs):
        configs.append((app, kwargs))
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(runtime_module.uvicorn, "Config", fake_config)
    monkeypatch.setattr(
        runtime_module.uvicorn, "Server", lambda config: fake_server
    )
    return fake_server, configs


@pytest.mark.asyncio
async def test_server_wires_auto_shutdown(
    faked_uvicorn: tuple[FakeUvicornServer, list],
) -> None:
    fake_server, configs = faked_uvicorn
    service = ServerServerService()
    service._api = FakeApi()

    service.server("127.0.0.1", 19000, auto_shutdown=True, grace=0.01)

    assert fake_server.ran is True
    app, kwargs = configs[0]
    assert isinstance(app, FastAPI)
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 19000,
        "log_config": None,
        "timeout_graceful_shutdown": 10,
    }
    registry = app.state.client_registry
    registry.mark_ready()
    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert fake_server.should_exit is True


@pytest.mark.asyncio
async def test_server_without_auto_shutdown_never_exits(
    faked_uvicorn: tuple[FakeUvicornServer, list],
) -> None:
    fake_server, configs = faked_uvicorn
    service = ServerServerService()
    service._api = FakeApi()

    service.server("127.0.0.1", 19000)

    app = configs[0][0]
    registry = app.state.client_registry
    registry.mark_ready()
    client = object()
    registry.attach(client)
    registry.detach(client)
    await asyncio.sleep(0.05)
    assert fake_server.should_exit is False


@pytest.mark.asyncio
async def test_lifespan_marks_registry_ready() -> None:
    shutdowns: list[int] = []
    registry = ClientRegistry(grace=0.01)
    registry.set_enabled(True)
    registry.set_shutdown_callback(lambda: shutdowns.append(1))

    app = FastAPI()
    app.state.client_registry = registry
    async with _lifespan(app):
        await asyncio.sleep(0.05)  # empty at mark_ready -> one shutdown
    assert shutdowns == [1]


@pytest.mark.asyncio
async def test_lifespan_without_registry_is_a_noop() -> None:
    async with _lifespan(FastAPI()):
        pass
```

`tests/test_bootstrap.py` 追加(文件已有 `import` bootstrap_module 的惯例,若未 import 则 `import langharness.bootstrap as bootstrap_module`):

```python
def test_parse_options_auto_shutdown_defaults() -> None:
    options, remainder = bootstrap_module.parse_options([])
    assert options.auto_shutdown is False
    assert options.auto_shutdown_grace == 10.0
    assert remainder == []


def test_parse_options_auto_shutdown_flags() -> None:
    options, remainder = bootstrap_module.parse_options(
        ["--mode", "server", "--auto-shutdown", "--auto-shutdown-grace", "0.5"]
    )
    assert options.mode == "server"
    assert options.auto_shutdown is True
    assert options.auto_shutdown_grace == 0.5
    assert remainder == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_server_runtime.py tests/test_bootstrap.py -q -k "auto_shutdown or lifespan"` 后运行
`.venv/bin/python -m pytest tests/test_server_runtime.py -q`
Expected: FAIL(ImportError / AttributeError: options 无 auto_shutdown;现有 test_bootstrap 里 `test_run_assembles_real_ui_and_server_managers` 也会因新 kwargs 而失败,属预期)

- [ ] **Step 3: 改 contracts.py 签名**

Modify `src/langharness_api/contracts.py:66`:

```python
    def server(
        self,
        host: str,
        port: int,
        auto_shutdown: bool = False,
        grace: float = 10.0,
    ) -> None: ...
```

- [ ] **Step 4: app.py 挂 lifespan**

Modify `src/langharness_api/plugins/server/app.py`:

文件头 import 区加入:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
```

在 `class APIServerService` 之前加模块级函数:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    registry = getattr(app.state, "client_registry", None)
    if registry is not None:
        registry.mark_ready()
    yield
```

`build_app` 中 `app = FastAPI(title="langharness_api", version="0.1.0")` 改为:

```python
        app = FastAPI(
            title="langharness_api", version="0.1.0", lifespan=_lifespan
        )
```

- [ ] **Step 5: runtime.py 改造 server()**

Modify `src/langharness_api/plugins/server/runtime.py:51-56`:

```python
    def server(
        self,
        host: str,
        port: int,
        auto_shutdown: bool = False,
        grace: float = 10.0,
    ) -> None:
        if self._api is None:
            raise RuntimeError("API server service is unavailable")
        app = self._api.build_app()
        app.state.agent = self._agent
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_config=None,
            timeout_graceful_shutdown=10,
        )
        server = uvicorn.Server(config)
        registry = ClientRegistry(grace=grace)
        registry.set_enabled(auto_shutdown)
        registry.set_shutdown_callback(
            lambda: setattr(server, "should_exit", True)
        )
        app.state.client_registry = registry
        server.run()
```

并在文件头加入 `from langharness_api.common.client_registry import ClientRegistry`。

- [ ] **Step 6: bootstrap.py 加参数与传参**

Modify `src/langharness/bootstrap.py` `parse_options`,在 `--config-dir` 之后加入:

```python
    parser.add_argument(
        "--auto-shutdown",
        action="store_true",
        default=False,
        help="exit the server after its last client detaches",
    )
    parser.add_argument(
        "--auto-shutdown-grace",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="grace window before auto-shutdown (default 10)",
    )
```

`_run` 中 `server_service.server(options.server_ip, options.server_port)` 改为:

```python
            server_service.server(
                options.server_ip,
                options.server_port,
                auto_shutdown=options.auto_shutdown,
                grace=options.auto_shutdown_grace,
            )
```

- [ ] **Step 7: 更新 test_bootstrap 的 fake server 签名**

Modify `tests/test_bootstrap.py` 中 `test_run_assembles_real_ui_and_server_managers` 的 fake:

```python
    def server(self, host, port, auto_shutdown=False, grace=10.0):
        calls.append(("server", host, port, auto_shutdown, grace))
```

断言改为:

```python
    assert calls[0] == ("server", "127.0.0.2", 19000, False, 10.0)
```

- [ ] **Step 8: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_server_runtime.py tests/test_bootstrap.py -q`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add src/langharness_api/contracts.py src/langharness_api/plugins/server/app.py src/langharness_api/plugins/server/runtime.py src/langharness/bootstrap.py tests/test_server_runtime.py tests/test_bootstrap.py
git commit -m "feat(api,cli): wire uvicorn auto-shutdown and its launcher flags"
```

---

### Task 4: APIGuard 重写

**Files:**

- Modify: `src/langharness/api_guard.py`(整体重写)
- Modify: `tests/test_api_guard.py`(适配 + 新增)

**Interfaces:**

- Consumes: websockets sync client;`/clients/attach`(Task 2)
- Produces: `APIGuard(base_url="http://127.0.0.1:11534", *, config_dir=None, startup_timeout=30.0, token="secret")`;方法 `is_running() -> bool`、`ensure_api_server() -> None`、`attach_client() -> None`、`detach_client() -> None`

- [ ] **Step 1: 重写 api_guard.py**

`src/langharness/api_guard.py` 全文替换为:

```python
"""Detects and auto-starts the API server, then holds a client lifeline."""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlparse

import httpx
from websockets.exceptions import ConnectionClosedOK
from websockets.sync.client import connect

LOGGER = logging.getLogger("langharness.api_guard")


class APIGuard:
    """Starts the API server on demand and keeps it alive while attached.

    Each CLI process opens one websocket "lifeline" to the server after
    ensuring it runs; the server shuts itself down when the last lifeline
    closes. The guard never terminates a server it started.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11534",
        *,
        config_dir: str | None = None,
        startup_timeout: float = 30.0,
        token: str = "secret",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.config_dir = config_dir
        self.startup_timeout = startup_timeout
        self.token = token
        self._process: subprocess.Popen[bytes] | None = None
        self._ws: Any = None
        self._hooks_installed = False

    def is_running(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=1.0)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    def ensure_api_server(self) -> None:
        if self.is_running():
            return
        lock = self._acquire_startup_lock()
        try:
            if self.is_running():
                return
            parsed = urlparse(self.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 11534

            command = [sys.executable]
            if not getattr(sys, "frozen", False):
                command += ["-m", "langharness"]
            command += [
                "--mode",
                "server",
                "--server-ip",
                host,
                "--server-port",
                str(port),
                "--auto-shutdown",
            ]
            if self.config_dir is not None:
                command += ["--config-dir", self.config_dir]
            self._process = subprocess.Popen(command)

            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self.is_running():
                    return
                time.sleep(0.1)

            self._process.terminate()
            self._process = None
            raise RuntimeError("API server did not become ready in time")
        finally:
            self._release_startup_lock(lock)

    def attach_client(self) -> None:
        """Open the client lifeline websocket; failures only warn."""
        if self._ws is not None:
            return
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11534
        ws_url = f"ws://{host}:{port}/clients/attach"
        try:
            self._ws = connect(
                ws_url,
                additional_headers={
                    "Authorization": f"Bearer {self.token}"
                },
                open_timeout=5.0,
            )
        except Exception as exc:
            LOGGER.warning("client lifeline failed to attach: %s", exc)
            return
        threading.Thread(
            target=self._drain_ws, name="langharness-lifeline", daemon=True
        ).start()
        self._install_exit_hooks()

    def detach_client(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _drain_ws(self) -> None:
        try:
            for _message in self._ws:
                pass
        except ConnectionClosedOK:
            LOGGER.debug("client lifeline closed")
        except Exception as exc:
            LOGGER.warning("client lifeline lost: %s", exc)

    def _install_exit_hooks(self) -> None:
        if self._hooks_installed:
            return
        self._hooks_installed = True
        atexit.register(self.detach_client)
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(signum, self._signal_handler)
            except (ValueError, OSError):
                pass  # not the main thread

    def _signal_handler(self, signum: int, _frame: FrameType | None) -> None:
        self.detach_client()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def _acquire_startup_lock(self) -> Path | None:
        if self.config_dir is None:
            return None
        directory = Path(self.config_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "api_server.lock"
        deadline = time.monotonic() + self.startup_timeout
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._lock_is_stale(path):
                    path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "another client is starting the API server"
                    )
                time.sleep(0.1)
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            return path

    def _release_startup_lock(self, path: Path | None) -> None:
        if path is not None:
            path.unlink(missing_ok=True)

    @staticmethod
    def _lock_is_stale(path: Path) -> bool:
        try:
            pid = int(path.read_text().strip())
        except (OSError, ValueError):
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False
```

- [ ] **Step 2: 重写 tests/test_api_guard.py**

`tests/test_api_guard.py` 全文替换为:

```python
"""Unit tests for the API-server guard owned by the unified bootstrap."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

import os
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
    states = [False, True]
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
    states = [False, True]
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
        lambda command: type("P", (), {"terminate": lambda self: None, "poll": lambda self: None})(),
    )
    monkeypatch.setattr(api_guard_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(api_guard_module.time, "monotonic", lambda: 0.0)

    guard = APIGuard(config_dir=str(tmp_path))
    states = [False, True]
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
    ticks = iter([0.0, 100.0])
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
```

注:两个 `ConnectionClosed*` 构造调用在 websockets 16 中需要 `(rcvd, sent)` 两参;若签名不同,以实际异常构造为准(测试内已有 import 处校验)。

- [ ] **Step 3: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api_guard.py -q`
Expected: PASS(现有 4 个用例被替换,新用例全绿)

- [ ] **Step 4: 全量回归**

Run: `.venv/bin/python -m pytest -q -k "not real_llm"`
Expected: PASS(注意此时 test_bootstrap 里 `_guard_spy` 尚无 attach_client,Task 5 修复)

- [ ] **Step 5: 提交**

```bash
git add src/langharness/api_guard.py tests/test_api_guard.py
git commit -m "feat(cli): hold a client lifeline instead of killing the server"
```

---

### Task 5: bootstrap 接线

**Files:**

- Modify: `src/langharness/bootstrap.py`(`_token_from_argv` 助手 + `--mode all` 分支)
- Modify: `tests/test_bootstrap.py`(`_guard_spy` 与 mode-all 断言)

**Interfaces:**

- Consumes: `APIGuard` 新接口(Task 4)
- Produces: `_token_from_argv(argv: list[str]) -> str`

- [ ] **Step 1: 写失败测试**

`tests/test_bootstrap.py` 追加:

```python
def test_token_from_argv_defaults_to_secret() -> None:
    assert bootstrap_module._token_from_argv([]) == "secret"


def test_token_from_argv_parses_flag_forms() -> None:
    assert bootstrap_module._token_from_argv(["--token", "abc"]) == "abc"
    assert bootstrap_module._token_from_argv(["--token=xyz"]) == "xyz"
    assert bootstrap_module._token_from_argv(["interactive", "--token", "q"]) == "q"
```

修改 `_guard_spy`(约 tests/test_bootstrap.py:278-300):

```python
    def guard(base_url: str, **kwargs) -> SimpleNamespace:
        calls: list[str] = []
        guards.append((base_url, kwargs, calls))
        return SimpleNamespace(
            ensure_api_server=lambda: calls.append("ensure"),
            attach_client=lambda: calls.append("attach"),
        )
```

修改 `test_run_guards_api_server_in_all_mode`:

```python
def test_run_guards_api_server_in_all_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    base_url, kwargs, calls = _guard_spy(monkeypatch, "all", tmp_path)[0]
    assert base_url == "http://127.0.0.2:19000"
    assert kwargs == {"config_dir": str(tmp_path), "token": "secret"}
    assert calls == ["ensure", "attach"]
```

`test_run_does_not_guard_api_server_in_ui_mode` 保持不变。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q -k "token_from_argv or guards_api_server"`
Expected: FAIL(AttributeError: 模块无 `_token_from_argv`;attach_client 缺失)

- [ ] **Step 3: bootstrap.py 接线**

Modify `src/langharness/bootstrap.py`,在 `base_url_for` 之后加入:

```python
def _token_from_argv(argv: list[str]) -> str:
    for index, arg in enumerate(argv):
        if arg == "--token" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--token="):
            return arg.split("=", 1)[1]
    return "secret"
```

`_run` 中 `--mode all` 分支:

```python
        if options.mode == "all":
            APIGuard(base_url, config_dir=options.config_dir).ensure_api_server()
```

改为:

```python
        if options.mode == "all":
            guard = APIGuard(
                base_url,
                config_dir=options.config_dir,
                token=_token_from_argv(remainder),
            )
            guard.ensure_api_server()
            guard.attach_client()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_bootstrap.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness/bootstrap.py tests/test_bootstrap.py
git commit -m "feat(cli): attach the client lifeline after ensuring the server"
```

---

### Task 6: 端到端生命周期测试

**Files:**

- Create: `tests/test_server_lifecycle_e2e.py`

**Interfaces:**

- Consumes: bootstrap `--auto-shutdown` / `--auto-shutdown-grace`(Task 3)、`/clients/attach`(Task 2)、APIGuard 行为(Task 4/5)
- Produces: 无(仅测试)

- [ ] **Step 1: 写失败测试(此时全链路已通,测试应直接 PASS;若 FAIL 则先修产品代码)**

`tests/test_server_lifecycle_e2e.py`:

```python
"""End-to-end tests for the multi-client server lifecycle."""
# mypy: ignore-errors

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    paths = [str(ROOT / "src"), *env.get("PYTHONPATH", "").split(os.pathsep)]
    env["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
    env["LANG_HARNESS_DIR"] = str(tmp_path)
    return env


def wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("API server did not become ready")


def wait_for_exit(process: subprocess.Popen, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def spawn_server(
    tmp_path: Path, port: int, *, auto_shutdown: bool, grace: float
) -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "langharness",
        "--mode",
        "server",
        "--server-ip",
        "127.0.0.1",
        "--server-port",
        str(port),
        "--config-dir",
        str(tmp_path),
    ]
    if auto_shutdown:
        command += ["--auto-shutdown", "--auto-shutdown-grace", str(grace)]
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=make_env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def attach_client(port: int):
    return connect(
        f"ws://127.0.0.1:{port}/clients/attach",
        additional_headers={"Authorization": "Bearer secret"},
    )


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


def test_server_exits_after_last_client_leaves(tmp_path: Path) -> None:
    port = free_port()
    process = spawn_server(tmp_path, port, auto_shutdown=True, grace=0.3)
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        first = attach_client(port)
        second = attach_client(port)

        first.close()
        time.sleep(0.8)  # grace elapsed; one client still attached
        assert process.poll() is None

        second.close()
        assert wait_for_exit(process)
    finally:
        stop_process(process)


def test_new_client_during_grace_cancels_shutdown(tmp_path: Path) -> None:
    port = free_port()
    process = spawn_server(tmp_path, port, auto_shutdown=True, grace=0.5)
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        first = attach_client(port)
        first.close()

        time.sleep(0.1)  # still within the grace window
        second = attach_client(port)
        time.sleep(0.8)  # grace would have elapsed
        assert process.poll() is None

        second.close()
        assert wait_for_exit(process)
    finally:
        stop_process(process)


def test_server_without_auto_shutdown_stays_up(tmp_path: Path) -> None:
    port = free_port()
    process = spawn_server(tmp_path, port, auto_shutdown=False, grace=0.0)
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        client = attach_client(port)
        client.close()
        time.sleep(0.8)
        assert process.poll() is None
    finally:
        stop_process(process)
```

- [ ] **Step 2: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_server_lifecycle_e2e.py -q`
Expected: PASS(3 passed,约 20-30 秒)

若 FAIL:先按失败信息修产品代码,回到本任务 Step 1 直至 PASS。

- [ ] **Step 3: 提交**

```bash
git add tests/test_server_lifecycle_e2e.py
git commit -m "test: cover the multi-client server lifecycle end to end"
```

---

### Task 7: 依赖声明与全量验证

**Files:**

- Modify: `pyproject.toml`(dependencies 追加)

- [ ] **Step 1: 声明 websockets 依赖**

Modify `pyproject.toml` dependencies 列表,在 `"uvicorn>=0.52.4",` 之后加:

```toml
    "websockets>=16.0",
```

- [ ] **Step 2: 全量检查**

Run: `.venv/bin/python -m pytest -q --cov=langharness --cov=langharness_scope --cov=langharness_config --cov=langharness_logging --cov=langharness_core --cov=langharness_plugin --cov=langharness_api --cov=langharness_cli --cov-report=term-missing --cov-fail-under=95`
Expected: PASS,coverage ≥ 95%(新模块 client_registry / lifecycle 路由 / api_guard 各分支已由上述测试覆盖;若 coverage 报告暴露未覆盖分支,补测试后再跑)

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m mypy && .venv/bin/python -m pyright --pythonpath .venv/bin/python && .venv/bin/python -m pytest tests/test_imports.py -q`
Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml
git commit -m "build: declare the websockets dependency"
```

- [ ] **Step 4: 人工冒烟(可选)**

Run: `.venv/bin/python -m langharness --mode all`(起一个 REPL),第二个终端同样命令,退出第一个终端,观察 server 进程在第二个终端退出后约 10 秒内自行退出:

```bash
ps aux | grep "mode.*server"
```

---

## Self-Review 记录

- **Spec 覆盖**:ClientRegistry ✓(Task 1);WS 路由 ✓(Task 2);runtime/uvicorn 接线与 timeout_graceful_shutdown ✓(Task 3);bootstrap 参数 ✓(Task 3/5);APIGuard 删除 atexit kill、frozen 统一 spawn、启动锁、命脉、退出钩子 ✓(Task 4);token 扫描 ✓(Task 5);e2e 三场景(最后退出/宽限取消/无 flag 不退)✓(Task 6);pyproject 依赖 ✓(Task 7)。
- **占位符扫描**:无 TBD/TODO;每个代码步骤附完整代码。
- **类型一致性**:`ClientRegistry` 的方法名在 Task 1/2/3 一致;`auto_shutdown`/`grace` 参数名在 contracts、runtime、bootstrap、e2e 一致;fixture `faked_uvicorn` 的注入写法已在 Task 3 Step 1 末尾修正说明。
- **已知遗留**:`test_api_guard.py` 中 `ConnectionClosed*` 的构造参数以 websockets 16 实际签名为准(Step 2 注已说明);coverage 若不足需按报告补测(Task 7 Step 2 注)。

---

## 执行偏差记录(2026-09-20 实施时发现)

1. **命脉路由不再走 RouteProvider 插件(Task 2 部分回退)**:FastAPI 不给
   WebSocket 路由的依赖注入 `Request` 参数——router 级 auth/rate-limit 依赖
   在 WS 握手时抛 `TypeError: dep() missing 1 required positional argument`,
   表现为 HTTP 500。改法:`ServerServerService.server()` 用
   `app.add_api_websocket_route("/clients/attach", lifeline, dependencies=...)`
   直接挂载;`AuthProvider` 契约新增 `get_websocket_dependency()`,其依赖取
   `WebSocket` 参数读 `websocket.headers`,校验失败抛 `HTTPException(401)`
   (FastAPI 在握手期转成 `websocket.close` → uvicorn 记录 403 拒绝)。
   路由插件 `plugins/routes/lifecycle.py` 及其 descriptor 已删除。
2. **APIGuard spawn 用 DEVNULL**:server 子进程若继承 CLI 的 stdout/stderr
   管道,CLI 退出后 server 按设计还要活宽限期,管道不 EOF,`subprocess.run`
   的 `communicate()` 被挂住直到 server 退出。server 有自己的日志插件
   写文件,stdio 重定向到 DEVNULL 无信息损失。
3. **契约校验要求测试 fake 带返回注解**:`ServerServerProvider`/`AgentServerProvider`
   的 monkeypatch 假实现若缺 `-> None` 等注解会被 `ContractGuard` 拒收
   (test_bootstrap、test_server_runtime 均已按此写)。
4. **test_module_services 适配**:原测试 monkeypatch `uvicorn.run`,重写后
   `server()` 不再调用它;改为 fake `uvicorn.Config`/`uvicorn.Server`,
   假 app 换成真 `FastAPI`(新代码调用 `app.add_api_websocket_route`)。
5. **test_cli_e2e 隔离配置目录**:原 `test_cli_auto_starts_api_server` 与
   `test_cli_reuses_running_api_server` 用真实 `~/.langharness`,受用户配置
   影响(启动 10-60 秒方差,超 30s 测试超时)且会向用户真实状态文件写入。
   改用 `tmp_path` + `--dir`,超时放宽到 60s。
6. **用户状态文件修复**:全量测试期间,临时存在的 lifecycle 插件被持久化进
   用户真实 `~/.langharness/runtime_state.sqlite3`;插件删除后 `restore()`
   在 `install_bundle` 阶段抛 `BundleException`,server 无法启动。已手动
   清理该快照中的 lifecycle descriptor/instance 记录(备份于
   /tmp/runtime_state.backup.sqlite3)。**遗留风险**:插件模块消失时
   `PluginManager.restore()` 会硬崩溃,建议后续加容错(忽略缺失模块并
   告警),超出本任务范围。
