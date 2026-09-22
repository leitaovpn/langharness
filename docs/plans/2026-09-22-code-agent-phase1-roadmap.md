# Code Agent Phase 1 开发路线图

> 本文是 `docs/designs/2026-09-22-code-agent-design.md` 第 11 节 Phase 1 的落地方案拆分。
> Phase 1 覆盖 4 个独立子系统，单个计划无法在合理篇幅内保持可执行性，因此按
> "每个计划结束时都有可独立验收的软件" 拆成 3 个顺序子计划。

## 为什么拆成 3 个

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

反向依赖不存在，所以三个计划可以严格顺序执行，每个都以一次 `make check` 通过
和一组新测试收尾。

## 计划清单

| 序号 | 计划 | 交付物 | 状态 |
|---:|---|---|---|
| 1 | `docs/plans/2026-09-22-code-agent-prompt-sections-plan.md` | agent loop 在每个 model step 用有序、具名、带预算的 sections 组装 system prompt；`AGENTS.md` 按 workspace→目标路径逐层注入并按 hash 失效 | 已可执行（8 个任务，含逐步代码与测试） |
| 2a | `docs/plans/2026-09-22-code-agent-workspace-tools-plan.md` | 受 workspace realpath 约束的 `list_directory` / `read_file` / `search_text` / `apply_patch` / `workspace_status`，以及 `ToolError` 与 mutation generation | 已可执行（8 个任务） |
| 2b | 尚未成文 | 有 deadline、输出上限、环境白名单、后台 job 归属的命令执行：`exec_command` / `write_stdin` / `wait_job` / `stop_job` | 骨架见本文；开始前按同一流程写成独立计划文件 |
| 3 | 尚未成文 | 持久化 goal 状态机、验证门禁、自动续跑控制器、`code` preset 与新的流事件 | 骨架见本文；开始前按同一流程写成独立计划文件 |

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

计划 2b 与计划 3 的计划文件在各自开工时创建（命名沿用
`YYYY-MM-DD-<feature>-plan.md`），本文只固定范围、依赖与验收，不预先占用文件名。

Phase 2（PTC presentation）与 Phase 3（OS sandbox、reviewer、web/skills/delivery）
不在本轮范围内，待 Phase 1 三个计划全部落地后按同一流程单独设计。

## 测试策略（三个计划共同）

设计第 12 节要求真实模型 smoke test 只作为补充。因此每个计划都遵循同一分层：

1. **纯逻辑单测**：不启动 Pelix，直接对 resolver / assembler / 状态机断言；
2. **真实 Pelix e2e**：用 `PluginManager` + fake model 验证 scope 不串、事件顺序、
   graph 重建行为；
3. **真实模型 smoke**：只在计划 3 收尾建一个 fixture 临时仓库 + `real_llm` marker
   的验收任务（`.pytest.ini` 已注册 `real_llm` marker，默认 skip）。

fake-model 确定性测试是门禁，真实模型验收是补充，两者不可互相替代。

---

## 计划 2 骨架：code workspace 与 exec/jobs 工具

**目标**：让 code agent 能在 workspace 边界内读取、搜索、打补丁，并运行命令、
测试、lint，且执行证据结构化可查。

**依赖**：计划 1 的 `WorkspaceProvider`（`agent.plugin.workspace`）。

**新增契约**（`src/langharness_core/contracts.py`）：

- `SPEC_JOB_MANAGER` / `JobManagerProvider`：后台 job 的创建、写入、等待、终止。
- `ToolError` / `ToolResult` 值类型：统一 `{code, message, retryable, details}`，
  异常不得以 Python traceback 形式回流给模型。

**新增文件**：

- `src/langharness_core/plugins/workspace/paths.py`：realpath 固定、`root_dir`
  约束、symlink escape 拒绝（计划 1 已在 instructions resolver 里实现同一判据，
  本计划抽取为共享实现并让两处复用）。
- `src/langharness_core/plugins/tools/code_workspace.py`：`list_directory`、
  `read_file`、`search_text`、`workspace_status`、`apply_patch`。
- `src/langharness_core/plugins/tools/patch.py`：补丁解析与原子应用。
- `src/langharness_core/plugins/exec/command.py`：`exec_command`。
- `src/langharness_core/plugins/exec/jobs.py`：`write_stdin`、`wait_job`、`stop_job`。
- `src/langharness_core/plugins/workspace/generation.py`：`mutation_generation` 计数器。

**补丁格式**（计划 1 的 `touched_paths_from_messages` 已按此格式解析，计划 2 必须
使用同一组标记）：

```text
*** Begin Patch
*** Update File: src/a.py
@@ <context>
-old
+new
*** Add File: src/b.py
+content
*** Delete File: src/c.py
*** End Patch
```

**任务骨架**：

1. `ToolError` / `ToolResult` 值类型 + 契约，含序列化测试。
2. workspace 路径约束共享实现（realpath、穿越、symlink），含两个 agent 不同
   workspace 的 scope e2e。
3. `list_directory` / `read_file`：结构化输出、行范围、内容 hash、截断标记。
4. `search_text`：ripgrep 优先、无 ripgrep 时纯 Python 回退、结果数量上限。
5. `apply_patch`：解析 V4A 风格补丁、原子应用（失败不部分写）、before/after hash、
   `wrong_generation` 冲突拒绝。
