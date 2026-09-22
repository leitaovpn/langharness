# Code Agent 计划 3b：Run Controller、流事件与 code preset 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"模型停止调用工具"与"用户目标完成"接起来——turn 静止但 goal 仍 `active` 时自动续跑，直到 complete / blocked / cancelled 或达到轮次上限；并把整套 code agent 装配成一个可用 `"preset": "code"` 选中的 agent。

**Architecture:** 新增 `agent.plugin.run_controller` 契约与 `GoalRoundController`。`PluginAgentLoop.astream` 的 graph 运行段外包一层 `while True`：每次 graph 静止后问 controller 要一个决策（`Continue` / `AwaitUser` / `Stop`），`Continue` 就用同一 `thread_id` 追加一条合成 follow-up 再跑一轮。补上设计第 6.1 节漏掉的独立 `verify` 工具，让模型能"先查再交"。最后加 `code` preset 把前面四个计划的插件装配成一个 agent。

**Tech Stack:** Python 3.13、Pelix/iPOPO、LangGraph checkpointer、pydantic `args_schema`、pytest。

## Global Constraints

- 质量门槛（AGENTS.md）：每个 commit 必须过 `make check`（Ruff → mypy → Pyright → clean-process import → pytest），覆盖率 ≥95%。**执行前基线 96.07%**。
- TDD：先写失败测试，再写最小实现；每个任务独立 commit。
- 生产代码 mypy strict、Pyright standard 不允许全局放宽。**`tests/` 不是 mypy 豁免区**：要么全量注解，要么 line 2 加 `# mypy: ignore-errors`，并按需补 `# pyright: report...=false`。
- **`self.x = param` 会被 Pyright 推断为 `str`**，赋给 Literal 别名时必须显式注解。
- 插件间通信只走 Pelix service spec（Protocol）；`@Provides`/`@Requires` 声明 Protocol 类。
- `PluginDescriptor` 只在 `langharness_core/plugin.py` 构造；组件实现在 `plugins/<topic>/<name>.py`。
- 工具异常一律走 `tool_error()`；`ToolErrorCode` 是 Literal，新增 code 必须同步加进去。
- 文档用中文，代码/命令用英文；commit 用 `feat:` / `test:` / `docs:`。
- 设计文档：`docs/designs/2026-09-22-code-agent-design.md` 第 8 节。
- 前置：计划 1、2a、2b、3a 已合入本分支。

## 开工前已验证的技术事实

**同一 `thread_id` 重复调用 `astream` 会延续对话。** 实测：带 `AsyncSqliteSaver` 的
graph 连调三次，模型每次看到的消息数是 `1, 3, 5`，线程里累计 6 条。这正是设计第 8.4 节
"写 checkpoint，追加合成 follow-up，再运行一轮"的实现基础。

**由此得到一条硬约束：没有 checkpointer 就不能自动续跑。** 没有它，第二次
`astream` 会从空白对话开始——那不是"继续",是"重来"，而且会把已有上下文丢掉。因此
loop 在决定续跑前必须检查 checkpointer 是否存在，不存在就 `Stop` 并在 wrapup 里说明。

## 一个被漏掉的缺口（本计划要补）

设计第 6.1 节的工具表里 `verify` 与 `finish_goal` 是**两个**工具：`verify` 让 agent 在
提交完成前先跑一遍门禁、拿到报告 id 与 generation。计划 3a 只实现了 `finish_goal`
内部顺带验证，于是 agent 没有"先查再交"的手段——只能盲目提交再读拒绝原因，多花一轮
模型调用，还可能因为不知道缺什么而反复失败。Task 2 补上它。

## 决策与状态映射（Task 4 的实现依据）

| goal 状态 | 轮次 | 决策 |
|---|---|---|
| 无 goal / `complete` / `blocked` / `cancelled` | — | `Stop(status)` —— 只允许一次收尾 |
| `active` | `rounds_started < max_rounds` | `Continue(follow-up)`，并把 `rounds_started` 加一 |
| `active` | 达到 `max_rounds` | `Stop("blocked", reason="round-limit")`，宿主直接改状态 |
| `awaiting_user` | — | `AwaitUser(reason)` —— 停止自动运行，等真实用户输入 |
| 无 checkpointer | 任何 | `Stop("active", reason="no-checkpointer")`，不续跑 |

## 文件布局

**新建**

| 文件 | 职责 |
|---|---|
| `src/langharness_core/plugins/controller/__init__.py` | 包标记 |
| `src/langharness_core/plugins/controller/goal_round.py` | `GoalRoundController` |
| `src/langharness_core/plugins/goals/verify_tool.py` | 独立的 `verify` 工具 |
| `src/langharness_core/plugins/goals/ask_user.py` | `ask_user` 工具 |
| `src/langharness_core/prompts/code_agent/goal_round.md` | order 9000：续跑要求 |
| `src/langharness_core/prompts/code_agent/goal_wrapup.md` | order 9001：收尾要求 |
| `tests/test_run_controller.py`、`tests/test_verify_tool.py`、`tests/test_ask_user.py`、`tests/test_goal_round_controller.py`、`tests/test_loop_continuation.py`、`tests/test_goal_stream_events.py`、`tests/test_code_preset.py`、`tests/test_code_agent_e2e.py` | 对应测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/langharness_core/contracts.py` | `SPEC_RUN_CONTROLLER`、`Continue`、`AwaitUser`、`Stop`、`RunControllerProvider` |
| `src/langharness_core/common/tool_error.py` | `ToolErrorCode` 增加 `"no_active_goal"` 已有；本计划无新增 |
| `src/langharness_core/plugins/loop/agent_loop.py` | `astream` 外层续跑循环、`_decide`、goal 事件 |
| `src/langharness_core/plugins/prompts/code_agent.py` | `SECTIONS` 增加两个 order 9000+ 的 section |
| `src/langharness_core/plugin.py` | `AGENT_PRESETS`、`agent_preset_modules()`、catalog 增加 `run-controller` / `verify-tool` / `ask-user` |
| `src/langharness_core/plugins/agents/directory.py` | `_bindings()` 支持 preset |
| `src/langharness_cli/plugins/rich_renderer.py` | 渲染 goal / verification 事件 |
| `tests/test_imports.py` | `PUBLIC_MODULES` 增加新模块 |

---

### Task 1: Run controller 契约与决策类型

**Files:**
- Modify: `src/langharness_core/contracts.py`
- Test: `tests/test_run_controller.py`（新建）

**Interfaces:**
- Produces（Task 4/5 复用）：
  - `SPEC_RUN_CONTROLLER = "agent.plugin.run_controller"`
  - `Continue(message: str, round: int)`
  - `AwaitUser(reason: str)`
  - `Stop(status: str, reason: str, wrapup: str)`
  - `RunDecision = Continue | AwaitUser | Stop`
  - `RunControllerProvider` Protocol：`decide(*, rounds_run: int, checkpointer_available: bool) -> RunDecision`、`get_plugin_info`

**为什么 `checkpointer_available` 是入参而不是 controller 自己去查：** controller 拿不到
loop 的 checkpointer 绑定，而"能不能续跑"是 loop 的事实。让 loop 把事实传进来，controller
只做决策——这样它保持纯逻辑、可单测。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_run_controller.py`：

```python
"""Contract tests for the run controller service."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_RUN_CONTROLLER,
    AwaitUser,
    Continue,
    RunControllerProvider,
    Stop,
)
from langharness_plugin.validation import contract_for


def test_specification_is_pinned() -> None:
    assert contract_for(SPEC_RUN_CONTROLLER) is RunControllerProvider
    assert SPEC_RUN_CONTROLLER in ALL_PLUGIN_SPECS


def test_continue_carries_the_followup_and_round() -> None:
    decision = Continue(message="keep going", round=2)
    assert decision.message == "keep going"
    assert decision.round == 2


def test_await_user_carries_the_reason() -> None:
    assert AwaitUser(reason="needs a choice").reason == "needs a choice"


def test_stop_carries_status_reason_and_wrapup() -> None:
    decision = Stop(status="blocked", reason="round-limit", wrapup="ran out of rounds")
    assert decision.status == "blocked"
    assert decision.reason == "round-limit"
    assert decision.wrapup == "ran out of rounds"


def test_decisions_are_distinct_types_not_dicts() -> None:
    # A dict-shaped decision would let a typo silently mean "stop".
    decision: RunDecision = Continue(message="x", round=1)
    assert isinstance(decision, Continue)
    assert not isinstance(decision, (AwaitUser, Stop))
    assert not isinstance(decision, dict)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_run_controller.py -q`
