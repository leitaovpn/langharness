# Phase 3 验收矩阵

日期：2026-09-27

## 已通过

| 项目 | 证据 | 结论 |
| --- | --- | --- |
| PTC/exec 环境与资源边界 | `tests/test_ptc_runtime.py`、`tests/test_exec_runner.py`、`tests/test_job_manager.py` | PTC 环境白名单、超时、输出上限和 Linux rlimit 接入通过；Linux 实测项在 macOS 跳过 |
| 审计持久化 | `tests/test_audit_persistence.py` | SQLite round-trip、重开、agent/session 隔离、工具/job/审批元数据通过 |
| 审批恢复幂等 | `tests/test_audit_persistence.py::test_only_one_concurrent_resume_can_claim_an_approval` | 两个连接并发认领仅一个成功，重复恢复在图执行前拒绝 |
| 上下文压缩 | `tests/test_context_compression.py`、`tests/test_context_checkpoint.py` | 控制证据保护、工具调用/结果成组保留、SQLite checkpoint 重开通过 |
| reviewer 门禁顺序 | `tests/test_goal_tools.py`、`tests/test_reviewer.py` | verifier 是唯一完成权；reviewer 只产生附加 artifact，故障不撤销完成 |
| delivery manifest | `tests/test_delivery_tools.py`、`tests/test_imports.py` | 可选、agent-scoped、只读 SHA-256 manifest；路径/大小/数量边界通过 |
| 三协议真实 PTC smoke | `docs/evidence/ptc/2026-09-27/` | chat、anthropic、responses 均调用 `run_code` 并返回 5；NDJSON、tool/audit、checkpoint 摘要已保存 |
| 三协议真实 reviewer smoke | `docs/evidence/ptc/2026-09-27/reviewer-*.json` | 三个 provider 均返回 `completed`；摘要脱敏保存 |
| 代码质量 | `make check` | 最近完整门禁 1403 passed、5 skipped，覆盖率 95.23%；Ruff/mypy/Pyright/clean import 通过 |

## 部分通过或待外部环境

| 项目 | 当前证据 | 缺口 |
| --- | --- | --- |
| 历史失败 smoke | `docs/evidence/ptc/2026-09-25/`、`docs/evidence/ptc/2026-09-24-reviewer/` | 保留 403/空流/error 诊断；已被 2026-09-27 成功复测取代 |
| Linux bwrap | sandbox 单元测试 | 当前主机为 macOS，Docker daemon 未运行；没有真实 Linux namespace 验证 |
| web/skills/delivery | `tests/test_delivery_tools.py`、Phase 3 计划 P3-D1 | delivery manifest 与只读 `list_skills` inventory 已实现；web provider、技能执行/安装后端和发布后端仍未实现 |
| 跨进程恢复 | Phase 3 计划 P3-E1/E3 | job/PTC 子进程恢复、崩溃后已认领审批的处置策略、goal/checkpoint 联合恢复仍缺证据 |

## 验收判定

真实外部模型验证已完成：2026-09-27 三个协议均成功执行 `run_code` 并保存
脱敏 NDJSON、artifact、tool/audit 和 checkpoint 摘要；三个 reviewer smoke 也均
返回 `completed`。Phase 3 整体仍为进行中，尚缺以下条件：

1. 在真实 Linux 环境执行 bwrap namespace、网络隔离、路径边界和资源限制测试；
2. 实现并验收 web provider、skills 执行/安装后端和 delivery 发布后端的按 scope 可选装配；
3. 完成跨进程 job/PTC/goal 恢复及崩溃重试边界；
4. 补齐 token 预算、结构化控制证据识别、异常消息历史策略及更广泛的 checkpoint/stream 验证。
