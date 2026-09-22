# 问题复现脚本

对应 `docs/problem/` 下的问题记录，均可用 `.venv/bin/python <script>` 直接运行
（除注明外不需要外部凭据）。

| 脚本 | 对应问题 | 说明 |
|------|----------|------|
| `repro_loop_death.py` | agent-loop-permanent-death | 假 LLM + 真实 API server 子进程；S1 带凭据 → S2 省略凭据 → 503，S3 仍 503。确定性复现 loop 永久死亡 |
| `repro_delta_drop.py` | stream-delta-drop | 假 LLM 按 chunk 依次发 `["scope","-marker","-","7","7"]`，`/stream` 只转发 `scope-marker-7`。确定性复现重复 delta 被吞 |
| `repro_bare_sdk.py` | responses-protocol-gateway-500 | 需在含 `langharness.toml`（`[providers.chatgpt-5]`）的目录下运行，用裸 openai SDK 对比 `/responses`（500）与 `/chat/completions`（正常）。会真实请求外部网关，按需运行 |
| `repro_ctrl_o_no_collapse.py` | ctrl-o-expansion-never-collapses | 假 LLM + 真实 CLI 跑在 pty 里，发一条消息后连按两次 ctrl+o：第一次出详情、第二次必须收回。修复前第二次只是再打一份，收不回。pty 会回应 CPR，屏幕即真实终端画面 |
| `repro_attach_race.py` | auto-shutdown-grace-race | 复刻生命周期测试的流程（spawn 服务端 + `--auto-shutdown --auto-shutdown-grace` + 健康检查 + 接两个客户端），保留服务端日志，并起 CPU 燃烧进程拉长「健康检查返回 → 接入完成」的间隙。参数为「轮数 燃烧进程数」，`REPRO_GRACE=2.0` 可对照宽限期 |
| `repro_attach_500_mechanism.py` | auto-shutdown-grace-race | 只用 uvicorn + FastAPI，不含本项目代码：握手飞行中把 `should_exit` 置位，断言服务端回 500 且**一条日志都不留**。无负载即可确定性复现 |

## 注意事项

- 需要起服务端的脚本都在 `$TMPDIR` 建隔离工作区并拉起/回收子进程，不污染 `~/.langharness`；
- `repro_loop_death.py` / `repro_delta_drop.py` 依赖 `tests/fixtures/fake_llm_server.py`（通过 `sys.path` 挂载 `tests/`）；
- `repro_attach_race.py` 会按需起 CPU 燃烧进程制造负载（默认 8 个），跑完自行回收；
- `repro_bare_sdk.py` 会消耗真实网关配额，且把 provider 的 api_key 读出到内存（不落盘）。
