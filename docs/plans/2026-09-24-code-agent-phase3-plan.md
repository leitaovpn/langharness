# Code Agent Phase 3：高保证与扩展

状态：进行中。PTC Phase 2 已完成；本文件记录 Phase 3 的细化 TODO 和验收证据。

## 已完成的小节

- [x] P3-A1 sandbox capability contract：新增 `SPEC_SANDBOX`、`SandboxProvider` 和
  `sandbox` 插件目录项。
  - 无受支持 backend 时返回 `isolation="process-only"`，不会宣称 OS sandbox。
  - 检测到 `sandbox-exec` 或 `bwrap` 时返回 `isolation="os-sandbox"`，并提供 argv wrapper。
  - 证据：`tests/test_sandbox.py`，`.venv/bin/pytest -q tests/test_sandbox.py tests/test_imports.py`
    → `3 passed`；Ruff、mypy 通过。

## 待完成的小节

- [ ] P3-A2 将 sandbox provider 接入 exec foreground/background 和 PTC runtime；记录实际
  isolation，backend 不可用时保持 process-only。
- [ ] P3-A3 sandbox policy：workspace 读写范围、网络默认拒绝、环境白名单、资源限制和
  symlink 防逃逸；为 macOS `sandbox-exec` 与 Linux `bwrap` 分别测试。
- [ ] P3-B1 audit persistence：把 `ptc_audit` 和 exec/job/approval 事件写入 append-only
  SQLite，按 agent/session 查询，保留 stream 摘要。
- [ ] P3-C1 reviewer provider：可选 reviewer agent，只产生独立证据，不绕过 verify/finish
  的确定性门禁；缺少 provider 时明确 disabled。
- [ ] P3-D1 web/skills/delivery：按 scope 可选装配，声明 schema、权限和 artifact；默认不启用。
- [ ] P3-E1 failure recovery：跨进程 job/Node 子进程恢复、重试边界、幂等 call id 和取消排空。
- [ ] P3-E2 context compression：按 token/字节预算压缩历史，保留目标、验证报告、审计引用
  和未完成审批；增加 checkpoint round-trip 测试。
- [ ] P3-E3 cross-process goal resume：从 SQLite checkpoint 恢复 agent/session/goal 状态，
  防止重复完成和 revision 回退。
- [ ] P3-F1 real provider smoke：设置 `LANG_HARNESS_REAL_LLM_PROVIDER`、密钥和 protocol，
  对 chat/anthropic/responses 各执行一轮；保存原始 NDJSON、artifact、checkpoint 摘要。
- [ ] P3-F2 final gate：P3 全部完成后运行 `make check`，覆盖率至少 95%，并完成真实 provider
  smoke 报告。

## 当前环境证据

当前 shell 未设置 `LANG_HARNESS_REAL_LLM_PROVIDER`，因此真实外部模型验证尚未执行；fake
model 的三协议 PTC 证据见 `docs/plans/2026-09-23-code-agent-ptc-plan.md`。
