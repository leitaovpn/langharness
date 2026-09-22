# 自动关服宽限期在首个客户端接入前就被消耗

日期：2026-09-21
状态：已解决（按"修复方向 1"把测试的窗口放到启动开销之外；两种复现 + 12 个 CPU 燃烧进程下复验）

## 现象

`tests/test_server_lifecycle_e2e.py::test_server_exits_after_last_client_leaves`
在 `attach_client` 处间歇性失败，同一个调用点会出现两种错误之一：

- `websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 500`
- `builtins.ConnectionRefusedError: [Errno 111] Connection refused`

单独运行通过，整仓高负载并发下复现；首次观察到的是 HTTP 500 形态。
两种形态都指向同一行，会让排查方向在「服务端崩了」和「端口没起来」之间来回跳。

修这个问题时补测到两件事：

- **还有第三种形态**：`wait_for_health` 自己抛 `AssertionError: API server did not become ready`
  （同负载、宽限期 0.3s 时出现在某一轮）。报错位置因此还会漂到 `wait_for_health`，
  比前两种更像「服务端起不来」，而不是「接入太快失败」。这一形态说明窗口紧到连健康检查
  都无法保证落地，而不只是两次握手来不及 —— 注意不能反过来说「服务端一定在应答 /health
  之前就关了」：同一负载下 grace 0.15–0.5 的多数轮次里 `/health` 是能应答的，
  所以走到哪一种形态取决于这一轮的启动有多慢。
- **同文件的 `test_new_client_during_grace_cancels_shutdown` 有同一个缺陷**，只是窗口稍宽
  （0.5s）所以先没暴露。同样负载下它以 `ConnectionRefusedError` 失败过一次。
  也就是说这不是某一个测试的时序没调好，而是这两个测试都在抢同一段窗口。

## 最小复现

两个脚本，一个复现故障，一个复现机制。

### 1. `docs/problem/repro/repro_attach_race.py` —— 带负载复现故障

复刻该测试的流程（spawn 服务器 + `--auto-shutdown --auto-shutdown-grace 0.3`
+ 健康检查 + 接两个客户端），区别是保留服务端 stdout/stderr 而不是丢进
`DEVNULL`，并起 N 个 CPU 燃烧进程把「健康检查返回 → 接入完成」的间隙拉长：

```
.venv/bin/python docs/problem/repro/repro_attach_race.py 30 12
```

```
30 rounds, 12 cpu burners, grace=0.3s
FAIL: round 0: websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 500
FAIL: round 1: websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 500
round 2: health->attach 0.160s (grace 0.3s)
round 3: health->attach 0.219s (grace 0.3s)
...
FAIL: round 7: builtins.ConnectionRefusedError: [Errno 111] Connection refused
reproduced 3 time(s)
```

**对照实验**：同一脚本、同一负载、同一台机器，只把宽限期换成 2.0s：

```
REPRO_GRACE=2.0 .venv/bin/python docs/problem/repro/repro_attach_race.py 15 12
→ 15/15 通过，not reproduced
```

唯一的自变量是宽限期窗口。

### 2. `docs/problem/repro/repro_attach_500_mechanism.py` —— 无负载确定性复现 500

只用 uvicorn + FastAPI，不含本项目代码：一条 `await asyncio.sleep(1.0)` 之后才
`accept()` 的 websocket 路由，在握手飞行途中把 `should_exit` 置位（正是注册表
定时器到期时服务端所处的状态），并断言服务端「回了 500 且什么都没记」：

```
python docs/problem/repro/repro_attach_500_mechanism.py
client saw: websockets.exceptions.InvalidStatus: server rejected WebSocket connection: HTTP 500
server logged during shutdown: 'Shutting down\n> HTTP/1.1 500 Internal Server Error\n...'
OK: 500 with no server-side log
```

## 根因

宽限期从**服务端启动完成**开始计时，而不是从**第一个客户端接入**开始，
所以测试要在这段窗口里完成的动作，比它自己以为的多。

1. `src/langharness_api/plugins/server/app.py:34-36`：lifespan 启动时调用
   `registry.mark_ready()`。这一步在 uvicorn 开始 accept 之前完成，即整个
   服务端启动过程都在窗口内。
