# Code Agent Phase 1 开发路线图

> 本文是 `docs/designs/2026-09-22-code-agent-design.md` 第 11 节 Phase 1 的落地方案拆分。
> Phase 1 覆盖多个独立子系统，单个计划无法在合理篇幅内保持可执行性，因此按
> "每个计划结束时都有可独立验收的软件" 拆成 5 个子计划：1、2a、2b、3a、3b。
> 其中 1 / 2a / 3a 已完成，2b / 3b 尚未开始。

## 为什么拆开

设计第 11 节把 Phase 1 列为一组 bullet，但它们之间的耦合方向是单一的：

```text
prompt 体系  ──┐
               ├──> goal / verify / run controller ──> code preset 组装
workspace/exec ┘
```

- prompt 体系（ordered sections + repository instructions）不依赖其它任何部分，
  装上一个 provider 就能观测到行为变化；
- workspace / exec 工具只依赖 workspace realpath 这一个共享概念；
- goal / verify / run controller 同时依赖前两者的产物（prompt section 用于
  `goal-round` / `goal-wrapup`，`mutation_generation` 来自 workspace/exec 工具），
  必须最后做。

反向依赖不存在，所以这些计划可以严格顺序执行，每个都以一次 `make check` 通过
和一组新测试收尾。

## 计划清单

| 序号 | 计划 | 交付物 | 状态 |
|---:|---|---|---|
| 1 | `docs/plans/2026-09-22-code-agent-prompt-sections-plan.md` | agent loop 在每个 model step 用有序、具名、带预算的 sections 组装 system prompt；`AGENTS.md` 按 workspace→目标路径逐层注入并按 hash 失效 | **已完成**（8 个任务） |
| 2a | `docs/plans/2026-09-22-code-agent-workspace-tools-plan.md` | 受 workspace realpath 约束的 `list_directory` / `read_file` / `search_text` / `apply_patch` / `workspace_status`，以及 `ToolError` 与 mutation generation | **已完成**（8 个任务） |
| 2b | 尚未成文 | 有 deadline、输出上限、环境白名单、后台 job 归属的命令执行：`exec_command` / `write_stdin` / `wait_job` / `stop_job` | 骨架见本文；未开始 |
| 3a | `docs/plans/2026-09-22-code-agent-goal-plan.md` | 持久化 goal 状态机（revision CAS）、宿主验证器、6 条完成门禁、`get_goal` / `finish_goal` | **已完成**（6 个任务） |
| 3b | 尚未成文 | run controller 自动续跑、`ask_user` / `awaiting_user`、新流事件、`code` preset 装配、三协议 e2e、真实模型临时仓库验收 | 骨架见本文；未开始 |

**Phase 1 = 1 + 2a + 2b + 3a + 3b。** 已完成 3 个，未开始 2 个。

**为什么把原计划 2 拆成 2a / 2b**：两者只共享 `ToolError` 与 mutation
generation（都由 2a 引入），其余部分是独立子系统——文件路径安全与补丁原子性是
一类问题，进程组、deadline 与 job 生命周期是另一类。拆开后各自结束时都能独立
验收：2a 结束时 agent 已能在边界内读、搜、改；2b 结束时才能跑命令。

### 计划 2a 开工前已验证的技术事实

1. **阻塞式同步工具不会卡住事件循环**：LangChain 的 `BaseTool._arun` 通过
   `run_in_executor` 把同步工具丢到线程池执行。实测在 `astream` 中调用一个
   `time.sleep(2)` 的同步工具，事件循环心跳仍以 50ms 间隔跳了 40 次。因此
   `exec_command` 可以用仓库现有的同步 `StructuredTool` 风格实现，不必改成
   异步子进程。上限是默认线程池大小（`min(32, cpu+4)`），并发长命令会排队。
2. **本机没有可用的 `rg` 二进制**：`shutil.which("rg")` 返回 `None`（shell 里的
   `rg` 是 Claude Code 的包装函数，不是 PATH 上的可执行文件）。所以
   `search_text` 的纯 Python 实现是主路径，ripgrep 只是存在时的加速分支，
   两条路径都必须测到。

计划 2b 与 3b 的计划文件在各自开工时创建（命名沿用
`YYYY-MM-DD-<feature>-plan.md`），本文只固定范围、依赖与验收，不预先占用文件名。

## 测试策略（各计划共同）

设计第 12 节要求真实模型 smoke test 只作为补充。因此每个计划都遵循同一分层：

