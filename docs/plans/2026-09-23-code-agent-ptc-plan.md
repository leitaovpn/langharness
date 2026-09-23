# Code Agent Phase 2：PTC 实施计划

状态：进行中。Phase 1 已由 `9449e0c` 完成确定性验收；真实模型 smoke 默认跳过。

## 细化 TODO 与验收记录

### A. 已验收

- [x] A1 模式契约与 scoped provider：`native | ptc | both`，默认 native。
  - 证据：`tests/test_presentation_mode.py`、`tests/test_core_descriptors.py`。
- [x] A2 Typed SDK：稳定排序、输入/输出 schema、排除 `run_code`。
  - 证据：`tests/test_ptc_sdk.py`、`tests/test_ptc_output_schemas.py`。
- [x] A3 Node runtime：JSON 行协议、TypeScript 擦除、deadline、取消、进程组清理、输出上限。
  - 证据：`tests/test_ptc_runtime.py` 的超时、取消、输出上限和可捕获错误测试。
- [x] A4 loop 接线：ptc 仅暴露 `run_code`，both 暴露原生工具和 `run_code`。
  - 证据：`tests/test_ptc_runtime.py::test_loop_ptc_mode_exposes_run_code_and_dispatches_scoped_tools`。
- [x] A5 子调用审计：root/parent/sub call id、参数、状态、结果、耗时；模型结果剥离 audit。
  - 证据：`tests/test_ptc_runtime.py::test_runtime_returns_only_program_output_and_process_isolation`。
- [x] A6 调度策略：只读调用最多 4 个并发，写调用串行。
  - 证据：`tests/test_ptc_runtime.py::test_loop_serializes_writes_but_allows_read_only_calls`。
- [x] A7 PTC prompt/preset：order 800 PTC-only section，新增 `ptc` preset，native 不注入。
  - 证据：`src/langharness_core/plugins/prompts/ptc.py`，目录导入测试通过。

本轮命令证据：

```text
.venv/bin/pytest -q tests/test_ptc_runtime.py
10 passed
```

最近一次完整质量门证据（提交 `63cfc83` 前）：

```text
make check
1336 passed, 2 skipped
Required test coverage of 95% reached. Total coverage: 95.58%
```

### B. 待完成的小节

- [x] B1 审计事件模型：定义统一事件类型和顺序字段，接入 agent stream 或持久化审计服务。
  - 事件包含 `type=ptc_audit`、`sequence`、root/parent/sub call id、工具名、参数、状态、
    结果/错误和耗时；`started`、`completed`、`failed`、`cancelled` 分开记录。
  - 证据：`tests/test_ptc_runtime.py::test_ptc_stream_emits_audit_separately_from_model_tool_output`，
    `test_timeout_audits_pending_child_as_cancelled`；组合测试 `22 passed`，Ruff、mypy 通过。
    stream 输出 `type=ptc_audit`，模型 `tool_output`
    不含 audit 字段。
- [x] B2 pending 生命周期：Node 退出、超时、取消时等待/取消所有 host dispatch，并测试无悬挂任务。
  - 证据：`tests/test_ptc_runtime.py::test_cancellation_cleans_up_pending_host_dispatch`，
    `.venv/bin/pytest -q tests/test_ptc_runtime.py` → `11 passed`；Ruff、mypy 通过。
- [ ] B3 授权拒绝：PTC 模式下原生工具直调返回结构化错误；both 模式允许两条路径。
  - 证据：`tests/test_ptc_runtime.py::test_ptc_rejects_direct_native_call_before_tool_runs`；
    fake model 直调 `danger` 得到 `Error: danger is not a valid tool, try one of [run_code]`，
    原工具执行记录为空。待验证：`both` 的原生和程序两条路径，以及明确错误码。
- [ ] B4 runtime 缺失：ptc/both 明确拒绝装配，不能静默降级 native。
  - 证据：`tests/test_ptc_runtime.py::test_ptc_requires_runtime_but_accepts_bound_runtime`。
    待验证：`both` 和真实 Pelix 服务缺失场景。
- [ ] B5 prompt 完整性：加入 order 5000 SDK section，并证明 prompt 中的 SDK 与 wire schema 相同。
  - 证据：`tests/test_ptc_runtime.py::test_ptc_prompt_contains_scoped_sdk_after_policy`，
    断言 order 800 先于 order 5000，SDK 含 `add` 且排除 `run_code`。
    待验证：模型实际收到的 prompt 与 wire schema 一致；`both` 模式指令正确。
- [ ] B6 scope 隔离：两个 agent scope 的工具、模式、SDK 和 runtime dispatch 互不串用。
  - 证据：`tests/test_ptc_runtime.py::test_ptc_scope_switch_changes_sdk_and_dispatch_target`；
    alpha scope 仅得到 `alpha_tool` 并返回 11，beta scope 仅得到 `beta_tool` 并返回 21；
    Ruff 通过，定向 pytest 通过。待验证：两个真实 agent scope 的模式注入和 prompt 隔离。