6. `workspace_status`：git 状态、区分 agent 改动与用户既有改动。
7. `mutation_generation`：写工具与 shell 命令结束后递增；只读工具不递增。
8. `exec_command`：cwd 必须在 workspace realpath 之下、独立进程组、deadline
   120s/上限 600s、stdout/stderr 分流与总输出限额、环境白名单。
9. job 控制：`write_stdin` / `wait_job` / `stop_job`，ownership 绑定
   `agent_id + session_id`，取消时终止整个进程组。

**验收**：fake-model 下路径穿越与 symlink escape 均被拒；补丁失败后文件字节不变；
超时后进程组无残留；输出超限被结构化标记；两个 agent 的 workspace 不互相可见。
真实模型 smoke 并入计划 3 的 fixture 仓库验收。

---

## 计划 3 骨架：goal、验证门禁与 run controller

**目标**：把"模型停止调用工具"与"用户目标完成"彻底分开：只有 `finish_goal`
提交证据并通过宿主确定性检查，goal 才进入 `complete`。

**依赖**：计划 1 的 prompt section 体系与 `WorkspaceProvider`；计划 2 的
`mutation_generation` 与 exec 工具。

**新增契约**：

- `SPEC_GOAL` / `GoalProvider`：goal 的创建、读取、CAS 更新。
- `SPEC_RUN_CONTROLLER` / `RunControllerProvider`：graph 静止后的决策。
- `SPEC_VERIFIER` / `VerificationProvider`：执行验证策略并签发
  `VerificationReport`。

**新增文件**：

- `src/langharness_core/plugins/goals/model.py`：`Goal`、`VerificationReport` 值类型。
- `src/langharness_core/plugins/goals/sqlite.py`：goal 持久化，revision CAS。
- `src/langharness_core/plugins/goals/tools.py`：`get_goal`、`finish_goal`、`ask_user`。
- `src/langharness_core/plugins/verifier/project.py`：按 argv 数组执行门禁命令、
  计算 artifact hash、校验 report 的 `mutation_generation` 新鲜度。
- `src/langharness_core/plugins/controller/goal_round.py`：单步 RunController。
- `src/langharness_core/prompts/code_agent/goal_round.md`、`goal_wrapup.md`。

**loop 扩展**（`_rebuild` 之外，`astream` 内部）：

- graph 一次运行静止后调用绑定到的 `RunControllerProvider`；
- 返回 `Continue(message)` / `AwaitUser(reason)` / `Stop(status, wrapup_context)`；
- `active` 时写 checkpoint、追加带 `goal id/revision/round` 的合成 follow-up 再跑一轮；
- 新流事件：`goal_started`、`goal_round_started`、`goal_status`、
  `verification_started`、`verification_result`、`job_started`、`job_output`、
  `job_finished`；现有事件保持兼容。

**完成门禁**（设计第 8.3 节 6 条全部实现为宿主侧确定性检查，任一失败即拒绝完成
并把拒绝原因作为 tool result 返回）：

1. goal 存在且 revision 匹配；
2. artifact 可定位且记录 hash；
3. 验证 report 的 `mutation_generation` 等于 workspace 当前代次；
4. 必选检查退出码为 0（本仓库为 `make check`）且 `git diff --check` 通过；
5. 每条 acceptance criterion 都有证据映射（tool call / 文件 hash / 测试用例 /
   显式声明 `manual/user_review`）；
6. 无未决审批、无 agent 自己的活动 job。

**任务骨架**：

1. `Goal` / `VerificationReport` 值类型与 JSON 往返，含 `goal_revision` 字段。
2. SQLite goal store：revision CAS、`rounds_started`、`blocker_history`、预算字段。
3. `ProjectVerifier`：argv 执行、退出码、时长、stdout digest、artifact hash。
4. `finish_goal` 门禁：6 条检查逐条一个失败测试，另一条全通过的测试。
5. `get_goal` / `ask_user` 工具。
6. `GoalRoundController` 状态机（active / awaiting_user / verifying / complete /
   blocked / cancelled），含 round limit 与连续阻塞阈值。
7. loop 集成：turn 静止但 goal active 时自动续跑；complete 后只产生一次收尾。
8. 新流事件贯通到 API 与 CLI renderer。
9. `code` preset：`AGENT_PLUGIN_CATALOG` 增加 preset 声明，agent 配置支持
   `"preset": "code"`，`simple_agent` 行为不变。
10. 真实 Pelix scope e2e：两个 agent 的 prompt、工具、workspace、goal 不串。
11. 三协议 e2e：chat / anthropic / responses 的多工具 turn、error 事件、
    checkpoint 恢复。
12. **fixture 临时仓库验收**：生成一个带失败测试的临时仓库，用真实模型跑
    "修复 bug" 任务，断言 agent 读到 `AGENTS.md`、改了正确文件、跑了配置门禁、
    且只在新鲜 report 通过后才完成。用 `real_llm` marker 标记，默认 skip。

**验收**：设计第 11 节 Phase 1 场景 —— 在隔离临时仓库中修复一个带失败测试的 bug；
验证失败时必须继续而不是输出"完成"。