2. `src/langharness_api/common/client_registry.py:33-36`：`mark_ready()` 发现
   `self._clients` 为空 → 立即 `_schedule_shutdown()`，起一个
   `asyncio.sleep(grace)` 定时器。`attach()`（第 38-40 行）会取消它 ——
   只要客户端在窗口内到达就没事。
3. `src/langharness_api/plugins/server/runtime.py:100-104`：定时器到期且仍无
   客户端 → 回调把 `server.should_exit` 置为 `True`，uvicorn 进入优雅关闭。

于是测试实际可用的窗口是：**lifespan 启动 → `/health` 可应答 →
`wait_for_health` 的轮询（每 0.1s 一次，`tests/test_server_lifecycle_e2e.py:41`）
发现它 → 两次 websocket 握手**。实测「健康检查返回 → 两次握手完成」本身就要
0.13–0.22s，这还没算服务端启动和轮询延迟 —— 0.3s 的预算在测试开始测量之前
就已经花掉大半。

窗口用尽时，客户端 connect 落在关闭序列的哪一步决定看到哪种错误：

- **Connection refused**：uvicorn 已关闭监听 socket，新连接直接被拒。
- **HTTP 500**：TCP 已被接受、协议对象已建立，但 ASGI 任务还没跑到
  `websocket.accept()`；此时 uvicorn 排空连接会调用协议的 `shutdown()`，
  而它对「握手未完成」的连接一律回 500。三个 websocket 实现行为一致：

  ```
  websockets_sansio_impl.py:177-192  shutdown(): if not self.handshake_complete: self.send_500_response()
  websockets_impl.py:152-158         shutdown(): if not handshake_completed_event.is_set(): self.send_500_response()
  wsproto_impl.py:206-220            shutdown(): if not self.handshake_complete: self.send_500_response()
  ```

  （本机是 uvicorn 0.53.0 + websockets 16.1.1，`ws="auto"` 因此选中
  `websockets_sansio_impl.py`；换实现或换版本都躲不掉，不是某个后端的怪癖。）

**这条路径不写任何日志**：`send_500_response()` 只构造响应，`shutdown()` 里
也没有 logger 调用。会写日志的是 `run_asgi()` 里另外两个 500 分支（ASGI 应用
抛异常、或应用未完成握手就返回）。所以服务端 stderr 干干净净 —— 这正是这个
问题看起来像「服务端莫名 500」而日志无迹可循的原因；确定性复现脚本专门断言了
这一点。

两种形态是同一个缺陷：不是服务器提前退出，也不是握手本身有问题，而是
**启动过程吃掉了客户端本应享有的全部宽限时间**。

## 影响

- **测试侧**：高负载下假失败，报错位置指向 `attach_client`，
  看起来像服务端缺陷；两种错误形态交替出现会进一步误导排查。
- **产品侧**（当前不易触发，但真实存在）：CLI 自拉服务端时同样带
  `--auto-shutdown`（`src/langharness/api_guard.py:77`），CLI 不传
  `--auto-shutdown-grace`，所以走默认 10s（`src/langharness/bootstrap.py:117-123`），
  窗口比测试大得多，正常不会踩到。但一旦接入失败，`attach_client()`
  只 `LOGGER.warning(...)` 就返回（`api_guard.py:115-116`），既不重试也不终止：
  结果是服务端在客户端「自认为已连接」的状态下按定时器自行退出。而且 CLI 是
  以 `stdout/stderr=DEVNULL` 拉起服务端的，这条告警在服务端侧根本看不到。

## 修复方向

（实施情况见文末修复记录。）可选方向，按推荐顺序：

1. **只改测试（最小、最贴合意图）**：把测试的 `grace` 放到明显大于
   「启动 + 轮询 + 两次握手」的时间（例如 1.0–2.0s），同时把「一个客户端仍在时
   不应关服」的 `time.sleep(0.8)` 按同一比例放大 —— 否则宽限期还没走完，
   该断言会退化成真空断言。生命周期语义与亚秒级时序无关，测试不需要抢这段窗口。