1. **纯逻辑单测**：不启动 Pelix，直接对 resolver / assembler / 状态机断言；
2. **真实 Pelix e2e**：用 `PluginManager` + fake model 验证 scope 不串、事件顺序、
   graph 重建行为；
3. **真实模型 smoke**：只在 3b 收尾建一个 fixture 临时仓库 + `real_llm` marker
   的验收任务（`pyproject.toml` 已注册 `real_llm` marker，默认 skip）。

fake-model 确定性测试是门禁，真实模型验收是补充，两者不可互相替代。

---

## 计划 2b 骨架：命令执行与 job 控制

**目标**：让 code agent 自己运行命令、测试与 lint，并有 deadline、输出上限、
环境白名单与后台 job 归属。2a 已经把"读、搜、改"做完了，本计划只补"跑"。

**依赖**：计划 2a 的 `WorkspaceProvider`、`WorkspaceStateProvider`（mutation
generation）、`tool_error()`、`resolve_workspace_path()`；计划 3a 的
`ProjectVerifier`（它的 argv 执行与 digest 逻辑应抽成共享实现，两处复用）。

**新增契约**（`src/langharness_core/contracts.py`）：

- `SPEC_JOB_MANAGER` / `JobManagerProvider`：job 的创建、写入、等待、终止、列举。
- `JobRecord` 值类型：`id`、`command`、`cwd`、`pid`、`state`、`started_at`、
  `exit_code`、`owner`（`agent_id + session_id`）。

**新增文件**：

- `src/langharness_core/plugins/exec/command.py`：`exec_command`。
- `src/langharness_core/plugins/exec/jobs.py`：`write_stdin`、`wait_job`、`stop_job`。
- `src/langharness_core/plugins/exec/runner.py`：被 verifier 与本计划共用的
  argv 执行器（进程组、deadline、输出上限、环境白名单）。

**任务骨架**：

1. `JobManagerProvider` 契约与 `JobRecord`，含序列化测试。
2. 共享 argv 执行器：独立进程组、deadline、stdout/stderr 分流与总输出限额、
   环境白名单（不继承 `*_API_KEY` 等秘密）；把 `ProjectVerifier` 改成调用它。
3. `exec_command`：cwd 必须落在 workspace realpath 之下、默认 deadline 120s /
   上限 600s、前台/后台明确、返回 exit code 与耗时。
4. job 归属与生命周期：绑定 `agent_id + session_id`，取消/超时终止**整个进程组**。
5. `write_stdin` / `wait_job` / `stop_job`，`wait_job` 的等待不得超过交互更新周期。
6. mutation generation：设计第 6.4 节要求**保守地把所有 shell 命令视作可能修改
   workspace**，因此每条命令结束后都递增（只读与否不由模型声明决定）。
7. 新流事件 `job_started` / `job_output` / `job_finished` 贯通到 loop（API 已是
   原样透传，CLI renderer 已声明忽略未知事件，因此只需 loop 侧发出）。
8. catalog 装配（`plugins/exec/...` 两个条目）与两个 agent 的 job 不串 scope 的 e2e。

**验收**：fake-model 下超时后进程组无残留；输出超限被结构化标记且不撑爆上下文；
环境变量里没有宿主秘密；`wait_job` 不会阻塞事件循环；两个 agent 的 job 互不可见；
`exec_command` 写文件后 mutation generation 递增，因而此前的验证报告失效。

---

## 计划 3b 骨架：run controller、流事件与 code preset

**目标**：把"模型停止调用工具"与"用户目标完成"接起来——turn 静止但 goal 仍
`active` 时自动续跑，直到 complete / blocked / cancelled 或达到预算。

**依赖**：计划 3a 的 `GoalProvider`、`VerificationProvider`、`evaluate_completion`；
计划 2a 的 `WorkspaceStateProvider`。

**注意：3a 已经交付的部分不要重做** —— goal 值类型与 SQLite CAS 存储、6 条完成
门禁、`ProjectVerifier`、`get_goal` / `finish_goal` 都已完成。

**新增契约**：

- `SPEC_RUN_CONTROLLER` / `RunControllerProvider`：graph 静止后的决策。
- 决策值类型：`Continue(message)` / `AwaitUser(reason)` / `Stop(status, wrapup_context)`。

**新增文件**：