Expected: FAIL — `ImportError: cannot import name 'SPEC_RUN_CONTROLLER'`

- [ ] **Step 3: 写实现**

在 `src/langharness_core/contracts.py` 中，`SPEC_JOB_MANAGER` 之后加：

```python
SPEC_RUN_CONTROLLER = "agent.plugin.run_controller"
```

把它追加进 `ALL_PLUGIN_SPECS`（紧跟 `SPEC_JOB_MANAGER`）。

`JobRecord` 之后加决策类型：

```python
@dataclass(frozen=True, slots=True)
class Continue:
    """Run another round with this synthetic follow-up as the next input."""

    message: str
    round: int


@dataclass(frozen=True, slots=True)
class AwaitUser:
    """Stop the automatic run; a real user message is needed to go on."""

    reason: str


@dataclass(frozen=True, slots=True)
class Stop:
    """End the run. ``wrapup`` is what the final model step is asked to cover."""

    status: str
    reason: str
    wrapup: str


RunDecision = Continue | AwaitUser | Stop
```

`JobManagerProvider` 之后加 Protocol：

```python
@service_contract(SPEC_RUN_CONTROLLER)
@runtime_checkable
class RunControllerProvider(Protocol):
    """Contract implemented by every ``agent.plugin.run_controller`` service."""

    def decide(
        self, *, rounds_run: int, checkpointer_available: bool
    ) -> RunDecision: ...

    def get_plugin_info(self) -> dict[str, str]: ...
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_run_controller.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/contracts.py tests/test_run_controller.py
git commit -m "feat: add the run controller contract and its decisions"
```

---

### Task 2: 独立的 `verify` 工具

**Files:**
- Create: `src/langharness_core/plugins/goals/verify_tool.py`
- Test: `tests/test_verify_tool.py`（新建）

**Interfaces:**
- Consumes: 计划 3a 的 `VerificationProvider`、`GoalProvider`、`WorkspaceStateProvider`、`evaluate_completion`、`tool_error`。
- Produces（Task 5/8 复用）：工厂 `verify-tool-plugin-factory`，模块 `langharness_core.plugins.goals.verify_tool`，工具名 `verify`。

**返回形状**：`{"report_id", "goal_revision", "mutation_generation", "passed", "checks": [...], "would_complete": bool, "blockers": [str]}`

`would_complete` 是**用同一套门禁算出来的**，`blockers` 就是拒绝原因的消息文本。这样
模型跑一次 `verify` 就知道"还差什么"，不必先提交再读拒绝。它与 `finish_goal` 共用
`evaluate_completion`，不存在两套判据。

**为什么 `verify` 不推进 goal 状态**：它只是查询。设计第 6.1 节把 `verify` 归为
"宿主执行完成门禁"，终结入口只有 `finish_goal`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_verify_tool.py`：

```python
"""Tests for the standalone verify tool."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_core.plugins.goals.verify_tool import VerifyToolPlugin
from langharness_core.plugins.verifier.project import ProjectVerifier
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path, *, failing: bool = False) -> VerifyToolPlugin:
    instance = VerifyToolPlugin()
    goals = SQLiteGoalPlugin()
    goals._path = str(tmp_path / "goals.sqlite3")
    state = WorkspaceStatePlugin()
    root = WorkspaceRootPlugin()
    root._root = str(tmp_path)
    verifier = ProjectVerifier()
    verifier._workspace = root
    verifier._state = state
    code = 1 if failing else 0
    verifier._commands = [["python3", "-c", f"import sys; sys.exit({code})"]]
    instance._goals = goals
    instance._verifier = verifier
    instance._state = state
    return instance


def verify(instance: VerifyToolPlugin) -> dict:
    return {t.name: t for t in instance.get_tools()}["verify"].invoke({})


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_verify_without_a_goal_is_reported(tmp_path: Path) -> None:
    assert verify(build(tmp_path))["error"]["code"] == "no_active_goal"


def test_verify_returns_a_signed_report_without_changing_the_goal(
    tmp_path: Path,
) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = verify(instance)
    assert result["report_id"].startswith("vr_")
    assert result["goal_revision"] == goal.revision
    assert result["mutation_generation"] == 1
    assert result["passed"] is True
    # Querying must not move the goal.
    assert instance._goals.current_goal().status == "active"
    assert instance._goals.current_goal().revision == goal.revision


def test_verify_says_whether_completion_would_be_accepted(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    # The criterion has no evidence yet, so completion would be refused.
    result = verify(instance)
    assert result["would_complete"] is False
    assert any("c1" in item for item in result["blockers"])


def test_verify_lists_a_failing_check(tmp_path: Path) -> None:
    instance = build(tmp_path, failing=True)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = verify(instance)
    assert result["passed"] is False
    assert result["would_complete"] is False
    assert result["checks"][0]["exit_code"] == 1


def test_verify_reports_the_checks_it_ran(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    result = verify(instance)
    assert result["checks"][0]["name"].startswith("python3")
    assert result["checks"][0]["exit_code"] == 0


def test_a_stale_report_is_surfaced_before_finishing(tmp_path: Path) -> None:
    # The point of verify: learn this BEFORE calling finish_goal.
    instance = build(tmp_path)
    instance._goals.create_goal(
        "fix it", [{"id": "c1", "text": "t", "evidence": "tool:x"}]
    )
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    assert verify(instance)["would_complete"] is True
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:y")
    result = verify(instance)
    assert result["would_complete"] is False
    assert any("generation" in item for item in result["blockers"])


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert build(tmp_path).get_plugin_info() == {
        "name": "verify-tool",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_verify_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.goals.verify_tool'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/goals/verify_tool.py`：

```python
"""The `verify` tool: run the gates and see what completion would say.

Distinct from `finish_goal` on purpose. The design lists both, and the split is
what lets a model check its work *before* submitting rather than learning what
is missing from a rejection.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    GoalProvider,
    ToolProvider,
    VerificationProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.goals.gate import evaluate_completion
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


