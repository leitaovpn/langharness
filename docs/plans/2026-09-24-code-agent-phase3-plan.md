# Code Agent Phase 3：高保证与扩展

状态：进行中。PTC Phase 2 已完成；本文件记录 Phase 3 的细化 TODO 和验收证据。

## 已完成的小节

- [x] P3-A1 sandbox capability contract：新增 `SPEC_SANDBOX`、`SandboxProvider` 和
  `sandbox` 插件目录项。
  - 默认返回 `isolation="process-only"`。只允许显式配置的 `bwrap`；当前 macOS
    `sandbox-exec` 策略不完整，故拒绝启用，避免虚报 OS 隔离。
  - 证据：`tests/test_sandbox.py`，`.venv/bin/pytest -q tests/test_sandbox.py tests/test_imports.py`
    → `3 passed`；Ruff、mypy 通过。

## 待完成的小节

- [ ] P3-A2 将 sandbox provider 接入 exec foreground/background 和 PTC runtime；记录实际
  isolation，backend 不可用时保持 process-only。
  - 已完成 PTC runtime 接入：绑定 provider 时包装 Node argv，并返回 provider 的 isolation；
    未绑定时保持 `process-only`。
  - 已完成 foreground exec 接入；background job 使用同一包装后的 argv。
  - 证据：`tests/test_ptc_runtime.py::test_runtime_reports_bound_sandbox_isolation`、
    `tests/test_exec_tools.py::test_exec_command_uses_bound_sandbox_and_reports_isolation`；
    exec 定向测试 `24 passed`；backend policy/resource limits 仍待完成。
- [ ] P3-B1 audit persistence：新增 append-only SQLite audit provider，并由 agent loop 在
  stream 输出 `ptc_audit` 时写入持久化存储。
  - 已完成：默认装配、stream 与同步 `_run_code` 写入、重启后按序查询；stream PTC
    事件标记 agent/session 并支持按两者查询，旧版 SQLite 表自动迁移。
  - 证据：`tests/test_audit_persistence.py` round-trip、reopen、scope 查询、旧库迁移；
    与 PTC runtime/code-agent e2e 定向执行 `28 passed, 1 skipped`。
  - 新增：stream 将 exec 工具开始/结束、后台 job 开始/结束与审批请求写入审计库，
    只保存元数据，不保存命令参数、输出或审批参数；对应测试见
    `tests/test_audit_persistence.py`。
  - 新增：恢复流从持久化审计库查询同 agent/session 的未决审批，将决策类型以
    `decision_submitted` 和 `decision_applied` 两条追加事件关联到原审批 root；仅在图流
    正常结束后写入 applied，且不保存 resume 参数。测试覆盖审计库重开后关联、超出普通
    session 查询分页的未决请求，以及 agent 隔离。`decision_applied` 只表示图流处理完毕，
    不表示被审批工具一定执行成功。
  - 新增：同步 `invoke(thread_id=...)` 路径的 `_run_code` 审计记录 agent/session，
    `tests/test_audit_persistence.py::test_sync_invoke_scopes_ptc_audit_to_agent_and_session`
    使用真实 Node PTC 执行与 SQLite 查询验证。
  - 待完成：真实跨进程 agent/checkpoint 恢复时的审计事件关联验证，以及崩溃后的决策
    提交重试/幂等语义。
- [ ] P3-A3 sandbox policy：workspace 读写范围、网络默认拒绝、环境白名单、资源限制和
  symlink 防逃逸；为 macOS `sandbox-exec` 与 Linux `bwrap` 分别测试。
  - Linux `bwrap` argv 已改为仅映射系统运行时目录、指定 workspace 与可信只读文件；
    使用独立 PID/IPC/网络命名空间、清空环境并设置最小 PATH/HOME/TMPDIR。
    exec 传入 workspace 根目录，PTC 显式挂载 Node 可执行文件和 bootstrap 脚本。
  - 单元测试验证无宿主 `/` 映射、工作目录/软链越界拒绝；当前开发机没有 `bwrap`，
    尚无真实 Linux bwrap 执行验证。非 Linux 平台显式拒绝 `bwrap`。资源限制、
    macOS 可用策略仍未完成。
  - exec foreground/background 现在在 Linux 子进程设置 CPU（按 timeout）与地址空间
    上限；macOS/Windows 保留已有 wall-clock、输出字节和 job 数上限。证据：
    `tests/test_exec_runner.py`、`tests/test_job_manager.py` 定向 `39 passed`。
- [ ] P3-C1 reviewer provider：可选 reviewer agent，只产生独立证据，不绕过 verify/finish
  的确定性门禁；缺少 provider 时明确 disabled。
  - 已新增 `ReviewerProvider` 与可选 agent plugin catalog 项；默认 disabled，返回明确
    `status=disabled`，即使启用占位实现也只返回独立元数据并声明 `completion_authority=verifier`。
  - 证据：`tests/test_reviewer.py`（契约、disabled 行为、catalog descriptor）。尚未接入
    外部 reviewer 模型和持久化 review artifact。