2. **测试侧加兜底**：把「首个客户端接入前服务端就没了」当作该轮重试而非失败。
   要注意不能吞掉异常 —— 真正的「服务端提前退出」回归会被一起盖住，
   所以必须是「换一个全新服务器重跑该轮」，而不是忽略错误继续。
3. **产品侧（可选）**：连接在排空期被回 500 且不留日志，从客户端视角就是
   「服务端 500」。可在注册表或 `lifeline` 侧补一条告警；`api_guard.attach_client()`
   也可以区分「服务端正在关闭」与其它失败，至少让「lifeline 没接上、服务端稍后
   会自行退出」这件事可见。

## 修复记录（2026-09-21）

按推荐顺序的第 1 条落地，只改测试：

`tests/test_server_lifecycle_e2e.py` 引入两个模块级常量，两个带 auto-shutdown 的测试都改用它们：

- `GRACE = 2.0`（原来是 0.3 与 0.5）；
- `GRACE_ELAPSED = GRACE + 0.5`，替换原来写死的 `time.sleep(0.8)` —— 宽限期变大后，
  「此刻服务端应该还活着」这类断言必须仍然跨过窗口，否则退化成真空断言。
  两个测试里那句 `0.1s` 保持原样：它的意思是「仍在窗口内」，窗口变大后语义不变。

常量上留了指向本文的注释，写清窗口为什么必须远大于「启动 + 轮询 + 握手」，避免以后又被调小。

### 复验

同一台机器、同一批负载，只改宽限期这一个自变量：

| 场景 | 旧值 | 新值 |
|------|------|------|
| `repro_attach_race.py`（12 燃烧进程） | grace 0.3 → 8 轮里前 3 轮连续失败（500 / 拒绝 / 未就绪 三种形态齐全） | grace 2.0 → 8/8 通过，`health->attach` 实测 0.168s、0.220s |
| `tests/test_server_lifecycle_e2e.py`（12 燃烧进程，整文件重复跑） | 5 次里失败 1 次（`test_new_client_during_grace_cancels_shutdown`，ConnectionRefusedError） | 6 次 0 失败 |
| 整仓 `pytest -q` | 报告中高负载下失败 | 915 passed, 1 skipped（原有 real-LLM skip），5:36 全绿 |

窗口的实际用量：`health -> 两次握手完成` 在高负载下实测 0.17–0.22s，`wait_for_health` 的轮询
粒度是 0.1s，再加上 mark_ready 之后 uvicorn 绑定端口并开始 accept 的那一小段 —— 也就是说
测试至少要花掉 0.27s 以上，旧窗口 0.3/0.5s 正好压在这条线上，新窗口 2.0s 才有余量。

（`mark_ready -> 首次接入` 这一段没能直接量到，记在这里免得下次再试一遍：服务端侧没有插桩，
而**从进程退出时间反推不可靠** —— uvicorn 在启动期间拿到 `should_exit` 会推迟到启动完成后
才真正退出，所以「退出时刻 − 宽限期」并不等于 mark_ready。）

### 没做的

- **第 2 条（把「首个客户端接入前服务端就没了」当作换一台新服务器重跑）**：窗口拉开后
  这条竞态已不存在，加了反而会盖住真正的「服务端提前退出」回归。保持裸断言。
- **第 3 条（产品侧可见性）**：仍未做。`api_guard.attach_client()` 失败时只 warning 就返回，
  服务端随后按定时器自行退出；默认 10s 窗口下不易触发（本次实测 `health -> 接上` 只要
  0.17–0.22s，窗口有几十倍余量），但一旦触发，CLI 侧表现为「自认为已连接、服务端却消失了」。
  留作后续项。
- 产品语义未改：`mark_ready()` 仍然在没有客户端时立刻起定时器。它的用途是防孤儿
  （启动后没人来接就自己退出），而这次的问题是**测试把窗口设得比启动开销还紧**，
  不是这个兜底策略本身错了。窗口起点仍在 lifespan 启动处 —— 也就是说服务端真正开始
  accept 之前的绑定时间也算在窗口内，这一点没有变。