@ComponentFactory("verify-tool-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "verify-tool")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@RequiresBest("_verifier", VerificationProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals", "_verifier", "_state")
class VerifyToolPlugin:
    """Runs the project's gates and reports whether completion would be accepted."""

    def __init__(self) -> None:
        self._plugin_name = "verify-tool"
        self._plugin_version = "1.0.0"
        self._goals: Any = None
        self._verifier: Any = None
        self._state: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_goals": ContractGuard(self, "_goals", GoalProvider),
            "_verifier": ContractGuard(self, "_verifier", VerificationProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

    @BindField("_goals", if_valid=True)
    def _on_goals_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_goals")
    def _on_goals_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_verifier", if_valid=True)
    def _on_verifier_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_verifier")
    def _on_verifier_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_state", if_valid=True)
    def _on_state_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_state")
    def _on_state_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._goals is None or self._verifier is None:
            return []
        return [self._verify_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        return {"verify": "verify()"}

    def _verify_tool(self) -> StructuredTool:
        plugin = self

        def verify() -> dict[str, Any]:
            goal = plugin._goals.current_goal()
            if goal is None:
                return tool_error("no_active_goal", "there is no goal to verify")
            report = plugin._verifier.verify(goal.revision)
            current = plugin._state.get_generation() if plugin._state else 0
            # Judged with the same gate finish_goal uses, so the two can never
            # disagree about what "ready" means.
            rejections = evaluate_completion(
                goal,
                expected_revision=goal.revision,
                report=report,
                current_generation=current,
                artifacts=report.artifacts,
                blockers=(),
            )
            return {
                "report_id": report.id,
                "goal_revision": goal.revision,
                "mutation_generation": current,
                "passed": report.passed,
                "checks": [
                    {
                        "name": check.name,
                        "exit_code": check.exit_code,
                        "duration_ms": check.duration_ms,
                    }
                    for check in report.checks
                ],
                "would_complete": not rejections,
                "blockers": [item.message for item in rejections],
            }

        return StructuredTool.from_function(
            func=verify,
            name="verify",
            description=(
                "Run the project's gate commands and report whether "
                "finish_goal(status='complete') would be accepted right now. "
                "Use it BEFORE submitting: 'blockers' names exactly what is "
                "still missing, so you can fix it in this turn instead of "
                "burning a round on a rejection. This only observes — it never "
                "changes the goal. Note that any command you run afterwards "
                "counts as a workspace change and makes this report stale, so "
                "verify last. Returns {\"report_id\": str, \"goal_revision\": "
                "int, \"mutation_generation\": int, \"passed\": bool, "
                "\"checks\": [{\"name\": str, \"exit_code\": int, "
                "\"duration_ms\": int}], \"would_complete\": bool, "
                "\"blockers\": [str]}, or {\"error\": {code, message, "
                "retryable, details}} when there is no active goal."
            ),
            args_schema=NoArgs,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_verify_tool.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/goals/verify_tool.py tests/test_verify_tool.py
git commit -m "feat: add the standalone verify tool"
```

---

### Task 3: `ask_user` 工具与 awaiting_user

**Files:**
- Create: `src/langharness_core/plugins/goals/ask_user.py`
- Test: `tests/test_ask_user.py`（新建）

**Interfaces:**
- Consumes: 计划 3a 的 `GoalProvider`；`tool_error`。
- Produces（Task 4 依赖它产生的状态）：工厂 `ask-user-plugin-factory`，工具名 `ask_user`。

**行为**：把 goal 转成 `awaiting_user`，并返回一条说明。设计第 6.1 节把它限制为
"只用于无法从仓库发现的用户选择"——这一点写在工具描述里，由模型遵守，宿主不强制
（宿主无法判断一个问题是否真的只能由用户回答）。

**为什么返回体要写清楚"接下来会怎样"**：模型拿到 tool result 后还会生成一段文字；
如果它不知道运行会在此暂停，它可能继续尝试。工具描述与本任务一起把这点讲明白。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_ask_user.py`：

```python
"""Tests for the ask_user tool."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.goals.ask_user import AskUserToolPlugin
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path) -> AskUserToolPlugin:
    instance = AskUserToolPlugin()
    goals = SQLiteGoalPlugin()
    goals._path = str(tmp_path / "goals.sqlite3")
    instance._goals = goals
    return instance


def ask(instance: AskUserToolPlugin, question: str) -> dict:
    return {t.name: t for t in instance.get_tools()}["ask_user"].invoke(
        {"question": question}
    )


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_asking_without_a_goal_is_reported(tmp_path: Path) -> None:
    assert ask(build(tmp_path), "which one?")["error"]["code"] == "no_active_goal"


def test_asking_moves_the_goal_to_awaiting_user(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    result = ask(instance, "Should the API change or the caller?")
    assert result["status"] == "awaiting_user"
    assert result["question"] == "Should the API change or the caller?"
    stored = instance._goals.get_goal(goal.id)
    assert stored.status == "awaiting_user"
    assert stored.revision == goal.revision + 1


def test_the_result_says_the_run_will_pause(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    result = ask(instance, "which?")
    assert "pause" in result["note"].lower()


def test_a_blank_question_is_refused(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    result = ask(instance, "   ")
    assert result["error"]["code"] == "invalid_arguments"
    assert instance._goals.current_goal().status == "active"


def test_asking_twice_is_still_awaiting_user(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    ask(instance, "first?")
    second = ask(instance, "second?")
    assert second["status"] == "awaiting_user"
    assert instance._goals.current_goal().status == "awaiting_user"


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert build(tmp_path).get_plugin_info() == {
        "name": "ask-user",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_ask_user.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.goals.ask_user'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/goals/ask_user.py`：

```python
"""The `ask_user` tool: hand a decision back to the person.

The host cannot tell whether a question truly needed a human, so this is a
policy the model follows rather than one the host enforces. What the host does
enforce is the consequence: the goal leaves `active`, so the run controller
stops auto-continuing until a real user message arrives.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import GoalProvider, ToolProvider
from langharness_core.plugins.goals.sqlite import GoalConflictError
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class AskUserArgs(BaseModel):
    question: str = Field(
        min_length=1,
        description="The single decision you need from the user, stated plainly.",
    )


@ComponentFactory("ask-user-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "ask-user")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals")
class AskUserToolPlugin:
    """Pauses the goal so a human can answer a question the repo cannot."""

    def __init__(self) -> None:
        self._plugin_name = "ask-user"
        self._plugin_version = "1.0.0"
        self._goals: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_goals": ContractGuard(self, "_goals", GoalProvider),
        }

    @BindField("_goals", if_valid=True)
    def _on_goals_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_goals")
    def _on_goals_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tools(self) -> list[Any]:
        if self._goals is None:
            return []
        return [self._ask_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        return {"ask_user": "ask_user({question})"}

    def _ask_tool(self) -> StructuredTool:
        plugin = self

        def ask_user(question: str) -> dict[str, Any]:
            text = question.strip()
            if not text:
                return tool_error("invalid_arguments", "the question is empty")
            goal = plugin._goals.current_goal()
            if goal is None:
                return tool_error("no_active_goal", "there is no goal to pause")
            try:
                updated = plugin._goals.update_goal(
                    goal.id,
                    expected_revision=goal.revision,
                    status="awaiting_user",
                )
            except GoalConflictError as exc:
                return tool_error("goal_revision_mismatch", str(exc), retryable=True)
            return {
                "status": updated.status,
                "goal_id": updated.id,
                "question": text,
                "note": (
                    "The run is now paused and will not continue on its own. "
                    "The user answers in their next message; treat that answer "
                    "as the decision and resume from there. Do not guess an "
                    "answer and do not keep working in the meantime."
                ),
            }

        return StructuredTool.from_function(
            func=ask_user,
            name="ask_user",
            description=(
                "Ask the user to decide something you cannot determine from the "
                "repository. Use it ONLY for choices that genuinely change the "
                "requirements — which of two behaviours is wanted, whether a "
                "breaking change is acceptable, what to do when the spec is "
                "ambiguous. Do not use it for anything you could find by "
                "reading the code or running a command, and do not use it to "
                "report progress. Calling it pauses the run: it will not "
                "continue until the user replies. Returns {\"status\": "
                "'awaiting_user', \"goal_id\": str, \"question\": str, "
                "\"note\": str}, or {\"error\": {code, message, retryable, "
                "details}}."
            ),
            args_schema=AskUserArgs,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_ask_user.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/goals/ask_user.py tests/test_ask_user.py
git commit -m "feat: let the agent hand a decision back with ask_user"
```

---

### Task 4: `GoalRoundController`

**Files:**
- Create: `src/langharness_core/plugins/controller/__init__.py`
- Create: `src/langharness_core/plugins/controller/goal_round.py`
- Test: `tests/test_goal_round_controller.py`（新建）

**Interfaces:**
- Consumes: Task 1 的决策类型与契约；计划 3a 的 `GoalProvider`。
- Produces（Task 5 复用）：工厂 `goal-round-controller-plugin-factory`，模块 `langharness_core.plugins.controller.goal_round`，属性 `plugin.goal.max_rounds`（默认 32）。

**决策表就是文件头那张表**，逐行对应一个测试。`Continue` 的 follow-up 文本带上
goal id、revision 与轮次，让模型知道自己处在第几轮、目标有没有变过。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_round_controller.py`：

```python
"""Tests for the goal round controller."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import (
    AwaitUser,
    Continue,
    RunControllerProvider,
    Stop,
)
from langharness_core.plugins.controller.goal_round import GoalRoundController
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path, *, max_rounds: int = 3) -> GoalRoundController:
    instance = GoalRoundController()
    goals = SQLiteGoalPlugin()
    goals._path = str(tmp_path / "goals.sqlite3")
    goals._max_rounds = max_rounds
    instance._goals = goals
    return instance


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), RunControllerProvider) == ()


def test_no_goal_stops_immediately(tmp_path: Path) -> None:
    decision = build(tmp_path).decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Stop)
    assert decision.reason == "no-goal"


def test_an_active_goal_continues(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Continue)
    assert decision.round == 1
    assert goal.id in decision.message
    assert "revision 1" in decision.message
    # Continuing consumes a round.
    assert instance._goals.get_goal(goal.id).rounds_started == 1


def test_the_round_limit_blocks_the_goal(tmp_path: Path) -> None:
    instance = build(tmp_path, max_rounds=2)
    goal = instance._goals.create_goal("fix it", [])
    for _ in range(2):
        assert isinstance(
            instance.decide(rounds_run=0, checkpointer_available=True), Continue
        )
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Stop)
    assert decision.reason == "round-limit"
    assert instance._goals.get_goal(goal.id).status == "blocked"


def test_awaiting_user_stops_without_consuming_a_round(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(goal.id, expected_revision=1, status="awaiting_user")
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, AwaitUser)
    assert instance._goals.get_goal(goal.id).status == "awaiting_user"


def test_a_complete_goal_stops(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(goal.id, expected_revision=1, status="complete")
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Stop)
    assert "complete" in decision.wrapup.lower()


def test_a_blocked_goal_stops(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(goal.id, expected_revision=1, status="blocked")
    assert isinstance(
        instance.decide(rounds_run=0, checkpointer_available=True), Stop
    )


def test_without_a_checkpointer_it_refuses_to_continue(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    decision = instance.decide(rounds_run=0, checkpointer_available=False)
    assert isinstance(decision, Stop)
    assert decision.reason == "no-checkpointer"
    assert "conversation" in decision.wrapup.lower()


def test_the_wrapup_asks_for_facts_not_claims(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(
        instance._goals.current_goal().id, expected_revision=1, status="complete"
    )
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert "tool" in decision.wrapup.lower()
    assert "risk" in decision.wrapup.lower()


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert build(tmp_path).get_plugin_info() == {
        "name": "goal-round-controller",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_round_controller.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.controller'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/controller/__init__.py`：

```python
"""Run controller plugins."""
```

`src/langharness_core/plugins/controller/goal_round.py`：

```python
"""Decides whether the agent keeps working after a turn goes quiet.

A turn going quiet means the model stopped calling tools. It does not mean the
goal is done — that is `finish_goal`'s call and the host's gate. This component
sits between the two: while the goal is active it hands the loop another round,
and it is the only thing that ends an automatic run.
"""

from __future__ import annotations

from typing import Any

from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)

from langharness_core.contracts import (
    AwaitUser,
    Continue,
    GoalProvider,
    RunControllerProvider,
    RunDecision,
    Stop,
)
from langharness_core.plugins.goals.sqlite import GoalConflictError
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

WRAPUP_COMPLETE = (
    "The goal is complete. Write the final answer now, from facts you can "
    "point at: what changed, which tool results or hashes prove it, which "
    "checks you ran, and what risks remain. Do not call more tools."
)
WRAPUP_BLOCKED = (
    "The goal is blocked. Write the final answer now: what you tried, what "
    "stopped you, and exactly what would unblock it. Do not call more tools."
)
WRAPUP_NO_CHECKPOINTER = (
    "This run cannot continue automatically because there is no checkpointer, "
    "so the conversation would restart and lose its context. Stop here and "
    "tell the user what is left to do."
)


@ComponentFactory("goal-round-controller-plugin-factory")
@Provides(RunControllerProvider)
@Property("_plugin_name", "plugin.name", "goal-round-controller")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_max_rounds", "plugin.goal.max_rounds", 32)
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals")
class GoalRoundController:
    """Keeps an active goal moving until it settles or runs out of rounds."""

    def __init__(self) -> None:
        self._plugin_name = "goal-round-controller"
        self._plugin_version = "1.0.0"
        self._max_rounds = 32
        self._goals: Any = None
        self._guards: dict[str, ContractGuard] = {
            "_goals": ContractGuard(self, "_goals", GoalProvider),
        }

    @BindField("_goals", if_valid=True)
    def _on_goals_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_goals")
    def _on_goals_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def decide(
        self, *, rounds_run: int, checkpointer_available: bool
    ) -> RunDecision:
        if self._goals is None:
            return Stop(status="cancelled", reason="no-goal-store", wrapup=WRAPUP_BLOCKED)
        goal = self._goals.current_goal()
        if goal is None:
            return Stop(status="complete", reason="no-goal", wrapup=WRAPUP_COMPLETE)
        if goal.status == "awaiting_user":
            return AwaitUser(reason=f"goal {goal.id} is waiting on the user")
        if goal.status == "complete":
            return Stop(status="complete", reason="done", wrapup=WRAPUP_COMPLETE)
        if goal.status == "blocked":
            return Stop(status="blocked", reason="blocked", wrapup=WRAPUP_BLOCKED)
        if not checkpointer_available:
            return Stop(
                status="active",
                reason="no-checkpointer",
                wrapup=WRAPUP_NO_CHECKPOINTER,
            )
        if goal.rounds_started >= int(self._max_rounds):
            self._mark_blocked(goal, "round-limit")
            return Stop(
                status="blocked",
                reason="round-limit",
                wrapup=(
                    f"The goal hit its {self._max_rounds}-round limit. "
                    + WRAPUP_BLOCKED
                ),
            )
        return self._continue(goal)

    def _continue(self, goal: Any) -> RunDecision:
        try:
            updated = self._goals.update_goal(
                goal.id, expected_revision=goal.revision, start_round=True
            )
        except GoalConflictError:
            # Someone moved the goal under us; let the next decision see it.
            return Stop(status="active", reason="goal-moved", wrapup=WRAPUP_BLOCKED)
        return Continue(
            message=(
                f"[goal {updated.id} · revision {updated.revision} · "
                f"round {updated.rounds_started} of {self._max_rounds}]\n"
                f"Objective: {updated.objective}\n"
                "The turn went quiet but the goal is not complete. Continue "
                "working on it. If you believe it is done, call verify to see "
                "what is still missing, then finish_goal with evidence. If you "
                "need a decision only the user can make, call ask_user."
            ),
            round=updated.rounds_started,
        )

    def _mark_blocked(self, goal: Any, reason: str) -> None:
        try:
            self._goals.update_goal(
                goal.id,
                expected_revision=goal.revision,
                status="blocked",
                blocker=reason,
            )
        except GoalConflictError:
            return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_round_controller.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/controller tests/test_goal_round_controller.py
git commit -m "feat: keep an active goal moving between turns"
```

---

### Task 5: Loop 集成——自动续跑

**Files:**
- Modify: `src/langharness_core/plugins/loop/agent_loop.py`
- Test: `tests/test_loop_continuation.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `RunControllerProvider` 与决策类型；计划 3a 的 `CheckpointerProvider`。
- Produces：`PluginAgentLoop._decide(rounds_run) -> RunDecision | None`、`_run_goal_rounds(...)`，以及新流事件 `goal_round_started` / `goal_status`。

**循环结构**（`astream` 内）：

```text
while True:
    <graph.astream 内层循环，逐 chunk 转发事件>
    decision = self._decide(rounds)
    None          -> break            # 没有绑定 controller，行为与从前完全一致
    Continue      -> rounds += 1; yield goal_round_started; input_value = {"messages": [follow-up]}; 继续
    AwaitUser     -> yield goal_status; break
    Stop          -> yield goal_status; break
```

**没有 controller 时行为必须逐字节不变**：`_decide` 返回 `None`，外层只跑一次，
事件序列与现在完全一样。既有 loop 测试是这条的回归网。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_loop_continuation.py`：

```python
"""Tests for automatic goal continuation in the agent loop."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from types import SimpleNamespace

from langharness_core.contracts import AwaitUser, Continue, Stop
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop


class FakeController:
    def __init__(self, decisions: list[object]) -> None:
        self._decisions = list(decisions)
        self.calls: list[tuple[int, bool]] = []

    def decide(self, *, rounds_run: int, checkpointer_available: bool):
        self.calls.append((rounds_run, checkpointer_available))
        return self._decisions.pop(0) if self._decisions else Stop(
            status="complete", reason="done", wrapup="w"
        )


def test_without_a_controller_there_is_no_decision() -> None:
    assert PluginAgentLoop()._decide(0) is None


def test_the_controller_sees_the_round_count_and_checkpointer_state() -> None:
    loop = PluginAgentLoop()
    controller = FakeController([Continue(message="go", round=1)])
    loop._run_controller = controller
    loop._checkpointer_provider = SimpleNamespace()
    decision = loop._decide(3)
    assert isinstance(decision, Continue)
    assert controller.calls == [(3, True)]


def test_no_checkpointer_is_reported_as_unavailable() -> None:
    loop = PluginAgentLoop()
    controller = FakeController([Stop(status="active", reason="no-checkpointer", wrapup="w")])
    loop._run_controller = controller
    loop._decide(0)
    assert controller.calls == [(0, False)]


def test_a_failing_controller_stops_the_run() -> None:
    class Boom:
        def decide(self, **kwargs: object) -> object:
            raise RuntimeError("controller exploded")

    loop = PluginAgentLoop()
    loop._run_controller = Boom()
    decision = loop._decide(0)
    assert isinstance(decision, Stop)
    assert decision.reason == "controller-error"


def test_the_loop_declares_the_controller_field_as_scoped() -> None:
    from langharness_plugin.scoped_dependencies import scoped_fields

    assert "_run_controller" in scoped_fields(PluginAgentLoop)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_loop_continuation.py -q`
Expected: FAIL — `AttributeError: 'PluginAgentLoop' object has no attribute '_decide'`

- [ ] **Step 3: 写实现**

在 `agent_loop.py` 中：

1. 导入 `RunControllerProvider`、`Continue`、`AwaitUser`、`Stop`。
2. 依赖声明（放在 `_job_manager` 之后）：

```python
@RequiresBest(
    "_run_controller", RunControllerProvider, optional=True, immediate_rebind=True
)
```

3. `@ScopedDependencies(...)` 加入 `"_run_controller"`。
4. `__init__` 加 `self._run_controller: Any = None` 与守卫。
5. 绑定回调（与 `_job_manager` 同形）。
6. 新增：

```python
    def _decide(self, rounds_run: int) -> RunDecision | None:
        """Ask the controller what to do now that the graph has gone quiet.

        Returns ``None`` when no controller is bound — the plain-agent path,
        where one graph run is the whole turn. A controller that raises must
        not kill the stream: the run stops and the failure is logged.
        """
        if self._run_controller is None:
            return None
        try:
            return self._run_controller.decide(
                rounds_run=rounds_run,
                checkpointer_available=self._checkpointer_provider is not None,
            )
        except Exception:
            LOGGER.exception("Run controller failed; stopping the run")
            return Stop(
                status="active",
                reason="controller-error",
                wrapup="The run controller failed; stopping here.",
            )
```

7. **先把内层转发抽成私有生成器**，再加外层循环。抽出来的这一段与现有代码逐字相同，
只是把 `self._graph.astream(...)` 换成了传入的 `stream`：

```python
    async def _forward_stream(
        self,
        stream: AsyncIterator[tuple[str, Any]],
        previous_content: dict[str, str],
        usage_totals: dict[str, int],
    ) -> AsyncIterator[dict[str, Any]]:
        """Translate one graph run's chunks into stream events.

        Pulled out of ``astream`` so the continuation loop and the wrapup round
        share exactly one translator instead of two copies that can drift.
        """
        async for stream_type, chunk in stream:
            for job_event in self._drain_job_events():
                yield job_event
            if stream_type == "messages":
                streamed_message = chunk[0]
                usage = _extract_usage(streamed_message)
                if usage is not None:
                    for key in usage_totals:
                        usage_totals[key] += usage[key]
                if isinstance(streamed_message, ToolMessage):
                    continue
                if getattr(streamed_message, "tool_calls", None) or getattr(
                    streamed_message, "tool_call_chunks", None
                ):
                    continue
                content = _extract_content(streamed_message)
                message_id = str(getattr(streamed_message, "id", None) or "default")
                previous = previous_content.get(message_id, "")
                if content.startswith(previous):
                    delta = content[len(previous) :]
                else:
                    delta = content
                previous_content[message_id] = previous + delta
                if delta:
                    yield {"type": "assistant", "content": delta}
                continue
            for interrupt in _iter_interrupts(chunk):
                yield {"type": "approval_required", "request": interrupt}
            for update in chunk.values():
                if not isinstance(update, dict):
                    continue
                for interrupt in _iter_interrupts(update):
                    yield {"type": "approval_required", "request": interrupt}
                for updated_message in update.get("messages", []):
                    _strip_orphan_tool_use(updated_message)
                    if isinstance(updated_message, ToolMessage):
                        yield {
                            "type": "tool_output",
                            "name": updated_message.name or "tool",
                            "tool_call_id": updated_message.tool_call_id,
                            "output": str(updated_message.content),
                        }
                    for tool_call in getattr(updated_message, "tool_calls", []):
                        yield {
                            "type": "tool_call",
                            "name": tool_call["name"],
                            "tool_call_id": tool_call["id"],
                            "args": tool_call["args"],
                        }
```

8. 把 `astream` 的循环段换成外层续跑循环：

```python
        rounds_run = 0
        while True:
            async for event in self._forward_stream(
                self._graph.astream(
                    input_value, config=config, stream_mode=["messages", "updates"]
                ),
                previous_content,
                usage_totals,
            ):
                yield event
            decision = self._decide(rounds_run)
            if decision is None:
                break
            if isinstance(decision, Continue):
                rounds_run += 1
                yield {
                    "type": "goal_round_started",
                    "round": decision.round,
                    "message": decision.message,
                }
                input_value = {
                    "messages": [{"role": "user", "content": decision.message}]
                }
                continue
            if isinstance(decision, AwaitUser):
                yield {"type": "goal_status", "status": "awaiting_user"}
                break
            yield {"type": "goal_status", "status": decision.status}
            yield {"type": "goal_wrapup", "text": decision.wrapup}
            # One final model step, so the user gets prose rather than a stop.
            async for event in self._forward_stream(
                self._graph.astream(
                    {"messages": [{"role": "user", "content": decision.wrapup}]},
                    config=config,
                    stream_mode=["messages", "updates"],
                ),
                previous_content,
                usage_totals,
            ):
                yield event
            break
        if usage_totals["total_tokens"]:
            yield {"type": "usage", **usage_totals}
```

同时 `astream` 的签名与前置检查保持原样，`usage_totals` 与 `previous_content` 在循环
**之外**初始化——它们要跨轮累计，收尾那一轮也要接着同一份计数。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_loop_continuation.py tests/test_e2e.py tests/test_job_events.py -q`
Expected: PASS。**既有的 `test_e2e.py` 必须全部照旧通过**——没有 controller 时行为不变。

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/loop/agent_loop.py tests/test_loop_continuation.py
git commit -m "feat: continue an active goal across turns"
```

---

### Task 6: `goal_round` / `goal_wrapup` prompt sections

**Files:**
- Create: `src/langharness_core/prompts/code_agent/goal_round.md`
- Create: `src/langharness_core/prompts/code_agent/goal_wrapup.md`
- Modify: `src/langharness_core/plugins/prompts/code_agent.py`
- Test: `tests/test_code_agent_prompt.py`（追加）

**Interfaces:**
- Consumes: 计划 1 的 `CodeAgentPromptPlugin`、`SECTIONS`。
- Produces：`SECTIONS` 增加 `("goal_round", 9000)` 与 `("goal_wrapup", 9001)`。

**为什么是 9000+**：设计第 5.1 节的顺序表把 9000 留给 goal 续跑与收尾文本，它们在工具
指导（1000+）与 SDK（5000）之后，是最后读到的行为要求。

- [ ] **Step 1: 写失败测试**

在 `tests/test_code_agent_prompt.py` 末尾追加：

```python
def test_goal_sections_sit_after_tool_guidance() -> None:
    orders = dict(SECTIONS)
    assert orders["goal_round"] == 9000
    assert orders["goal_wrapup"] == 9001
    assert orders["goal_round"] > 5000


def test_the_goal_round_section_explains_what_a_quiet_turn_means() -> None:
    text = load_section("goal_round").lower()
    assert "continue" in text
    assert "verify" in text
    assert "finish_goal" in text


def test_the_wrapup_section_demands_facts() -> None:
    text = load_section("goal_wrapup").lower()
    assert "evidence" in text or "tool result" in text
    assert "risk" in text
    assert "no more tools" in text or "do not call" in text


def test_the_plugin_emits_the_goal_sections(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    names = [item.name for item in sections]
    assert names[-2:] == ["goal_round", "goal_wrapup"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_agent_prompt.py -q`
Expected: FAIL — `KeyError: 'goal_round'`

- [ ] **Step 3: 写实现**

`src/langharness_core/prompts/code_agent/goal_round.md`：

```markdown
# Continuing a goal

A turn ending does not mean the goal is finished. When you are handed another
round, the goal is still open and the host expects you to keep working on it.

- Keep going on the objective. Do not restate what you already did.
- If you believe the work is done, call `verify` first. It runs the project's
  gates and tells you exactly what completion is still missing — fixing that in
  this round is cheaper than submitting and reading a rejection.
- Only `finish_goal(status="complete", evidence=[...])` closes the goal. Attach
  real evidence to every acceptance criterion: a tool call id, a file hash, or
  a test case name. `manual/user_review` is for things a human must judge.
- Running any command or editing any file makes an earlier verification stale.
  Verify last.
- If you are stuck on a decision only the user can make, call `ask_user`. Do not
  guess a choice that changes the requirements.
```

`src/langharness_core/prompts/code_agent/goal_wrapup.md`：

```markdown
# Wrapping up

The goal has settled. Write the final answer now, and write no more tools.

- State the outcome plainly, then the evidence: what changed, which tool
  results or file hashes support it, which checks you ran.
- Name the risks and anything you could not verify. An unreported gap is worse
  than a reported one.
- If the goal was blocked, say what you tried, what stopped you, and what
  would unblock it.
- Do not claim anything you did not observe through a tool result in this
  session. Do not call more tools to look something up.
```

在 `code_agent.py` 中把 `SECTIONS` 扩成：

```python
GOAL_ROUND_ORDER = 9000
GOAL_WRAPUP_ORDER = 9001

SECTIONS: tuple[tuple[str, int], ...] = (
    ("identity", IDENTITY_ORDER),
    ("safety", SAFETY_ORDER),
    ("workflow", WORKFLOW_ORDER),
    ("completion", COMPLETION_ORDER),
    ("goal_round", GOAL_ROUND_ORDER),
    ("goal_wrapup", GOAL_WRAPUP_ORDER),
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_agent_prompt.py -q`
Expected: PASS（14 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/prompts/code_agent src/langharness_core/plugins/prompts/code_agent.py \
        tests/test_code_agent_prompt.py
git commit -m "feat: ship the goal round and wrapup prompt sections"
```

---

### Task 7: goal / verification 事件贯通到 CLI

**Files:**
- Modify: `src/langharness_cli/plugins/rich_renderer.py`
- Modify: `src/langharness_core/plugins/loop/agent_loop.py`
- Test: `tests/test_goal_stream_events.py`（新建）

**Interfaces:**
- Consumes: Task 4/5 的事件；计划 3a 的 `verify`/`finish_goal` 工具结果。
- Produces：新事件 `goal_round_started`、`goal_status`、`goal_wrapup`（Task 5 已发），
  以及本任务在 CLI 侧的渲染。

**API 侧不需要改动**：`stream.py` 把 loop 的每个事件原样 `_encode` 成 NDJSON，新事件
自动透传。

**渲染原则**：goal 事件是**状态**不是内容，用一行状态行表示，不进入回答正文——回答
正文留给模型的话。渲染器已有 `_status` 机制，扩展它即可。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_stream_events.py`：

```python
"""Tests for goal events reaching the CLI renderer."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from typing import Any

from langharness_cli.plugins.rich_renderer import RichInteractiveRenderer


def renderer() -> RichInteractiveRenderer:
    return RichInteractiveRenderer()


def test_goal_events_do_not_crash_the_renderer() -> None:
    instance = renderer()
    for event in (
        {"type": "goal_round_started", "round": 2, "message": "keep going"},
        {"type": "goal_status", "status": "complete"},
        {"type": "goal_wrapup", "text": "summarise"},
    ):
        instance.render_event(event)


def test_a_goal_round_shows_up_in_the_transcript() -> None:
    instance = renderer()
    instance.render_event({"type": "goal_round_started", "round": 2, "message": "go"})
    assert instance.status_line() == "goal · round 2"


def test_a_terminal_status_shows_up() -> None:
    instance = renderer()
    instance.render_event({"type": "goal_status", "status": "complete"})
    assert instance.status_line() == "goal · complete"


def test_the_wrapup_does_not_leak_into_the_answer() -> None:
    instance = renderer()
    instance.render_event({"type": "goal_wrapup", "text": "summarise"})
    # The wrapup is an instruction to the model, not part of its answer.
    assert "summarise" not in instance.answer_text()


def test_the_renderer_still_ignores_unknown_events() -> None:
    instance = renderer()
    instance.render_event({"type": "something_new", "x": 1})
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_stream_events.py -q`
Expected: FAIL — `AttributeError: 'RichInteractiveRenderer' object has no attribute 'status_line'`

- [ ] **Step 3: 写实现**

在 `rich_renderer.py` 中：`render_event` 的分支里加三行，并补两个查询方法。已有
`self._status` 保存状态字符串，本任务只是让它承载 goal 状态：

```python
        elif event_type == "goal_round_started":
            self._goal_status = f"goal · round {event.get('round', '?')}"
        elif event_type == "goal_status":
            self._goal_status = f"goal · {event.get('status', 'unknown')}"
        elif event_type == "goal_wrapup":
            # An instruction to the model, not part of its answer.
            pass
```

`__init__` 里加 `self._goal_status: str | None = None`。新增方法：

```python
    def status_line(self) -> str | None:
        """The one-line goal state to show while a run is in progress."""
        return self._goal_status

    def answer_text(self) -> str:
        """Everything the model actually said, without tool or status noise."""
        return "".join(segment.text for segment in self._segments)
```

（`_segments` 是渲染器已有的字段；若其元素不是带 `.text` 的对象，改用既有的取文本方式
拼装，保持与 `expansion_text()` 同一套来源。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_stream_events.py tests/test_rich_renderer.py -q`
Expected: PASS。既有渲染器测试必须照旧通过。

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_cli/plugins/rich_renderer.py tests/test_goal_stream_events.py
git commit -m "feat: show goal state in the CLI without polluting the answer"
```

---

### Task 8: `code` preset

**Files:**
- Modify: `src/langharness_core/plugin.py`
- Modify: `src/langharness_core/plugins/agents/directory.py`
- Test: `tests/test_code_preset.py`（新建）

**Interfaces:**
- Consumes: 前四个计划交付的全部插件（都已在 catalog 里）。
- Produces：
  - `plugin.AGENT_PRESETS: dict[str, tuple[str, ...]]`，键 `"simple"` / `"code"`
  - `plugin.agent_preset_bindings(preset: str) -> tuple[str, ...]`
  - `plugin.agent_preset_modules(preset: str) -> list[str]`
  - `directory._bindings` 支持 preset

**preset 怎么表达**：agent 的插件配置里加一条 `preset` 绑定，属性 `preset.name` 指向
preset 名。它与普通插件条目同形，因此配置存储、`/plugins` 路由、CLI 编辑都不用改。

**合并规则**：preset 给基线集合，显式条目覆盖或增补；`enabled: false` 的条目从基线里摘掉。
`simple_agent` 不带 preset，行为与现在完全一致。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_code_preset.py`：

```python
"""Tests for the agent presets and the code agent assembly."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from langharness_core.plugin import (
    AGENT_PLUGIN_CATALOG,
    AGENT_PRESETS,
    agent_preset_bindings,
    agent_preset_modules,
)


def test_the_simple_preset_is_the_default_set() -> None:
    assert agent_preset_bindings("simple") == ("tools", "name")


def test_the_code_preset_carries_what_a_coding_agent_needs() -> None:
    bindings = agent_preset_bindings("code")
    for name in (
        "workspace",
        "workspace-state",
        "code-tools",
        "exec",
        "exec-tools",
        "repository-instructions",
        "prompt",
        "goal",
        "goal-tools",
        "verify-tool",
        "ask-user",
        "verifier",
        "run-controller",
        "name",
    ):
        assert name in bindings, name
    # The plain workspace toolbox is replaced by the bounded code one.
    assert "tools" not in bindings


def test_every_preset_plugin_exists_in_the_catalog() -> None:
    for preset, names in AGENT_PRESETS.items():
        for name in names:
            assert name in AGENT_PLUGIN_CATALOG, f"{preset}: {name}"


def test_preset_modules_are_the_catalog_modules() -> None:
    modules = agent_preset_modules("code")
    assert "langharness_core.plugins.tools.code_workspace" in modules
    assert "langharness_core.plugins.controller.goal_round" in modules
    assert len(set(modules)) == len(modules)


def test_an_unknown_preset_falls_back_to_simple() -> None:
    assert agent_preset_bindings("nope") == agent_preset_bindings("simple")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_preset.py -q`
Expected: FAIL — `ImportError: cannot import name 'AGENT_PRESETS'`

- [ ] **Step 3: 写实现**

在 `src/langharness_core/plugin.py` 中，`DEFAULT_AGENT_PLUGINS` 之后加：

```python
AGENT_PRESETS: dict[str, tuple[str, ...]] = {
    "simple": DEFAULT_AGENT_PLUGINS,
    "code": (
        "name",
        "workspace",
        "workspace-state",
        "code-tools",
        "exec",
        "exec-tools",
        "repository-instructions",
        "prompt",
        "goal",
        "goal-tools",
        "verify-tool",
        "ask-user",
        "verifier",
        "run-controller",
    ),
}
PRESET_PLUGIN_KEY = "preset"
PRESET_NAME_PROPERTY = "preset.name"


def agent_preset_bindings(preset: str) -> tuple[str, ...]:
    """The plugin names a preset turns on; unknown names fall back to simple."""
    return AGENT_PRESETS.get(preset, AGENT_PRESETS["simple"])


def agent_preset_modules(preset: str) -> list[str]:
    """The bundle modules a preset needs installed before it can materialize."""
    return list(
        dict.fromkeys(
            AGENT_PLUGIN_CATALOG[name][0]
            for name in agent_preset_bindings(preset)
            if name in AGENT_PLUGIN_CATALOG
        )
    )


def preset_name_of(plugins: dict[str, Any]) -> str | None:
    """Read the preset an agent configuration selected, if any."""
    entry = plugins.get(PRESET_PLUGIN_KEY)
    if not isinstance(entry, dict):
        return None
    properties = entry.get("properties")
    if not isinstance(properties, dict):
        return None
    name = properties.get(PRESET_NAME_PROPERTY)
    return str(name) if name else None
```

同时把 `run-controller` / `verify-tool` / `ask-user` 三个条目加进 `AGENT_PLUGIN_CATALOG`
与 `AGENT_PLUGIN_DESCRIPTIONS`：

```python
    "run-controller": (
        "langharness_core.plugins.controller.goal_round",
        "goal-round-controller-plugin-factory",
        SPEC_RUN_CONTROLLER,
    ),
    "verify-tool": (
        "langharness_core.plugins.goals.verify_tool",
        "verify-tool-plugin-factory",
        SPEC_TOOL,
    ),
    "ask-user": (
        "langharness_core.plugins.goals.ask_user",
        "ask-user-plugin-factory",
        SPEC_TOOL,
    ),
```

并在 `builtin_package()` 里为三者各加一个 `agent` template contribution；导入
`SPEC_RUN_CONTROLLER`。

在 `src/langharness_core/plugins/agents/directory.py` 中，把 `_bindings` 换成：

```python
    def _bindings(self, agent_id: str) -> dict[str, dict[str, Any]]:
        """Effective plugin bindings: the preset's baseline, then overrides.

        An explicit entry wins over the preset; ``enabled: false`` removes a
        plugin the preset would otherwise turn on.
        """
        stored = self._configs.get(agent_id)
        if stored is None:
            return {plugin: {"enabled": True} for plugin in DEFAULT_AGENT_PLUGINS}
        preset = preset_name_of(stored)
        baseline = (
            agent_preset_bindings(preset) if preset is not None else DEFAULT_AGENT_PLUGINS
        )
        bindings: dict[str, dict[str, Any]] = {
            name: {"enabled": True} for name in baseline
        }
        for name, entry in stored.items():
            if name == PRESET_PLUGIN_KEY:
                continue
            if entry.get("enabled", True):
                bindings[name] = dict(entry)
            else:
                bindings.pop(name, None)
        return bindings
```

并把 `_materialize` 里算 `required` 的那几行改成先看 preset：

```python
        bindings = self._bindings(agent_id)
        preset = preset_name_of(self._configs.get(agent_id, {}))
        required = (
            agent_preset_modules(preset)
            if preset is not None
            else list(agent_required_modules())
        )
        if "llm" in bindings:
            required = [*required, AGENT_PLUGIN_CATALOG["llm"][0]]
```

（相应地从 `langharness_core.plugin` 导入 `agent_preset_bindings`、
`agent_preset_modules`、`preset_name_of`、`PRESET_PLUGIN_KEY`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_preset.py tests/test_agent_directory.py -q`
Expected: PASS。**`test_agent_directory.py` 必须照旧通过**——没有 preset 的 agent
走的是同一条默认路径。

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugin.py src/langharness_core/plugins/agents/directory.py \
        tests/test_code_preset.py
git commit -m "feat: assemble the code agent from a preset"
```

---

### Task 9: 组合作用域 e2e

**Files:**
- Test: `tests/test_code_agent_e2e.py`（新建）

**Interfaces:**
- Consumes: Task 8 的 preset；计划 1–3a 的全部组件。
- Produces：一个用真实 Pelix 框架把 code agent 装起来、跑完一轮的 e2e。

**这一条把前面各计划分散的 scope e2e 合成一个整体**：prompt、工具、workspace、goal
四者必须在同一 agent 上互不串，且第二个 agent 看不到第一个的任何东西。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_code_agent_e2e.py`：

```python
"""End-to-end: a code agent assembled from the preset, in its own scope."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path
from typing import Any

from langharness_core.contracts import (
    SPEC_GOAL,
    SPEC_TOOL,
    SPEC_WORKSPACE,
    SPEC_WORKSPACE_STATE,
)
from langharness_core.plugin import agent_preset_modules, agent_preset_bindings, agent_plugin_descriptor
from langharness_plugin.errors import PluginAlreadyInstalledError
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id

TEXT_TOOLS = {"list_directory", "read_file", "search_text", "apply_patch", "workspace_status"}


def build_agent(manager: PluginManager, agent_id: str, workspace: Path) -> None:
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    for name in agent_preset_bindings("code"):
        descriptor = agent_plugin_descriptor(name)
        properties: dict[str, Any] = {"plugin.agent_id": agent_id}
        if name == "workspace":
            properties["plugin.workspace.root_dir"] = str(workspace)
        if name == "goal":
            properties["plugin.goal.path"] = str(workspace / "goals.sqlite3")
        if name == "verifier":
            properties["plugin.verify.commands"] = [["python3", "-c", "pass"]]
        manager.create_instance(
            descriptor.factory, descriptor.module, scope, properties=properties
        )


def install_preset(manager: PluginManager) -> None:
    """Install every descriptor the preset needs, once each.

    Installing twice raises PluginAlreadyInstalledError, and two agents share
    one install — so this runs once before either agent is built, not per agent.
    """
    for name in agent_preset_bindings("code"):
        descriptor = agent_plugin_descriptor(name)
        try:
            manager.install_descriptor(descriptor)
        except PluginAlreadyInstalledError:
            continue


def test_two_code_agents_share_nothing(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        alpha_dir = tmp_path / "alpha"
        beta_dir = tmp_path / "beta"
        alpha_dir.mkdir()
        beta_dir.mkdir()
        (alpha_dir / "only-alpha.py").write_text("x\n", encoding="utf-8")

        install_preset(manager)
        build_agent(manager, "alpha", alpha_dir)
        build_agent(manager, "beta", beta_dir)

        def tools_for(agent: str) -> dict[str, Any]:
            providers: list[Any] = manager.find_services(
                SPEC_TOOL, filter=f"(plugin.scope_id=agent:{agent})"
            )
            merged: dict[str, Any] = {}
            for provider in providers:
                merged.update({item.name: item for item in provider.get_tools()})
            return merged

        alpha, beta = tools_for("alpha"), tools_for("beta")
        assert TEXT_TOOLS | {"exec_command"} <= set(alpha)
        assert set(alpha) == set(beta)
        assert [e["name"] for e in alpha["list_directory"].invoke({})["entries"]] == [
            "only-alpha.py"
        ]
        assert beta["list_directory"].invoke({})["entries"] == []

        alpha_goals: list[Any] = manager.find_services(
            SPEC_GOAL, filter="(plugin.scope_id=agent:alpha)"
        )
        beta_goals: list[Any] = manager.find_services(
            SPEC_GOAL, filter="(plugin.scope_id=agent:beta)"
        )
        alpha_goals[0].create_goal("alpha objective", [{"id": "c1", "text": "t"}])
        assert beta_goals[0].current_goal() is None

        alpha_state: list[Any] = manager.find_services(
            SPEC_WORKSPACE_STATE, filter="(plugin.scope_id=agent:alpha)"
        )
        beta_state: list[Any] = manager.find_services(
            SPEC_WORKSPACE_STATE, filter="(plugin.scope_id=agent:beta)"
        )
        alpha["apply_patch"].invoke(
            {
                "patch": "*** Begin Patch\n*** Add File: made.py\n+x\n*** End Patch\n"
            }
        )
        assert alpha_state[0].get_generation() == 1
        assert beta_state[0].get_generation() == 0

        alpha_ws: list[Any] = manager.find_services(
            SPEC_WORKSPACE, filter="(plugin.scope_id=agent:alpha)"
        )
        assert Path(alpha_ws[0].get_workspace()) == alpha_dir.resolve()
    finally:
        manager.stop()
```

- [ ] **Step 2: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_agent_e2e.py -q`
Expected: PASS。

- [ ] **Step 3: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 4: Commit**

```bash
git add tests/test_code_agent_e2e.py
git commit -m "test: prove two code agents share no prompt, tools, workspace or goal"
```

---

### Task 10: 三协议 e2e 与真实模型验收

**Files:**
- Test: `tests/test_code_agent_e2e.py`（追加）
- Create: `tests/fixtures/failing_repo.py`（新建）

**Interfaces:**
- Consumes: 全部前序工作。
- Produces：三协议多工具 turn 的确定性 e2e；以及 `real_llm` marker 的真实模型验收。

**为什么真实模型验收只能作为补充**：设计第 12 节明确"真实模型 smoke test 只作为补充，
不能替代 fake-model 的确定性状态机测试"。因此它标 `real_llm`、默认 skip，不在
`make check` 门禁里。

- [ ] **Step 1: 写三协议确定性测试**

在 `tests/test_code_agent_e2e.py` 末尾追加：

```python
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

PROTOCOLS = ("chat", "anthropic", "responses")


class ScriptedModel(BaseChatModel):
    """Calls apply_patch once, then verify, then stops."""

    step: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.step += 1
        if self.step == 1:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="",
                tool_calls=[{
                    "name": "apply_patch",
                    "args": {"patch": "*** Begin Patch\n*** Add File: made.py\n+x\n*** End Patch\n"},
                    "id": "c1", "type": "tool_call",
                }],
            ))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_a_tool_call_turn_streams_events_on_every_protocol(
    protocol: str, tmp_path: Path
) -> None:
    """`chat`/`responses` keep calls in tool_calls; anthropic embeds them in
    content, so protocol-specific breakage only shows up here."""
    from langharness_core.contracts import SPEC_AGENT_LOOP, SPEC_LLM

    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for name in agent_preset_bindings("code"):
            manager.install_descriptor(agent_plugin_descriptor(name))
        llm = agent_plugin_descriptor("llm")
        manager.install_descriptor(llm)
        workspace = tmp_path / protocol
        workspace.mkdir()
        build_agent(manager, "alpha", workspace)
        manager.create_instance(
            llm.factory,
            llm.module,
            agent_instance_scope_id("alpha"),
            properties={
                "plugin.model.instance": ScriptedModel(),
                "plugin.model.protocol": protocol,
                "plugin.agent_id": "alpha",
            },
        )
        loop = manager.find_service(
            SPEC_AGENT_LOOP, filter="(plugin.scope_id=agent:alpha)"
        )
        import asyncio

        async def collect() -> list[dict]:
            return [event async for event in loop.astream("add a file")]

        events = asyncio.run(collect())
        kinds = [item["type"] for item in events]
        assert "tool_call" in kinds
        assert "tool_output" in kinds
        assert not any(item["type"] == "error" for item in events), events
        assert (workspace / "made.py").read_text(encoding="utf-8") == "x\n"
    finally:
        manager.stop()
```

- [ ] **Step 2: 写真实模型验收（默认 skip）**

`tests/fixtures/failing_repo.py`：

```python
"""Builds a throwaway repository whose test fails until a bug is fixed."""

from __future__ import annotations

from pathlib import Path

MODULE = '''"""Tiny module with an off-by-one bug."""


def total(values: list[int]) -> int:
    """Sum every value but the last — the bug under test."""
    return sum(values[:-1])
'''

TEST = '''from calc import total


def test_total_sums_every_value() -> None:
    assert total([1, 2, 3]) == 6
'''

INSTRUCTIONS = """# AGENTS.md

- Run `python -m pytest -q` before claiming a fix.
- `calc.total` must sum every value it is given.
"""


def build(root: Path) -> Path:
    """Materialise the repository and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(INSTRUCTIONS, encoding="utf-8")
    (root / "calc.py").write_text(MODULE, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    return root
```

在 `tests/test_code_agent_e2e.py` 末尾追加：

```python
@pytest.mark.real_llm
def test_the_agent_fixes_a_failing_test_in_a_temp_repo(tmp_path: Path) -> None:
    """The design's Phase 1 acceptance scenario, against a real provider.

    Skipped unless LANG_HARNESS_REAL_LLM_PROVIDER is set; it is a supplement to
    the deterministic tests above, never a replacement for them.
    """
    import os

    from tests.fixtures.failing_repo import build

    provider = os.environ.get("LANG_HARNESS_REAL_LLM_PROVIDER")
    if not provider:
        pytest.skip("set LANG_HARNESS_REAL_LLM_PROVIDER to run real LLM smoke")

    workspace = build(tmp_path / "repo")
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for name in agent_preset_bindings("code"):
            manager.install_descriptor(agent_plugin_descriptor(name))
        llm = agent_plugin_descriptor("llm")
        manager.install_descriptor(llm)
        build_agent(manager, "alpha", workspace)
        manager.create_instance(
            llm.factory,
            llm.module,
            agent_instance_scope_id("alpha"),
            properties={
                "plugin.model.name": provider,
                "plugin.model.protocol": os.environ.get(
                    "LANG_HARNESS_REAL_LLM_PROTOCOL", "chat"
                ),
                "plugin.agent_id": "alpha",
            },
        )
        # The verifier must run the repo's own test command.
        verifier = agent_plugin_descriptor("verifier")
        manager.create_instance(
            verifier.factory,
            verifier.module,
            agent_instance_scope_id("alpha"),
            properties={
                "plugin.verify.commands": [["python", "-m", "pytest", "-q"]],
                "plugin.agent_id": "alpha",
            },
        )
        goal_store = manager.find_service(
            SPEC_GOAL, filter="(plugin.scope_id=agent:alpha)"
        )
        goal_store.create_goal(
            "Fix calc.total so the test passes",
            [{"id": "c1", "text": "python -m pytest -q exits 0"}],
        )
        loop = manager.find_service(
            SPEC_AGENT_LOOP, filter="(plugin.scope_id=agent:alpha)"
        )

        import asyncio

        async def run() -> list[dict]:
            return [
                event
                async for event in loop.astream(
                    "The test in test_calc.py fails. Fix the bug."
                )
            ]

        events = asyncio.run(run())
        assert not any(item["type"] == "error" for item in events), events
        # The artifact is what matters, not what the model said about it.
        import subprocess

        completed = subprocess.run(
            ["python", "-m", "pytest", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout
        assert goal_store.current_goal() is None  # the goal settled
    finally:
        manager.stop()
```

- [ ] **Step 3: 运行确定性测试**

Run: `.venv/bin/python -m pytest tests/test_code_agent_e2e.py -q`
Expected: PASS（三协议各一个 + 组合 e2e；真实模型那条被 skip）

- [ ] **Step 4: 跑全量门禁**

Run: `make check`
Expected: 全部通过，覆盖率 ≥95%。

- [ ] **Step 5: Commit**

```bash
git add tests/test_code_agent_e2e.py tests/fixtures/failing_repo.py
git commit -m "test: cover three protocols and the real-model acceptance scenario"
```

---

## 完成后的验收

计划 3b 落地后应能观测到：

1. turn 静止但 goal 仍 `active` 时自动续跑，`astream` 里能看到 `goal_round_started`；
2. 达到 `max_rounds` 由宿主直接转 `blocked`，不需要模型配合（Task 4）；
3. 没有 checkpointer 时**拒绝**续跑并说明原因，而不是悄悄从头开始（Task 4/5）；
4. `ask_user` 把 goal 转入 `awaiting_user` 并停止自动运行（Task 3）；
5. `verify` 能在提交前告诉模型还缺什么，且不改变 goal（Task 2）；
6. `"preset": "code"` 装配出的 agent 具备全部能力，`simple_agent` 行为不变（Task 8）；
7. 两个 code agent 的 prompt、工具、workspace、goal 四者都不串（Task 9）；
8. 三种协议的多工具 turn 都产出 `tool_call` / `tool_output` 且无 `error` 事件（Task 10）。

至此设计第 11 节的 Phase 1 全部落地，第 11 节的验收场景由 `real_llm` 那条测试覆盖。
Phase 2（PTC）与 Phase 3（OS sandbox、reviewer、web/skills/delivery）仍未开始，见
`docs/plans/2026-09-22-code-agent-phase1-roadmap.md`。