- [ ] P3-D1 web/skills/delivery：按 scope 可选装配，声明 schema、权限和 artifact；默认不启用。
- [ ] P3-E1 failure recovery：跨进程 job/Node 子进程恢复、重试边界、幂等 call id 和取消排空。
- [ ] P3-E2 context compression：按 token/字节预算压缩历史，保留目标、验证报告、审计引用
  和未完成审批；增加 checkpoint round-trip 测试。
  - 已新增纯函数 `compress_messages` 和纯状态投影
    `ContextCompressionTransformer`：按字节预算保留最新消息，并优先保留 goal、
    verification、audit、approval、unfinished 标记的控制证据；输入预算非法时拒绝。
  - 证据：`tests/test_context_compression.py`。控制证据超出预算时现在明确报错，不再静默丢弃。
  - 已改走真实 `before_model` middleware：显式安装该可选 middleware 后，在
    `plugin.context.budget_bytes > 0` 时返回 state update，否则不注入；它不使用
    `create_agent(transformers=...)` 的流事件接口。证据：`tests/test_context_middleware.py`。
    已通过真实 `create_agent` + SQLite checkpoint 的跨重启 round-trip 测试：
    `tests/test_context_checkpoint.py` 验证保留 goal、新回合继续、过大的无关旧消息被删。
  - 已验证 LangGraph `add_messages` reducer：单纯返回压缩后的子集不会删除旧消息；
    middleware 现在在确需压缩时先发 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 再写入保留
    消息。见 `test_context_middleware_replaces_checkpoint_history`。
  - 已按 `tool_call_id` 将 AI 调用与全部匹配的工具结果作为不可拆分的组，预算不足时整体
    跳过；控制证据所在组预算不足时拒绝压缩。`tests/test_context_compression.py` 覆盖真实
    LangChain 消息和多调用字典消息。
  - 待完成：token 预算、结构化控制证据识别、缺失调用/结果的异常历史策略，以及更广泛的
    checkpoint/stream 语义验证；现有测试仅证明上述单线程 round-trip 场景。
- [ ] P3-E3 cross-process goal resume：从 SQLite checkpoint 恢复 agent/session/goal 状态，
  防止重复完成和 revision 回退。
  - 已补 goal SQLite 的 `agent_id` 隔离和数据库级 revision CAS；同一数据库内两个
    真实 iPOPO agent 实例重启后各自恢复，跨 agent 读写被拒绝；旧版无 agent 字段的表
    自动迁移，旧记录保留在空 agent scope，不会被猜测性分配给某个 agent。
  - 证据：`tests/test_goal_scope.py::test_shared_goal_database_is_scoped_after_framework_restart`、
    `tests/test_goal_store.py` 的双连接冲突与旧表迁移测试。尚缺 agent/session/checkpoint
    联合恢复及完成状态幂等的端到端证据。
  - 已加终态不可重开与 mutation generation 不回退约束；重启后的已完成 goal 不会
    重新成为 active，见 `tests/test_goal_store.py`。尚未完成 checkpoint 联合恢复。
- [ ] P3-F1 real provider smoke：设置 `LANG_HARNESS_REAL_LLM_PROVIDER`、密钥和 protocol，
  对 chat/anthropic/responses 各执行一轮；保存原始 NDJSON、artifact、checkpoint 摘要。
  - `tests/test_ptc_real_provider.py` 使用本地 provider 配置运行真实 PTC `run_code` 调用，
    将无密钥的摘要写入 pytest 临时目录。2026-09-23：`deepseek-v4-flash`（chat）
    `1 passed`，`claude-fable5`（anthropic）`1 passed`；两者均执行 `tools.add` 并返回 5。
  - `chatgpt-5`（responses）实际调用失败：上游 HTTP 500，响应事件缺少 `object` 字段；
    尚无可用的 Responses 兼容 provider 验收证据。成功运行的原始 NDJSON、artifact、
    checkpoint 摘要也尚未保存。
  - 2026-09-24 复测：真实 smoke 已支持在 `LANG_HARNESS_REAL_LLM_EVIDENCE_DIR` 指定目录
    保存脱敏 NDJSON 与 checkpoint/tool 摘要；证据见 `docs/evidence/ptc/`。
    当前 chat 与 anthropic 均在模型调用阶段返回 HTTP 403（免费额度耗尽），responses
    返回 `ValueError: No generations found in stream`，均未进入 `run_code`，本轮无成功
    artifact/checkpoint。此前 2026-09-23 两协议成功记录不能替代当前三协议验收。
  - 2026-09-25 复测：三个协议仍未进入 `run_code`。chat/anthropic 在模型调用阶段返回
    HTTP 403（额度耗尽）；responses 返回空流。脱敏失败摘要和 NDJSON 保存在
    `docs/evidence/ptc/2026-09-25/`，这些是失败诊断而非成功验收证据。
- [ ] P3-F2 final gate：P3 全部完成后运行 `make check`，覆盖率至少 95%，并完成真实 provider
  smoke 报告。
  - 当前中途门禁：2026-09-24 `make check` 通过；Ruff、mypy、Pyright、clean-process
    import、`1385 passed, 3 skipped`，总覆盖率 `95.36%`。3 个跳过项包括尚未纳入
    默认门禁的真实模型 smoke；这不是 Phase 3 最终验收。

## 当前环境证据

当前 shell 未设置 `LANG_HARNESS_REAL_LLM_PROVIDER`，但本地 provider 配置可供 opt-in
smoke 使用；已单独执行上述 chat 与 anthropic 验证。fake model 的三协议 PTC 证据见
`docs/plans/2026-09-23-code-agent-ptc-plan.md`。
