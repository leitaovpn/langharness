# 多客户端 Server 生命周期设计方案

## 目标

支持多个 CLI 进程共享同一个 server 进程:每个 CLI 进程算一个客户端,只有当
最后一个客户端断开后,server 才自动优雅退出。机制参考 deepseek-harness 的
"传输层生命周期"设计(server 生命周期绑定到客户端连接,崩溃场景由 OS 兜底),
移植到 langharness 的 HTTP 架构上。

## 背景与现状

当前 server 生命周期由 `APIGuard`([api_guard.py](../../src/langharness/api_guard.py))
管理:

- CLI 启动时 `ensure_api_server()` 探测 `http://127.0.0.1:11534/health`,不通则
  `subprocess.Popen` 拉起 `python -m langharness --mode server`;
- CLI 进程退出时 `atexit` 杀掉自己拉起的 server 子进程;
- frozen 打包版走进程内 `serve()` 阻塞分支,不支持多客户端;
- server 本体是 [runtime.py](../../src/langharness_api/plugins/server/runtime.py)
  中阻塞式 `uvicorn.run`。

问题:CLI A 拉起 server 后先退出,server 被 A 杀掉,即使 CLI B 仍在使用;
同时两个 CLI 同时启动存在 spawn 竞态(都发现端口未通、都尝试拉起)。

## deepseek-harness 参考

deepseek-harness                        langharness 对应
exitOnStdinEnd:退出绑定客户端管道 EOF   WS 长连接,崩溃时 OS 关 socket
appReady 门控:启动未完成不触发退出       ClientRegistry.mark_ready()
window-all-closed → quit:归零才退出      连接计数归零 → 宽限期 → 退出
process-shutdown.ts:有界优雅退出          uvicorn 优雅退出 + 兜底超时
客户端 close():关传输 → 等 → 逐级强杀    CLI 只关 WS,由 server 自行决策

## 总体架构

```text
CLI A ──WS 命脉──┐
CLI B ──WS 命脉──┼──▶ server 进程 (uvicorn)
                 │     ├─ ClientRegistry:活跃连接集合
                 │     └─ 归零 → 10s 宽限 → should_exit → 优雅退出
```

- 客户端 = CLI 进程。启动时(自动拉起 server 后)开一条 WebSocket 作为"命脉",
  退出时断开;
- server 统计活跃 WS 连接数,归零后启动 10 秒宽限期,期间有新连接则取消,
  到期仍为零则触发优雅退出;
- 崩溃安全:CLI 进程死亡(含 SIGKILL)时 OS 自动断开 socket,server 立即感知,
  无需心跳、无僵尸期;
- 只有 `APIGuard` 自动拉起的 server(`--auto-shutdown`)才自动退出;用户手动
  `--mode server` 行为不变,永不自动退;
- WS 意外断开时 CLI 不重连(server 崩溃则行为与现状一致:后续请求报错)。

## Server 端

### ClientRegistry

新类,放 `langharness_api/common/client_registry.py`。普通运行时状态组件,
经 `app.state` 注入,不走插件系统(不是插件贡献)。

```python
class ClientRegistry:
    def __init__(self, grace: float = 10.0) -> None:
        self._clients: set[Any] = set()
        self._grace = grace
        self._ready = False
        self._enabled = False
        self._shutdown_callback: Callable[[], None] | None = None
        self._timer: asyncio.Task | None = None

    def set_shutdown_callback(self, callback: Callable[[], None]) -> None: ...
    def set_enabled(self, enabled: bool) -> None: ...
    def mark_ready(self) -> None: ...
    def attach(self, ws) -> None: ...
    def detach(self, ws) -> None: ...
    def active_count(self) -> int: ...
```

行为:

- `attach` 加入集合;若有未到期的关闭定时器,取消(宽限期内新客户端接入);
- `detach` 移除;集合归零且 `_ready` 且 `_enabled` 时,创建 asyncio task,
  sleep `_grace` 秒后检查集合仍为零,调用 `_shutdown_callback`(至少一次);
- `mark_ready` 置位,由 FastAPI lifespan startup 调用;ready 之前归零不排关闭
  (对应 deepseek 的 `appReady` 门控,防止启动期间客户端接入又断开误触发);
- 未 `mark_ready` 时已归零,`mark_ready` 之后排一次关闭(server 拉起后没有任何
  客户端时也要能自退)。

### WS 路由插件

新文件 `langharness_api/plugins/routes/lifecycle.py`,factory
`api-lifecycle-route-factory`,在 [plugin.py](../../src/langharness_api/plugin.py)
注册 descriptor,写法参照 health 路由。路由:

- `GET /clients/attach`:accept → `websocket.app.state.client_registry.attach(ws)`
  → 循环 receive(内容忽略)→ `WebSocketDisconnect` → detach;
- registry 为 None(理论不可达,防御)时 accept 后直接循环等待断开,不计数;
- auth:router 级 `Authorization: Bearer` 依赖自动适用,与其它路由一致。