- `src/langharness_core/plugins/controller/goal_round.py`：`GoalRoundController`。
- `src/langharness_core/prompts/code_agent/goal_round.md`、`goal_wrapup.md`
  （order 9000，设计第 5.1 节已为它们留好位置）。
- `src/langharness_core/plugins/goals/verify_tool.py`：**独立的 `verify` 工具**。

**一个被漏掉的缺口**：设计第 6.1 节的工具表里，`verify` 与 `finish_goal` 是
**两个**工具——`verify` 让 agent 在提交完成前先跑一遍门禁、拿到报告 id 与 generation
（"宿主执行完成门禁"）。3a 只实现了 `finish_goal` 内部顺带验证，agent 没有"先查
再交"的手段，只能盲目提交再读拒绝原因。本计划需要补上它。

**loop 扩展**（`astream` 内部）：

- graph 一次运行静止后调用绑定到的 `RunControllerProvider`；
- `active` 时用同一 `thread_id` 追加带 `goal id/revision/round` 的合成 follow-up 再跑
  一轮（**依赖 checkpointer**：没有 checkpointer 时续跑会从头开始，必须显式拒绝并说明）；
- `awaiting_user` 时停止自动运行，等真实用户输入；
- `complete` / `blocked` / `cancelled` 时只允许一个收尾 model step；
- 达到 `max_rounds` 由宿主直接转 `blocked(reason="round-limit")`；
- 新流事件：`goal_started`、`goal_round_started`、`goal_status`、
  `verification_started`、`verification_result`。

**任务骨架**：

1. `RunControllerProvider` 契约与三个决策值类型。
2. `ask_user` 工具与 `awaiting_user` 转移（设计第 6.1 节：只用于无法从仓库发现的
   用户选择）。
3. 独立的 `verify` 工具，复用 3a 的 verifier 与门禁。
4. `GoalRoundController` 状态机（active / awaiting_user / verifying / complete /
   blocked / cancelled），含 round limit 与预算。
5. loop 集成：turn 静止但 goal active 时自动续跑；complete 后只产生**一次**收尾；
   无 checkpointer 时拒绝续跑。
6. `goal_round.md` / `goal_wrapup.md` 两个 prompt section。
7. 新流事件贯通到 CLI renderer（API 侧已是透传）。
8. `code` preset：`AGENT_PLUGIN_CATALOG` 增加 preset 声明，agent 配置支持
   `"preset": "code"`，`simple_agent` 行为不变。
9. 真实 Pelix scope e2e：两个 agent 的 prompt、工具、workspace、goal **合起来**不串
   （各自的专项 e2e 已分别在 1 / 2a / 3a 完成，这里只测组合）。
10. 三协议 e2e：chat / anthropic / responses 的多工具 turn、error 事件、
    checkpoint 恢复。
11. **fixture 临时仓库验收**：生成一个带失败测试的临时仓库，用真实模型跑"修复 bug"
    任务，断言 agent 读到 `AGENTS.md`、改了正确文件、跑了配置门禁、且只在新鲜
    report 通过后才完成。用 `real_llm` marker 标记，默认 skip。

**验收**：设计第 11 节 Phase 1 场景 —— 在隔离临时仓库中修复一个带失败测试的 bug；
验证失败时必须继续而不是输出"完成"。

---

## Phase 1 之后

设计第 11 节的 Phase 2 / Phase 3 均未开始，也不在本轮拆分内：

| 阶段 | 内容 | 关键约束 |
|---|---|---|
| Phase 2 | PTC presentation：`native \| ptc \| both` scoped presenter、从工具 schema 生成 typed SDK、`run_code` 隔离进程、直调阻断、子调用调度与审计、取消与资源限额 | 模式判据必须同时作用于 wire schema 与 dispatch authorization，不能只靠 prompt；`run_code` 不出现在程序内 SDK |
| Phase 3 | OS sandbox provider、可选 reviewer agent、web/skills/delivery 工具、失败恢复、上下文压缩、跨进程 goal resume | 未配置真 sandbox 时 UI 与 tool result 必须显示 `isolation="process-only"`，不得声称"沙箱执行" |

设计第 6.2 节列出的可选工具（`web_search` / `web_fetch` / `skill_list` /
`skill_read` / `deliver_file`）归 Phase 3。设计第 6.3 节明确**不提供**的工具
（无确认的 `file_delete`、`git commit` / `git push` 等外部副作用、读取全部环境
变量的工具、任意绝对路径文件 API）不在任何后续阶段的计划里，除非设计改版。