- [x] B7 Pelix + fake model E2E：验证模型收到的 schema、程序并发读取、写入串行、审计和取消。
  - 已验证：真实 Pelix/iPOPO 装配、模型只绑定 `run_code`、Node 子调用、审计事件、
    模型结果隔离。测试：`tests/test_e2e.py::test_real_pelix_ptc_stream_exposes_only_run_code_and_audit`。
  - 已验证：调度器交错读写屏障、PTC 原生直调拒绝、runtime 取消排空；定向 PTC 测试 `23 passed`。
- [x] B8 三协议验收：chat、anthropic、responses 各跑一轮，保存 NDJSON 结果和 artifact。
  - 已验证：上述 fake model Pelix 流程按三个协议配置各跑一轮，测试写入并回读
    `ptc-<protocol>.ndjson`，`3 passed`；当前证据使用 fake model 的三协议配置，真实外部
    provider smoke 仍按仓库约定跳过。
- [x] B9 完整质量门：所有 B 项完成后重新运行 `make check`，覆盖率保持至少 95%。
  - 证据：`make check`；Ruff、mypy、Pyright、clean-process imports 全部通过；
    `1351 passed, 2 skipped`，覆盖率 `95.46%`。

## 目标与边界

让同一组 agent scope 内的工具按 `native | ptc | both` 呈现。PTC 模式下，模型只看见
`run_code`，程序通过生成的 SDK 调用现有工具。LangGraph `ToolNode` 继续承担工具调用的
执行节点；它本身不提供程序运行时、host bindings、审计或模式授权，因此这些能力必须由
Phase 2 明确实现。

完成条件：设计文档第 7 节及第 11 节 Phase 2 的全部断言通过，并保持 `make check`
及至少 95% 覆盖率。每项改动先写失败测试，生产插件通过 Protocol 服务通信，不跨插件
导入具体实现。

## 接口与装配

- `ToolPresentationProvider.get_mode() -> Literal["native", "ptc", "both"]` 是按 agent
  scope 注入的模式服务。缺省为 `native`；未知值拒绝。它只声明模式，不复制工具注册表。
- agent loop 从现有 scoped `ToolProvider` 收集原生工具；模式判据同时用于模型请求的工具
  列表和 dispatch authorization。PTC 直调原生工具返回结构化错误，不能进入原工具。
- PTC runtime 也是服务契约。缺失时 `ptc` / `both` 装配失败并明确报错，不降级为 native。
- `run_code` 是外层工具，输入为 `code`、`description`、可选正数 `timeout_ms`；SDK 的工具
  集合按当前 scope 生成，排除 `run_code`。首版运行语言为可擦除 TypeScript，使用独立 Node
  进程；运行结果标明 `isolation="process-only"`。
- 子调用带父子 call id，重走现有工具策略与审计。只读调用可有限并发，写调用独占；程序
  退出、超时或取消时停止并排空未完成子调用。程序输出只包含显式日志和返回值，中间结果
  留在审计记录。

## 开发顺序

1. **模式契约与可选插件（已完成）**：增加 `SPEC_TOOL_PRESENTATION`、Protocol、Pelix provider、
   catalog 与 template。测试无配置时为 native、合法模式可设置、无效模式被拒绝、不同
   agent scope 可选不同模式。此步骤不启用 PTC wire schema。
2. **稳定 typed SDK（已完成）**：基于 LangChain 工具的输入 JSON schema 和明确的输出 schema 生成
   排序稳定的 TypeScript 声明。`run_code` 永不进入 SDK；未声明输出形状时用
   `unknown`，不能猜测字段。测试同一工具集文本稳定、scope 切换、复杂 schema 和名称冲突。
3. **进程运行时与 host binding**：新服务负责单次 Node 子进程、JSON 行协议、deadline、
   进程组清理、输出/内存/pending 上限；绑定请求在 host 校验参数并调用原工具。测试
   超时、取消、进程残留、输出截断、可捕获子调用错误和结束排空。
4. **调度与审计**：只读并发、写调用独占且按提交顺序；每个子调用记录开始和结算事件，
   带 root/parent/sub call id、参数、结果和耗时。测试并发屏障、审批/策略失败、不向模型
   泄漏中间结果。
5. **呈现与执行授权**：loop 加入 scoped presenter。模型请求按模式提供工具 schema；
   dispatch 使用同一 mode 判据阻断 PTC 原生直调。`both` 同时允许原生与程序调用；
   runtime 缺失时拒绝 PTC 模式。测试三模式 schema 与恶意直调，覆盖同步和异步路径。
6. **prompt 与 preset**：增加 order 800 的 PTC-only 指令和 order 5000 的 SDK section，
   把 provider/runtime 装入 PTC preset；保持现有 `code` preset 为 native。测试 prompt 排序、
   SDK 与 wire schema 一致、不同 agent 互不串 scope。
7. **端到端验收**：真实 Pelix + fake model 断言 PTC schema 严格为 `[run_code]`、程序内
   并发读取、独占写入、直调被拒绝、审计完整、取消排空；三协议各跑一轮。

Task 1 完成后仍只具备模式配置服务。Task 2–7 完成前不得把 `ptc` 加入默认 preset，也
不得在用户界面上宣称 PTC 已可用。