### runtime.py 改造

`ServerServerService.server` 签名与实现:

```python
def server(self, host: str, port: int, auto_shutdown: bool = False,
           grace: float = 10.0) -> None:
    ...
    config = uvicorn.Config(
        app, host=host, port=port, log_config=None,
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

- `should_exit = True` 后 uvicorn 停止接收新连接、等待 in-flight 请求完成再退出;
  `timeout_graceful_shutdown=10` 兜底卡死请求(对应 deepseek 的 5s 有界退出);
- `server.run()` 返回后走 bootstrap `_run` 的 `finally: manager.stop()` 正常清理;
- `mark_ready` 由 FastAPI lifespan 钩子调用:钩子挂在 `build_app` 创建的 app 上,
  回调运行时从 `app.state.client_registry` 取值(此时 `server()` 已注入,
  早于 uvicorn 开始服务)。

### bootstrap 参数

`parse_options` 新增:

```text
--auto-shutdown           server 在最后一个客户端断开后自动退出(默认关)
--auto-shutdown-grace N   宽限期秒数(默认 10)
```

`--mode server` 分支把两者传给 `server_service.server(...)`。

## CLI 端

### APIGuard 改造

[api_guard.py](../../src/langharness/api_guard.py):

- **删除** `atexit.register(self._terminate)` 与 `_terminate`——CLI 不再杀 server;
- spawn 命令追加 `--auto-shutdown`(source 与 frozen 两条路径统一);
- **frozen 统一 spawn**:删除进程内 `serve()` 阻塞分支,改为 spawn 自身可执行
  文件 `[sys.executable, "--mode", "server", ...]`。onedir 打包正常;onefile
  会重新解压,打包文档注明推荐 onedir;
- **启动竞态锁**:spawn 前获取 `config_dir/api_server.lock`(使用
  `os.open(..., O_CREAT | O_EXCL)` 写入 pid;锁已存在时读 pid,`os.kill(pid, 0)`
  报 `ProcessLookupError` 视为 stale,清除后重试;短超时循环等待)。拿到锁才
  执行 check + spawn + 等 health,完成后释放。两个 CLI 同时启动只拉起一个
  server;
- 新增 `attach_client(token: str = "secret")` / `detach_client()`:
  - `attach_client`:用 `websockets.sync.client.connect(ws://host:port/clients/attach,
    additional_headers={"Authorization": f"Bearer {token}"})` 开命脉,保存连接;
    连接失败只记警告,不阻塞 CLI 启动;
  - `detach_client`:幂等关闭连接;
  - 同时注册 `atexit` 与 `SIGTERM`/`SIGINT` handler 调 `detach_client`(Python 的
    atexit 对 SIGTERM 不生效,必须挂 signal handler;SIGINT handler 关完连接后
    重新抛出,保持既有 Ctrl+C 语义)。

### bootstrap 接线

`--mode all` 分支:`ensure_api_server()` 之后 `attach_client()`,token 从
remainder argv 扫描 `--token`(默认 "secret",与 server 端 auth 默认一致)。

## 关键时序

1. CLI A 启动:拿锁 → health 不通 → spawn(`--auto-shutdown`)→ 轮询 health OK
   → 释放锁 → attach WS;
2. CLI B 启动:拿锁 → health 已通 → 不 spawn → 释放锁 → attach WS;
3. CLI A 退出(正常/异常/SIGTERM)→ WS 断 → registry 2→1,不动作;
4. CLI B 退出 → WS 断 → registry 1→0 → 10s 宽限无新连接 → `should_exit` →
   uvicorn drain 后进程退出。

## 错误处理与边界

场景                                行为
CLI 崩溃(SIGKILL)                   OS 断 socket,server 按归零流程关
CLI 在 spawn 后、attach 前崩溃      server 零客户端,ready 后 10s 自动退
宽限期内新客户端接入                取消关闭
WS 意外断开(server 崩溃)            CLI 记警告继续运行,不重连
手动 `--mode server`                enabled=False,永不自动退
in-flight 请求卡死                  `timeout_graceful_shutdown=10s` 兜底

## 依赖

`websockets`(当前环境已随 uvicorn 生态安装)需加入 `pyproject.toml`
dependencies 显式声明,CLI 端使用其同步客户端 API。

## 测试

- **ClientRegistry 单测**:attach/detach 计数、宽限到期关闭、宽限内接入取消、
  ready 前不关闭、ready 时已归零补排一次、enabled=False 不关闭;
- **server 集成**:真实拉起 server(grace 调小如 0.1s),两个 WS 客户端:断一个
  不关;断两个在宽限后退出;宽限期内接入取消关闭;
- **APIGuard**:两个 APIGuard 并发 ensure 只拉起一个 server(锁);CLI 退出后
  server 自动关闭;
- **回归**:手动 `--mode server`(不带 `--auto-shutdown`)零客户端不退出;
  frozen spawn 路径的 argv 拼装单测。
