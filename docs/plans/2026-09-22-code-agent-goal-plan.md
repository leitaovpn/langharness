# Code Agent 计划 3a：Goal、验证门禁与完成判定 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"模型说完成了"不再等于"目标完成了"。goal 有持久化状态与 revision，验证由宿主执行命令并签发带 mutation generation 的报告，只有全部门禁通过 `finish_goal(status="complete")` 才会把 goal 推进到 `complete`。

**Architecture:** 新增 `agent.plugin.goal` 与 `agent.plugin.verifier` 两个服务规格。goal 存在同步 `sqlite3`（工具在 LangChain 线程池里跑，同步存储才能直接被工具调用；仓库已有 `langharness_scope/sqlite.py` 这一先例）。验证器由宿主执行 argv 数组形式的门禁命令并签发 `VerificationReport`，完成判定是一个纯函数 `evaluate_completion`，`finish_goal` 工具只是它的薄封装。

**Tech Stack:** Python 3.13、Pelix/iPOPO、`sqlite3`、pydantic `args_schema`、pytest。

## Global Constraints

- 质量门槛（AGENTS.md）：每个 commit 必须过 `make check`（Ruff → mypy → Pyright → clean-process import → pytest），覆盖率 ≥95%。**执行前基线 96.05%**。
- TDD：先写失败测试，再写最小实现；每个任务独立 commit。
- 生产代码 mypy strict、Pyright standard 不允许全局放宽。**`tests/` 不是 mypy 豁免区**：新测试文件要么全量注解，要么在 line 2 加 `# mypy: ignore-errors`，并按需补 `# pyright: report...=false`。
- **`self.x = param` 形式赋值会被 Pyright 推断为 `str`**，赋给 Literal 类型别名时必须显式注解（计划 2a 已踩两次）。
- 插件间通信只走 Pelix service spec（Protocol）；`@Provides`/`@Requires` 声明 Protocol 类。
- `PluginDescriptor` 只在 `langharness_core/plugin.py` 构造；组件实现在 `plugins/<topic>/<name>.py`。
- 工具异常一律走 `tool_error()`，不得把 traceback 当字符串返回。
- 文档用中文，代码/命令用英文；commit 用 `feat:` / `test:` / `docs:`。
- 设计文档：`docs/designs/2026-09-22-code-agent-design.md` 第 8 节。
- 前置：计划 1 与计划 2a 已在本分支合入（`WorkspaceProvider`、`WorkspaceStateProvider`、`tool_error`）。

## 范围与依赖

**本计划（3a）交付**：goal 状态机与持久化、宿主验证器、完成门禁、`get_goal` / `finish_goal` 工具。

**明确不含（留给 3b）**：run controller 自动续跑、`ask_user`、`awaiting_user` 转移、新流事件、`code` preset 装配、三协议 e2e、真实模型临时仓库验收。3b 依赖本计划的 goal 与 verifier。

**与计划 2b 的关系**：`exec_command`（agent 自己跑命令）不在本计划内。设计第 6.1 节把 `verify` 定义为**宿主**执行门禁，因此即使没有 2b，完成判定也是端到端可用的：agent 改文件，宿主跑门禁出报告。3b 的 controller 与 2b 都会复用本计划的验证器内部实现。

## 完成门禁（设计第 8.3 节的 6 条，逐条落到代码）

`evaluate_completion` 是纯函数，返回拒绝原因元组，空元组表示通过：

| # | 检查 | 判定 |
|---:|---|---|
| 1 | goal 存在且 revision 匹配 | `expected_revision != goal.revision` → `goal_revision_mismatch` |
| 2 | 验证报告存在且通过 | 无报告 → `verification_missing`；`report.passed is False` → `verification_failed` |
| 3 | 验证新鲜 | `report.mutation_generation != current_generation` → `verification_stale` |
| 4 | 必选检查退出码为 0 | 任一 `check.exit_code != 0` → `check_failed`（附命令名） |
| 5 | 验收项有证据映射 | 任一 criterion 的 `evidence` 为空 → `criterion_without_evidence` |
| 6 | 无未决审批/后台 job | `blockers` 非空 → `pending_activity`；且当存在非 `manual/user_review` 的验收项而 workspace 自 goal 创建以来代次未变 → `no_artifacts` |

**第 6 条的现状**：job 与审批分别属于计划 2b 与 3b。门禁通过 `blockers: Sequence[str]` 形参接收它们，现在调用方传空序列；2b/3b 接上后填值即可，门禁本身不改。

**为什么 generation 由 agent 写入推进、而不是验证器**：验证器自己跑的 `make check` 会写 `.coverage`、`__pycache__` 等构建产物。若这些也算 mutation，报告一签发就立刻过期。因此 generation 只记录 **agent 发起的源码写入**（计划 2a 的 `apply_patch` 等），验证器不推进它，并在**开始**时快照。

## 文件布局

**新建**

| 文件 | 职责 |
|---|---|
| `src/langharness_core/plugins/goals/model.py` | `Goal` / `AcceptanceCriterion` / `GoalStatus` 辅助与序列化 |
| `src/langharness_core/plugins/goals/gate.py` | 纯函数 `evaluate_completion` 与 `GateRejection` |
| `src/langharness_core/plugins/goals/sqlite.py` | `SQLiteGoalStore`：revision CAS、线程安全 |
| `src/langharness_core/plugins/verifier/project.py` | `ProjectVerifier`：argv 执行、退出码、digest、artifact |
| `src/langharness_core/plugins/goals/tools.py` | `GoalToolsPlugin`：`get_goal` / `finish_goal` |
| `tests/test_goal_model.py`、`tests/test_goal_gate.py`、`tests/test_goal_store.py`、`tests/test_project_verifier.py`、`tests/test_goal_tools.py`、`tests/test_goal_scope.py` | 对应测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/langharness_core/contracts.py` | `SPEC_GOAL`、`SPEC_VERIFIER`、`GoalStatus`、`AcceptanceCriterion`、`Goal`、`VerificationCheck`、`VerificationArtifact`、`VerificationReport`、`GoalProvider`、`VerificationProvider` |
| `src/langharness_core/plugin.py` | catalog 增加 `goal` / `verifier` / `goal-tools`；`builtin_package()` 增加三个 template |
| `tests/test_imports.py` | `PUBLIC_MODULES` 增加新模块 |

---

### Task 1: Goal 与验证报告的契约和值类型

**Files:**
- Modify: `src/langharness_core/contracts.py`
- Test: `tests/test_goal_model.py`（新建）

**Interfaces:**
- Produces（后续所有任务复用）：
  - `SPEC_GOAL = "agent.plugin.goal"`、`SPEC_VERIFIER = "agent.plugin.verifier"`
  - `GoalStatus = Literal["active", "awaiting_user", "verifying", "complete", "blocked", "cancelled"]`
  - `TERMINAL_GOAL_STATUSES: frozenset[str]`
  - `AcceptanceCriterion(id: str, text: str, evidence: str | None = None)`
  - `Goal(id, revision, objective, acceptance_criteria, status, rounds_started, max_rounds, mutation_generation, blocker_history, created_at, updated_at)`
  - `VerificationCheck(name, command, exit_code, stdout_digest, duration_ms)`
  - `VerificationArtifact(path, sha256)`
  - `VerificationReport(id, goal_revision, mutation_generation, started_at, finished_at, checks, artifacts, passed)`
  - `GoalProvider` Protocol：`create_goal`、`get_goal`、`current_goal`、`update_goal`、`get_plugin_info`
  - `VerificationProvider` Protocol：`verify(goal_revision) -> VerificationReport`、`get_plugin_info`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_model.py`：

```python
"""Contract tests for the goal and verification services."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_GOAL,
    SPEC_VERIFIER,
    TERMINAL_GOAL_STATUSES,
    AcceptanceCriterion,
    Goal,
    GoalProvider,
    VerificationArtifact,
    VerificationCheck,
    VerificationProvider,
    VerificationReport,
)
from langharness_plugin.validation import contract_for


def sample_goal(**overrides: object) -> Goal:
    values: dict[str, object] = {
        "id": "g1",
        "revision": 1,
        "objective": "fix the flaky test",
        "acceptance_criteria": (
            AcceptanceCriterion(id="c1", text="test passes"),
        ),
        "status": "active",
        "rounds_started": 0,
        "max_rounds": 32,
        "mutation_generation": 0,
        "blocker_history": (),
        "created_at": "2026-09-22T00:00:00+00:00",
        "updated_at": "2026-09-22T00:00:00+00:00",
    }
    values.update(overrides)
    return Goal(**values)  # type: ignore[arg-type]


def test_specifications_are_pinned() -> None:
    assert contract_for(SPEC_GOAL) is GoalProvider
    assert contract_for(SPEC_VERIFIER) is VerificationProvider
    assert SPEC_GOAL in ALL_PLUGIN_SPECS
    assert SPEC_VERIFIER in ALL_PLUGIN_SPECS


def test_terminal_statuses_are_exactly_the_three_ends() -> None:
    assert TERMINAL_GOAL_STATUSES == {"complete", "blocked", "cancelled"}
    assert "active" not in TERMINAL_GOAL_STATUSES
    assert "awaiting_user" not in TERMINAL_GOAL_STATUSES


def test_goal_is_frozen_and_hashable() -> None:
    goal = sample_goal()
    assert goal == sample_goal()
    assert hash(goal) == hash(sample_goal())


def test_goal_revision_is_part_of_identity() -> None:
    assert sample_goal(revision=1) != sample_goal(revision=2)


def test_criteria_default_to_no_evidence() -> None:
    assert AcceptanceCriterion(id="c1", text="t").evidence is None


def test_report_carries_generation_and_checks() -> None:
    report = VerificationReport(
        id="vr1",
        goal_revision=3,
        mutation_generation=7,
        started_at="a",
        finished_at="b",
        checks=(
            VerificationCheck(
                name="make check",
                command=("make", "check"),
                exit_code=0,
                stdout_digest="sha256:x",
                duration_ms=12,
            ),
        ),
        artifacts=(VerificationArtifact(path="src/a.py", sha256="sha256:y"),),
        passed=True,
    )
    assert report.mutation_generation == 7
    assert report.checks[0].command == ("make", "check")
    assert report.artifacts[0].path == "src/a.py"


def test_a_failed_check_makes_the_report_representable() -> None:
    report = VerificationReport(
        id="vr2",
        goal_revision=1,
        mutation_generation=1,
        started_at="a",
        finished_at="b",
        checks=(
            VerificationCheck(
                name="make check",
                command=("make", "check"),
                exit_code=2,
                stdout_digest="sha256:z",
                duration_ms=5,
            ),
        ),
        artifacts=(),
        passed=False,
    )
    assert report.passed is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'SPEC_GOAL'`

- [ ] **Step 3: 写实现**

在 `src/langharness_core/contracts.py` 中：

`SPEC_WORKSPACE_STATE` 之后加两个规格常量：

```python
SPEC_GOAL = "agent.plugin.goal"
SPEC_VERIFIER = "agent.plugin.verifier"
```

把两者追加进 `ALL_PLUGIN_SPECS`（紧跟 `SPEC_WORKSPACE_STATE`）。

`MutationRecord` 之后加值类型：

```python
GoalStatus = Literal[
    "active", "awaiting_user", "verifying", "complete", "blocked", "cancelled"
]
TERMINAL_GOAL_STATUSES: frozenset[str] = frozenset(
    {"complete", "blocked", "cancelled"}
)


@dataclass(frozen=True, slots=True)
class AcceptanceCriterion:
    """One thing the user asked for, and the evidence that it holds.

    ``evidence`` names a tool call id, a file hash, a test case, or the literal
    ``"manual/user_review"``. A criterion with no evidence can never be
    satisfied by the host, which is what stops "it works" from counting.
    """

    id: str
    text: str
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class Goal:
    """A durable objective plus the bookkeeping that gates its completion."""

    id: str
    revision: int
    objective: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    status: GoalStatus
    rounds_started: int
    max_rounds: int
    mutation_generation: int
    blocker_history: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """One gate command the host ran, and how it ended."""

    name: str
    command: tuple[str, ...]
    exit_code: int
    stdout_digest: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class VerificationArtifact:
    """A workspace file the goal produced, pinned at a content hash."""

    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """What the host observed when it verified one goal revision.

    Only the summary lives here; full command output stays in the audit log so
    verification noise cannot crowd out the conversation.
    """

    id: str
    goal_revision: int
    mutation_generation: int
    started_at: str
    finished_at: str
    checks: tuple[VerificationCheck, ...]
    artifacts: tuple[VerificationArtifact, ...]
    passed: bool
```

`WorkspaceStateProvider` 之后加两个 Protocol：

```python
@service_contract(SPEC_GOAL)
@runtime_checkable
class GoalProvider(Protocol):
    """Contract implemented by every ``agent.plugin.goal`` service."""

    def create_goal(
        self,
        objective: str,
        acceptance_criteria: list[dict[str, str]],
        *,
        max_rounds: int = 32,
    ) -> Goal: ...

    def get_goal(self, goal_id: str) -> Goal | None: ...

    def current_goal(self) -> Goal | None: ...

    def update_goal(
        self,
        goal_id: str,
        *,
        expected_revision: int,
        status: str | None = None,
        blocker: str | None = None,
    ) -> Goal: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_VERIFIER)
@runtime_checkable
class VerificationProvider(Protocol):
    """Contract implemented by every ``agent.plugin.verifier`` service."""

    def verify(self, goal_revision: int) -> VerificationReport: ...

    def get_plugin_info(self) -> dict[str, str]: ...
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_model.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/contracts.py tests/test_goal_model.py
git commit -m "feat: add goal and verification service contracts"
```

---

### Task 2: 完成门禁（纯函数）

**Files:**
- Create: `src/langharness_core/plugins/goals/__init__.py`
- Create: `src/langharness_core/plugins/goals/gate.py`
- Test: `tests/test_goal_gate.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `Goal`、`VerificationReport`、`VerificationArtifact`、`TERMINAL_GOAL_STATUSES`。
- Produces（Task 5 复用）：
  - `gate.GateRejection(code: str, message: str)`
  - `gate.evaluate_completion(goal, *, expected_revision, report, current_generation, artifacts, blockers) -> tuple[GateRejection, ...]`
  - `gate.MANUAL_EVIDENCE = "manual/user_review"`

**为什么是纯函数：** 门禁是产品最关键的判定，必须能在不启动 Pelix、不跑命令的情况下逐条测试。工具只是把参数凑齐后调用它。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_gate.py`：

```python
"""Tests for the completion gate."""

from __future__ import annotations

from langharness_core.contracts import (
    AcceptanceCriterion,
    Goal,
    VerificationArtifact,
    VerificationCheck,
    VerificationReport,
)
from langharness_core.plugins.goals.gate import (
    MANUAL_EVIDENCE,
    GateRejection,
    evaluate_completion,
)


def goal(**overrides: object) -> Goal:
    values: dict[str, object] = {
        "id": "g1",
        "revision": 3,
        "objective": "fix the bug",
        "acceptance_criteria": (
            AcceptanceCriterion(id="c1", text="test passes", evidence="tool:call_1"),
        ),
        "status": "verifying",
        "rounds_started": 2,
        "max_rounds": 32,
        "mutation_generation": 2,
        "blocker_history": (),
        "created_at": "a",
        "updated_at": "b",
    }
    values.update(overrides)
    return Goal(**values)  # type: ignore[arg-type]


def report(**overrides: object) -> VerificationReport:
    values: dict[str, object] = {
        "id": "vr1",
        "goal_revision": 3,
        "mutation_generation": 7,
        "started_at": "a",
        "finished_at": "b",
        "checks": (
            VerificationCheck(
                name="make check",
                command=("make", "check"),
                exit_code=0,
                stdout_digest="sha256:x",
                duration_ms=10,
            ),
        ),
        "artifacts": (VerificationArtifact(path="src/a.py", sha256="sha256:y"),),
        "passed": True,
    }
    values.update(overrides)
    return VerificationReport(**values)  # type: ignore[arg-type]


def codes(rejections: tuple[GateRejection, ...]) -> list[str]:
    return [item.code for item in rejections]


def test_a_complete_goal_with_fresh_evidence_passes() -> None:
    assert (
        evaluate_completion(
            goal(),
            expected_revision=3,
            report=report(),
            current_generation=7,
            artifacts=report().artifacts,
            blockers=(),
        )
        == ()
    )


def test_a_stale_revision_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(revision=4),
        expected_revision=3,
        report=report(goal_revision=3),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["goal_revision_mismatch"]


def test_a_missing_report_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=None,
        current_generation=7,
        artifacts=(),
        blockers=(),
    )
    # Both are true at once: there is no report, and nothing was produced.
    assert codes(rejections) == ["verification_missing", "no_artifacts"]


def test_a_failed_report_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(passed=False),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["verification_failed"]


def test_editing_after_verification_invalidates_the_report() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(mutation_generation=7),
        current_generation=8,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["verification_stale"]


def test_a_failing_check_is_named_in_the_rejection() -> None:
    failing = VerificationCheck(
        name="make check",
        command=("make", "check"),
        exit_code=2,
        stdout_digest="sha256:z",
        duration_ms=3,
    )
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(checks=(failing,), passed=False),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert "check_failed" in codes(rejections)
    assert any("make check" in item.message for item in rejections)


def test_a_criterion_without_evidence_is_rejected() -> None:
    rejections = evaluate_completion(
        goal(
            acceptance_criteria=(
                AcceptanceCriterion(id="c1", text="it works", evidence=None),
            )
        ),
        expected_revision=3,
        report=report(),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=(),
    )
    assert codes(rejections) == ["criterion_without_evidence"]
    assert "c1" in rejections[0].message


def test_pending_activity_blocks_completion() -> None:
    rejections = evaluate_completion(
        goal(),
        expected_revision=3,
        report=report(),
        current_generation=7,
        artifacts=report().artifacts,
        blockers=("job j1 still running",),
    )
    assert codes(rejections) == ["pending_activity"]
    assert "j1" in rejections[0].message


def test_a_goal_that_changed_nothing_has_no_artifacts() -> None:
    rejections = evaluate_completion(
        goal(mutation_generation=7),
        expected_revision=3,
        report=report(),
        current_generation=7,
        artifacts=(),
        blockers=(),
    )
    assert codes(rejections) == ["no_artifacts"]


def test_a_manual_only_goal_needs_no_artifacts() -> None:
    assert (
        evaluate_completion(
            goal(
                acceptance_criteria=(
                    AcceptanceCriterion(
                        id="c1", text="looks right", evidence=MANUAL_EVIDENCE
                    ),
                ),
                mutation_generation=7,
            ),
            expected_revision=3,
            report=report(artifacts=()),
            current_generation=7,
            artifacts=(),
            blockers=(),
        )
        == ()
    )


def test_every_rejection_is_reported_not_just_the_first() -> None:
    rejections = evaluate_completion(
        goal(
            revision=9,
            acceptance_criteria=(
                AcceptanceCriterion(id="c1", text="t", evidence=None),
            ),
        ),
        expected_revision=3,
        report=None,
        current_generation=7,
        artifacts=(),
        blockers=("approval pending",),
    )
    assert set(codes(rejections)) == {
        "goal_revision_mismatch",
        "verification_missing",
        "criterion_without_evidence",
        "pending_activity",
        "no_artifacts",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.goals'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/goals/__init__.py`：

```python
"""Goal tracking and completion gating."""
```

`src/langharness_core/plugins/goals/gate.py`：

```python
"""The deterministic checks that stand between "done" and "complete".

Deliberately a pure function over values: this is the product's central
judgement, so it must be testable without Pelix, a workspace, or a subprocess.
The caller gathers the inputs; nothing here reaches out.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langharness_core.contracts import (
    Goal,
    VerificationArtifact,
    VerificationReport,
)

MANUAL_EVIDENCE = "manual/user_review"


@dataclass(frozen=True, slots=True)
class GateRejection:
    """One reason completion was refused, phrased for the model to act on."""

    code: str
    message: str


def evaluate_completion(
    goal: Goal,
    *,
    expected_revision: int,
    report: VerificationReport | None,
    current_generation: int,
    artifacts: Sequence[VerificationArtifact],
    blockers: Sequence[str],
) -> tuple[GateRejection, ...]:
    """Return every reason this goal may not complete; empty means it may.

    All reasons are returned rather than the first, so one round of the loop
    can fix everything that is wrong instead of discovering it one at a time.
    """
    rejections: list[GateRejection] = []
    if goal.revision != expected_revision:
        rejections.append(
            GateRejection(
                "goal_revision_mismatch",
                f"goal is at revision {goal.revision}, not {expected_revision}; "
                "the objective changed since this work started",
            )
        )
    if report is None:
        rejections.append(
            GateRejection(
                "verification_missing",
                "no verification report exists for this goal; run verify first",
            )
        )
    else:
        if not report.passed:
            rejections.append(
                GateRejection(
                    "verification_failed",
                    f"verification report {report.id} did not pass",
                )
            )
        for check in report.checks:
            if check.exit_code != 0:
                rejections.append(
                    GateRejection(
                        "check_failed",
                        f"check {check.name!r} exited {check.exit_code}: "
                        f"{' '.join(check.command)}",
                    )
                )
        if report.mutation_generation != current_generation:
            rejections.append(
                GateRejection(
                    "verification_stale",
                    f"report was produced at generation "
                    f"{report.mutation_generation} but the workspace is at "
                    f"{current_generation}; the files changed after it ran",
                )
            )
    for criterion in goal.acceptance_criteria:
        if not criterion.evidence:
            rejections.append(
                GateRejection(
                    "criterion_without_evidence",
                    f"acceptance criterion {criterion.id!r} "
                    f"({criterion.text!r}) has no evidence attached",
                )
            )
    if blockers:
        rejections.append(
            GateRejection(
                "pending_activity",
                "work is still outstanding: " + "; ".join(blockers),
            )
        )
    needs_artifact = any(
        criterion.evidence != MANUAL_EVIDENCE
        for criterion in goal.acceptance_criteria
    )
    if needs_artifact and not artifacts:
        rejections.append(
            GateRejection(
                "no_artifacts",
                "no workspace file changed since the goal started, so there is "
                "nothing to point at as the deliverable; mark the criteria "
                f"{MANUAL_EVIDENCE!r} if the goal truly produces no file",
            )
        )
    return tuple(rejections)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_gate.py -q`
Expected: PASS（11 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/goals/__init__.py \
        src/langharness_core/plugins/goals/gate.py tests/test_goal_gate.py
git commit -m "feat: gate goal completion on fresh verified evidence"
```

---

### Task 3: SQLite goal store（revision CAS）

**Files:**
- Create: `src/langharness_core/plugins/goals/sqlite.py`
- Test: `tests/test_goal_store.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `GoalProvider`、`Goal`、`AcceptanceCriterion`。
- Produces（Task 5、3b 复用）：
  - `sqlite.GoalConflictError(message)` —— revision CAS 失败
  - `sqlite.SQLiteGoalPlugin`，工厂 `sqlite-goal-plugin-factory`，模块 `langharness_core.plugins.goals.sqlite`
  - 属性 `plugin.goal.path`（默认 `langharness_goals.sqlite3`）、`plugin.goal.max_rounds`（默认 32）
  - `BLOCKER_THRESHOLD = 3` —— 同一 blocker 连续出现多少次才允许转 `blocked`

**并发模型：** 工具在 LangChain 线程池里跑，所以用同步 `sqlite3` + `threading.Lock`，连接以 `check_same_thread=False` 打开。仓库已有同步 sqlite 先例（`langharness_scope/sqlite.py`）。

**CAS 语义：** `update_goal(..., expected_revision=N)` 只在存储中 revision 恰为 N 时成功，并把 revision 加一；否则抛 `GoalConflictError`。这是设计第 8.3 节第 1 条"防止旧 turn 完成已经被用户修改的目标"的实现。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_store.py`：

```python
"""Tests for the SQLite goal store."""
# mypy: ignore-errors

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from langharness_core.contracts import GoalProvider
from langharness_core.plugins.goals.sqlite import (
    BLOCKER_THRESHOLD,
    GoalConflictError,
    SQLiteGoalPlugin,
)
from langharness_plugin.validation import validate


def store(tmp_path: Path) -> SQLiteGoalPlugin:
    plugin = SQLiteGoalPlugin()
    plugin._path = str(tmp_path / "goals.sqlite3")
    return plugin


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(store(tmp_path), GoalProvider) == ()


def test_a_new_goal_starts_active_at_revision_one(tmp_path: Path) -> None:
    goal = store(tmp_path).create_goal("fix the bug", [{"id": "c1", "text": "t"}])
    assert goal.status == "active"
    assert goal.revision == 1
    assert goal.rounds_started == 0
    assert goal.acceptance_criteria[0].id == "c1"
    assert goal.blocker_history == ()


def test_goals_round_trip_through_sqlite(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    created = plugin.create_goal("objective", [{"id": "c1", "text": "t"}])
    assert plugin.get_goal(created.id) == created
    assert plugin.current_goal() == created


def test_an_unknown_goal_is_none(tmp_path: Path) -> None:
    assert store(tmp_path).get_goal("nope") is None


def test_current_goal_is_the_latest_active_one(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    first = plugin.create_goal("one", [])
    second = plugin.create_goal("two", [])
    assert plugin.current_goal().id == second.id
    plugin.update_goal(first.id, expected_revision=1, status="complete")
    assert plugin.current_goal().id == second.id


def test_completing_a_goal_removes_it_from_current(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("only", [])
    plugin.update_goal(goal.id, expected_revision=1, status="complete")
    assert plugin.current_goal() is None
    assert plugin.get_goal(goal.id).status == "complete"


def test_a_stale_revision_is_refused(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    plugin.update_goal(goal.id, expected_revision=1, status="verifying")
    with pytest.raises(GoalConflictError):
        plugin.update_goal(goal.id, expected_revision=1, status="complete")


def test_each_update_advances_the_revision(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    second = plugin.update_goal(goal.id, expected_revision=1, status="verifying")
    third = plugin.update_goal(goal.id, expected_revision=2, status="active")
    assert (second.revision, third.revision) == (2, 3)


def test_rounds_can_be_incremented(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    updated = plugin.update_goal(goal.id, expected_revision=1, start_round=True)
    assert updated.rounds_started == 1


def test_blocker_history_accumulates(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    first = plugin.update_goal(goal.id, expected_revision=1, blocker="no network")
    assert first.blocker_history == ("no network",)
    second = plugin.update_goal(goal.id, expected_revision=2, blocker="no network")
    assert second.blocker_history == ("no network", "no network")


def test_the_blocker_threshold_is_three() -> None:
    assert BLOCKER_THRESHOLD == 3


def test_state_survives_a_new_plugin_instance(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("durable", [{"id": "c1", "text": "t"}])
    reopened = store(tmp_path)
    assert reopened.get_goal(goal.id) == goal


def test_concurrent_updates_never_lose_a_revision(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])

    def bump(index: int) -> int:
        # Each worker retries once on conflict, so the revision must end up
        # exactly equal to the number of successful updates.
        for _ in range(50):
            current = plugin.get_goal(goal.id)
            try:
                return plugin.update_goal(
                    goal.id, expected_revision=current.revision, status="active"
                ).revision
            except GoalConflictError:
                continue
        raise AssertionError("could not make progress")

    with ThreadPoolExecutor(max_workers=8) as pool:
        revisions = list(pool.map(bump, range(16)))
    assert len(set(revisions)) == 16
    assert plugin.get_goal(goal.id).revision == 17


def test_criterion_evidence_is_recorded_after_a_completion(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [{"id": "c1", "text": "t"}, {"id": "c2", "text": "u"}])
    updated = plugin.set_criterion_evidence(goal.id, {"c1": "tool:call_7"})
    assert updated.acceptance_criteria[0].evidence == "tool:call_7"
    assert updated.acceptance_criteria[1].evidence is None


def test_recording_evidence_does_not_advance_the_revision(tmp_path: Path) -> None:
    # Evidence is what the completion was granted on, not a revision of the
    # goal; advancing it would invalidate the report that just passed.
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [{"id": "c1", "text": "t"}])
    updated = plugin.set_criterion_evidence(goal.id, {"c1": "tool:call_7"})
    assert updated.revision == goal.revision


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert store(tmp_path).get_plugin_info() == {
        "name": "sqlite-goal",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.goals.sqlite'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/goals/sqlite.py`：

```python
"""Durable goal state with revision compare-and-swap.

Sync ``sqlite3`` on purpose: tools run in LangChain's thread pool, and a sync
store is the only shape a sync tool can call directly. The connection is
shared across those threads, so every access takes the lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pelix.ipopo.decorators import ComponentFactory, Invalidate, Property, Provides

from langharness_core.contracts import (
    AcceptanceCriterion,
    Goal,
    GoalProvider,
    GoalStatus,
)

BLOCKER_THRESHOLD = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    objective TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    status TEXT NOT NULL,
    rounds_started INTEGER NOT NULL DEFAULT 0,
    max_rounds INTEGER NOT NULL DEFAULT 32,
    mutation_generation INTEGER NOT NULL DEFAULT 0,
    blocker_history TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goals_status ON goals (status, rowid);
"""


class GoalConflictError(RuntimeError):
    """Raised when a goal update loses the revision race."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        revision=row["revision"],
        objective=row["objective"],
        acceptance_criteria=tuple(
            AcceptanceCriterion(
                id=item["id"], text=item["text"], evidence=item.get("evidence")
            )
            for item in json.loads(row["acceptance_criteria"])
        ),
        status=row["status"],
        rounds_started=row["rounds_started"],
        max_rounds=row["max_rounds"],
        mutation_generation=row["mutation_generation"],
        blocker_history=tuple(json.loads(row["blocker_history"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@ComponentFactory("sqlite-goal-plugin-factory")
@Provides(GoalProvider)
@Property("_plugin_name", "plugin.name", "sqlite-goal")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_path", "plugin.goal.path", "langharness_goals.sqlite3")
@Property("_max_rounds", "plugin.goal.max_rounds", 32)
class SQLiteGoalPlugin:
    """Persists goals and enforces the revision CAS that guards completion."""

    def __init__(self) -> None:
        self._plugin_name = "sqlite-goal"
        self._plugin_version = "1.0.0"
        self._path = "langharness_goals.sqlite3"
        self._max_rounds = 32
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    def create_goal(
        self,
        objective: str,
        acceptance_criteria: list[dict[str, str]],
        *,
        max_rounds: int = 32,
    ) -> Goal:
        goal_id = uuid4().hex
        timestamp = _now()
        criteria = [
            {
                "id": str(item.get("id") or f"c{index + 1}"),
                "text": str(item.get("text", "")),
                "evidence": item.get("evidence"),
            }
            for index, item in enumerate(acceptance_criteria or [])
        ]
        with self._lock:
            connection = self._connection_or_open()
            connection.execute(
                "INSERT INTO goals (id, revision, objective, acceptance_criteria,"
                " status, rounds_started, max_rounds, mutation_generation,"
                " blocker_history, created_at, updated_at)"
                " VALUES (?, 1, ?, ?, 'active', 0, ?, 0, '[]', ?, ?)",
                (
                    goal_id,
                    objective,
                    json.dumps(criteria),
                    max_rounds,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            row = self._select(connection, goal_id)
        return _row_to_goal(row)

    def get_goal(self, goal_id: str) -> Goal | None:
        with self._lock:
            connection = self._connection_or_open()
            row = self._select(connection, goal_id)
        return _row_to_goal(row) if row is not None else None

    def current_goal(self) -> Goal | None:
        """The newest goal that is not in a terminal state."""
        with self._lock:
            connection = self._connection_or_open()
            cursor = connection.execute(
                "SELECT * FROM goals WHERE status NOT IN"
                " ('complete', 'blocked', 'cancelled')"
                " ORDER BY rowid DESC LIMIT 1"
            )
            row = cursor.fetchone()
        return _row_to_goal(row) if row is not None else None

    def update_goal(
        self,
        goal_id: str,
        *,
        expected_revision: int,
        status: str | None = None,
        blocker: str | None = None,
        start_round: bool = False,
        mutation_generation: int | None = None,
    ) -> Goal:
        with self._lock:
            connection = self._connection_or_open()
            row = self._select(connection, goal_id)
            if row is None:
                raise GoalConflictError(f"unknown goal {goal_id!r}")
            if row["revision"] != expected_revision:
                raise GoalConflictError(
                    f"goal {goal_id!r} is at revision {row['revision']}, "
                    f"not {expected_revision}"
                )
            history = list(json.loads(row["blocker_history"]))
            if blocker is not None:
                history.append(blocker)
            connection.execute(
                "UPDATE goals SET revision = revision + 1, status = ?,"
                " blocker_history = ?, rounds_started = rounds_started + ?,"
                " mutation_generation = ?, updated_at = ? WHERE id = ?",
                (
                    status or row["status"],
                    json.dumps(history),
                   1 if start_round else 0,
                    (
                        mutation_generation
                        if mutation_generation is not None
                        else row["mutation_generation"]
                    ),
                    _now(),
                    goal_id,
                ),
            )
            connection.commit()
            updated = self._select(connection, goal_id)
        return _row_to_goal(updated)

    def set_criterion_evidence(
        self, goal_id: str, evidence: dict[str, str]
    ) -> Goal:
        """Record what proved each criterion, without revising the goal.

        Deliberately not a revision bump: this runs *after* a completion has
        been granted, and moving the revision would invalidate the very report
        that earned it.
        """
        with self._lock:
            connection = self._connection_or_open()
            row = self._select(connection, goal_id)
            if row is None:
                raise GoalConflictError(f"unknown goal {goal_id!r}")
            criteria = json.loads(row["acceptance_criteria"])
            for item in criteria:
                replacement = evidence.get(item["id"])
                if replacement:
                    item["evidence"] = replacement
            connection.execute(
                "UPDATE goals SET acceptance_criteria = ? WHERE id = ?",
                (json.dumps(criteria), goal_id),
            )
            connection.commit()
            updated = self._select(connection, goal_id)
        return _row_to_goal(updated)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    @staticmethod
    def _select(connection: sqlite3.Connection, goal_id: str) -> sqlite3.Row | None:
        cursor = connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        row: sqlite3.Row | None = cursor.fetchone()
        return row

    def _connection_or_open(self) -> sqlite3.Connection:
        if self._connection is None:
            path = Path(self._path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            connection.commit()
            self._connection = connection
        return self._connection

    @Invalidate
    def _invalidate(self, bundle_context: Any) -> None:
        with self._lock:
            if self._connection is None:
                return
            self._connection.close()
            self._connection = None
```

**注意**：`update_goal` 比 Task 1 的 Protocol 多两个关键字参数（`start_round`、
`mutation_generation`）。Protocol 是"调用兼容性"契约，实现多出**带默认值**的参数是
允许的（契约校验只拒绝无默认值的多余必填参数），因此不必改 Protocol。

同时把 `_max_rounds` 用到 `create_goal` 的默认值上：把签名改成
`max_rounds: int | None = None` 并在函数体内 `max_rounds if max_rounds is not None else int(self._max_rounds)`——这样 `plugin.goal.max_rounds` 属性才真正生效。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_store.py -q`
Expected: PASS（13 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/goals/sqlite.py tests/test_goal_store.py
git commit -m "feat: persist goals with revision compare-and-swap"
```

---

### Task 4: 宿主验证器（argv 门禁命令）

**Files:**
- Create: `src/langharness_core/plugins/verifier/__init__.py`
- Create: `src/langharness_core/plugins/verifier/project.py`
- Test: `tests/test_project_verifier.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `VerificationProvider`、`VerificationCheck`、`VerificationArtifact`、`VerificationReport`；计划 2a 的 `WorkspaceProvider`、`WorkspaceStateProvider`、`common/text.py` 的 `digest`。
- Produces（Task 5、3b 复用）：
  - 工厂 `project-verifier-plugin-factory`，模块 `langharness_core.plugins.verifier.project`
  - 属性 `plugin.verify.commands`（argv 数组的列表，默认 `[]`）、`plugin.verify.timeout_seconds`（默认 900）
  - `DEFAULT_TIMEOUT_SECONDS = 900`、`MAX_OUTPUT_BYTES = 65_536`
  - `ProjectVerifier.verify(goal_revision: int) -> VerificationReport`

**命令为什么是 argv 数组：** 设计第 10 节明确"验证命令使用 argv 数组存储，避免为了配置而引入一次额外 shell 解析"。`shell=False` 同时消除了注入面。

**为什么验证器不推进 mutation generation：** 它自己跑的 `make check` 会写 `.coverage`、`__pycache__`。若这些算 mutation，报告一签发就过期。generation 只记录 agent 发起的源码写入。

**超时与输出上限：** 单条命令 `plugin.verify.timeout_seconds` 秒后 `TimeoutExpired`，该 check 记为 `exit_code = -1` 并在 stdout 里说明超时；stdout/stderr 合并后截断到 `MAX_OUTPUT_BYTES`，只留 digest 进报告。

**没有配置命令时：** `verify` 返回 `passed=True` 且 `checks=()`，并在报告里如实反映——此时门禁的第 4 条自动满足，第 2/3/5 条仍然把关。这是诚实的默认：没配置门禁命令不代表验证通过，只代表宿主没有可执行的确定性检查，其他检查照常生效。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_project_verifier.py`：

```python
"""Tests for the host-side project verifier."""
# mypy: ignore-errors

from __future__ import annotations

import sys
from pathlib import Path

from langharness_core.contracts import VerificationProvider
from langharness_core.plugins.verifier.project import (
    ProjectVerifier,
    _artifact_set,
)
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def verifier(workspace: Path, *commands: list[str]) -> ProjectVerifier:
    instance = ProjectVerifier()
    root = WorkspaceRootPlugin()
    root._root = str(workspace)
    instance._workspace = root
    instance._state = WorkspaceStatePlugin()
    instance._commands = [list(item) for item in commands]
    return instance


def ok(*text: str) -> list[str]:
    return [sys.executable, "-c", f"print({text[0]!r})" if text else "print('ok')"]


def failing(*text: str) -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(3)"]


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(verifier(tmp_path), VerificationProvider) == ()


def test_a_passing_command_produces_a_passing_report(tmp_path: Path) -> None:
    report = verifier(tmp_path, ok("all good")).verify(goal_revision=2)
    assert report.passed is True
    assert report.goal_revision == 2
    assert report.checks[0].exit_code == 0
    assert report.checks[0].stdout_digest.startswith("sha256:")
    assert report.checks[0].duration_ms >= 0
    assert report.id.startswith("vr_")


def test_a_failing_command_makes_the_report_fail(tmp_path: Path) -> None:
    report = verifier(tmp_path, failing()).verify(goal_revision=1)
    assert report.passed is False
    assert report.checks[0].exit_code == 3


def test_every_command_runs_even_after_one_fails(tmp_path: Path) -> None:
    report = verifier(tmp_path, failing(), ok("second")).verify(goal_revision=1)
    assert [check.exit_code for check in report.checks] == [3, 0]


def test_no_configured_commands_passes_with_no_checks(tmp_path: Path) -> None:
    report = verifier(tmp_path).verify(goal_revision=1)
    assert report.checks == ()
    assert report.passed is True


def test_the_report_records_the_generation_it_verified(tmp_path: Path) -> None:
    instance = verifier(tmp_path, ok())
    instance._state.record_mutation("a.py", "apply_patch")
    report = instance.verify(goal_revision=1)
    assert report.mutation_generation == 1


def test_a_command_that_writes_does_not_advance_the_generation(tmp_path: Path) -> None:
    # The gate's own build artifacts must not invalidate the gate's own report.
    writer = [
        sys.executable,
        "-c",
        f"open({str(tmp_path / '.coverage')!r}, 'w').write('x')",
    ]
    instance = verifier(tmp_path, writer)
    report = instance.verify(goal_revision=1)
    assert instance._state.get_generation() == 0
    assert report.mutation_generation == 0


def test_a_hanging_command_is_timed_out(tmp_path: Path) -> None:
    instance = verifier(tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"])
    instance._timeout_seconds = 1
    report = instance.verify(goal_revision=1)
    assert report.passed is False
    assert report.checks[0].exit_code == -1
    assert "timed out" in _last_output(instance).lower()


def test_a_missing_binary_is_reported_not_raised(tmp_path: Path) -> None:
    report = verifier(tmp_path, ["/nonexistent/binary"]).verify(goal_revision=1)
    assert report.passed is False
    assert report.checks[0].exit_code == -1


def test_artifacts_come_from_recorded_mutations(tmp_path: Path) -> None:
    instance = verifier(tmp_path, ok())
    instance._state.record_mutation(
        "src/a.py", "apply_patch", after_sha256="sha256:new"
    )
    instance._state.record_mutation("src/gone.py", "apply_patch", after_sha256=None)
    report = instance.verify(goal_revision=1)
    assert report.artifacts == (
        type(report.artifacts[0])(path="src/a.py", sha256="sha256:new"),
    )


def test_artifact_set_keeps_the_newest_hash_per_path() -> None:
    from langharness_core.contracts import MutationRecord

    records = [
        MutationRecord("a.py", 1, "apply_patch", None, "sha256:v1"),
        MutationRecord("a.py", 2, "apply_patch", "sha256:v1", "sha256:v2"),
        MutationRecord("b.py", 3, "apply_patch", "sha256:x", None),
    ]
    assert [(item.path, item.sha256) for item in _artifact_set(records)] == [
        ("a.py", "sha256:v2")
    ]


def _last_output(instance: ProjectVerifier) -> str:
    return instance._last_output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_project_verifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.verifier'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/verifier/__init__.py`：

```python
"""Host-side verification plugins."""
```

`src/langharness_core/plugins/verifier/project.py`：

```python
"""Runs the project's configured gates and signs a verification report.

The host owns this, not the model: a gate the model could skip or edit is not
a gate. Commands are argv arrays executed without a shell.
"""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)

from langharness_core.common.text import digest
from langharness_core.contracts import (
    MutationRecord,
    VerificationArtifact,
    VerificationCheck,
    VerificationProvider,
    VerificationReport,
    WorkspaceProvider,
    WorkspaceStateProvider,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

DEFAULT_TIMEOUT_SECONDS = 900
MAX_OUTPUT_BYTES = 65_536


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _artifact_set(records: list[MutationRecord]) -> tuple[VerificationArtifact, ...]:
    """The newest surviving content hash per path.

    A path written and then deleted has no ``after_sha256``; it is not an
    artifact, it is the absence of one.
    """
    newest: dict[str, str] = {}
    for record in records:
        if record.after_sha256:
            newest[record.path] = record.after_sha256
        else:
            newest.pop(record.path, None)
    return tuple(
        VerificationArtifact(path=path, sha256=newest[path])
        for path in sorted(newest)
    )


@ComponentFactory("project-verifier-plugin-factory")
@Provides(VerificationProvider)
@Property("_plugin_name", "plugin.name", "project-verifier")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_commands", "plugin.verify.commands", None)
@Property("_timeout_seconds", "plugin.verify.timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_workspace", "_state")
class ProjectVerifier:
    """Executes the configured gate commands and reports what it observed."""

    def __init__(self) -> None:
        self._plugin_name = "project-verifier"
        self._plugin_version = "1.0.0"
        self._commands: Any = None
        self._timeout_seconds = DEFAULT_TIMEOUT_SECONDS
        self._workspace: Any = None
        self._state: Any = None
        self._last_output = ""
        self._guards: dict[str, ContractGuard] = {
            "_workspace": ContractGuard(self, "_workspace", WorkspaceProvider),
            "_state": ContractGuard(self, "_state", WorkspaceStateProvider),
        }

    @BindField("_workspace", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_workspace")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    @BindField("_state", if_valid=True)
    def _on_state_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].admit(service)

    @UnbindField("_state")
    def _on_state_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def verify(self, goal_revision: int) -> VerificationReport:
        """Run every configured gate and summarise the outcome.

        The generation is snapshotted before anything runs: build artifacts the
        gate itself writes are not agent mutations, and counting them would
        invalidate this report the moment it is signed.
        """
        started_at = _now()
        generation = self._state.get_generation() if self._state else 0
        records = self._state.mutations() if self._state else []
        checks = tuple(self._run(command) for command in self._configured())
        return VerificationReport(
            id=f"vr_{uuid4().hex}",
            goal_revision=goal_revision,
            mutation_generation=generation,
            started_at=started_at,
            finished_at=_now(),
            checks=checks,
            artifacts=_artifact_set(list(records)),
            passed=all(check.exit_code == 0 for check in checks),
        )

    def _configured(self) -> list[list[str]]:
        commands = self._commands or []
        return [[str(part) for part in item] for item in commands if item]

    def _run(self, command: list[str]) -> VerificationCheck:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=self._workspace.get_workspace() if self._workspace else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=float(self._timeout_seconds),
            )
            output = completed.stdout + completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            output = f"command timed out after {self._timeout_seconds}s"
            exit_code = -1
        except OSError as exc:
            output = f"command could not be run: {exc}"
            exit_code = -1
        duration_ms = int((time.monotonic() - started) * 1000)
        self._last_output = output[:MAX_OUTPUT_BYTES]
        return VerificationCheck(
            name=" ".join(command),
            command=tuple(command),
            exit_code=exit_code,
            stdout_digest=digest(output),
            duration_ms=duration_ms,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_project_verifier.py -q`
Expected: PASS（12 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/verifier tests/test_project_verifier.py
git commit -m "feat: run project gates and sign verification reports"
```

---

### Task 5: `get_goal` 与 `finish_goal` 工具

**Files:**
- Create: `src/langharness_core/plugins/goals/tools.py`
- Test: `tests/test_goal_tools.py`（新建）

**Interfaces:**
- Consumes: Task 1 的契约；Task 2 的 `evaluate_completion`；Task 3 的 `GoalConflictError`、`BLOCKER_THRESHOLD`；Task 4 的 verifier；计划 2a 的 `WorkspaceStateProvider`、`tool_error`。
- Produces（3b 复用）：
  - 工厂 `goal-tools-plugin-factory`，模块 `langharness_core.plugins.goals.tools`
  - `GoalToolsPlugin`，`@Provides(ToolProvider)`，`@RequiresBest` goal/verifier/state，`@ScopedDependencies` 同三个字段
  - 工具 `get_goal`、`finish_goal`；`get_tool_presentations()` 只列 `get_goal`

**`finish_goal` 的输入**（设计第 6.1 节）：

```json
{"status": "complete" | "blocked", "summary": "...", "evidence": {"c1": "tool:call_7"}, "blocker": "..."}
```

**`status="complete"` 的行为**：跑 verifier → 调 `evaluate_completion` → 通过则 CAS 写 `complete`，返回收尾上下文；不通过则返回 `tool_error("completion_rejected", ...)`，`details` 里带每一条拒绝原因，**goal 状态不变**。模型据此继续干活。

**`status="blocked"` 的行为**：把 blocker 追加进历史；同一 blocker 连续出现次数 <
`BLOCKER_THRESHOLD` 时拒绝并告知已出现几次；达到阈值才转 `blocked`。设计第 6.1 节与
`completion.md` 都要求"同一阻塞连续达到阈值后才可标 blocked"。

**证据写入**：`evidence` 映射按 criterion id 写入 goal 的 acceptance_criteria 后再判定，
所以第 5 条检查看的是本次提交的证据——但写入本身也要走 CAS，且必须在门禁**之后**才落库，
否则一次失败的完成尝试会污染 goal。做法：先在内存里构造带证据的 goal 副本交给
`evaluate_completion`，通过后才把证据与状态一次写入。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_tools.py`：

```python
"""Tests for the goal tools."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import GoalProvider, ToolProvider, VerificationProvider
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_core.plugins.goals.tools import GoalToolsPlugin
from langharness_core.plugins.verifier.project import ProjectVerifier
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path, *, failing: bool = False) -> GoalToolsPlugin:
    instance = GoalToolsPlugin()
    goal_store = SQLiteGoalPlugin()
    goal_store._path = str(tmp_path / "goals.sqlite3")
    state = WorkspaceStatePlugin()
    root = WorkspaceRootPlugin()
    root._root = str(tmp_path)
    verifier = ProjectVerifier()
    verifier._workspace = root
    verifier._state = state
    verifier._commands = [
        ["python3", "-c", "import sys; sys.exit(1 if %r else 0)" % failing]
    ]
    instance._goals = goal_store
    instance._verifier = verifier
    instance._state = state
    return instance


def tools(instance: GoalToolsPlugin) -> dict[str, object]:
    return {item.name: item for item in instance.get_tools()}


def test_plugin_conforms_to_its_tool_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_goal_dependencies_are_scoped(tmp_path: Path) -> None:
    from langharness_plugin.scoped_dependencies import scoped_fields

    assert scoped_fields(GoalToolsPlugin) == frozenset(
        {"_goals", "_verifier", "_state"}
    )


def test_get_goal_without_one_reports_none(tmp_path: Path) -> None:
    result = tools(build(tmp_path))["get_goal"].invoke({})
    assert result["goal"] is None


def test_get_goal_returns_the_current_goal(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    result = tools(instance)["get_goal"].invoke({})
    assert result["goal"]["id"] == goal.id
    assert result["goal"]["status"] == "active"
    assert result["goal"]["revision"] == 1
    assert result["goal"]["acceptance_criteria"][0]["id"] == "c1"


def test_finish_goal_without_a_goal_is_rejected(tmp_path: Path) -> None:
    result = tools(build(tmp_path))["finish_goal"].invoke(
        {"status": "complete", "summary": "done"}
    )
    assert result["error"]["code"] == "no_active_goal"


def test_complete_is_rejected_without_evidence(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {}}
    )
    assert result["error"]["code"] == "completion_rejected"
    assert "criterion_without_evidence" in str(result["error"]["details"])
    # The goal must not move on a failed attempt.
    assert instance._goals.current_goal().status == "active"


def test_complete_is_rejected_when_a_gate_fails(tmp_path: Path) -> None:
    instance = build(tmp_path, failing=True)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = tools(instance)["finish_goal"].invoke(
        {
            "status": "complete",
            "summary": "done",
            "evidence": {"c1": "tool:call_1"},
        }
    )
    assert result["error"]["code"] == "completion_rejected"
    assert "check_failed" in str(result["error"]["details"])
    assert instance._goals.current_goal().status == "active"


def test_complete_is_accepted_when_every_gate_passes(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "fixed it", "evidence": {"c1": "tool:call_1"}}
    )
    assert result["status"] == "complete"
    assert result["report_id"].startswith("vr_")
    stored = instance._goals.get_goal(goal.id)
    assert stored.status == "complete"
    assert stored.acceptance_criteria[0].evidence == "tool:call_1"


def test_editing_after_verification_blocks_completion(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    state_generation = instance._state
    # A report produced now, then another edit before finishing.
    instance._verify_then_edit = True  # marker for the shim below
    original = instance._verifier.verify

    def verify_then_edit(goal_revision: int):
        report = original(goal_revision)
        state_generation.record_mutation("late.py", "apply_patch", after_sha256="sha256:z")
        return report

    instance._verifier.verify = verify_then_edit
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {"c1": "tool:call_1"}}
    )
    assert result["error"]["code"] == "completion_rejected"
    assert "verification_stale" in str(result["error"]["details"])


def test_blocked_needs_the_same_blocker_repeatedly(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    finish = tools(instance)["finish_goal"]
    first = finish.invoke(
        {"status": "blocked", "summary": "stuck", "blocker": "no network"}
    )
    assert first["error"]["code"] == "blocker_not_established"
    assert "1 of 3" in first["error"]["message"]
    finish.invoke({"status": "blocked", "summary": "stuck", "blocker": "no network"})
    third = finish.invoke(
        {"status": "blocked", "summary": "stuck", "blocker": "no network"}
    )
    assert third["status"] == "blocked"
    assert instance._goals.current_goal() is None


def test_a_different_blocker_resets_the_count(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    finish = tools(instance)["finish_goal"]
    finish.invoke({"status": "blocked", "summary": "s", "blocker": "no network"})
    finish.invoke({"status": "blocked", "summary": "s", "blocker": "no network"})
    result = finish.invoke({"status": "blocked", "summary": "s", "blocker": "no disk"})
    assert result["error"]["code"] == "blocker_not_established"
    assert "1 of 3" in result["error"]["message"]


def test_blocked_requires_a_blocker_text(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    result = tools(instance)["finish_goal"].invoke(
        {"status": "blocked", "summary": "stuck"}
    )
    assert result["error"]["code"] == "invalid_arguments"


def test_presentations_skip_finish_goal(tmp_path: Path) -> None:
    presentations = build(tmp_path).get_tool_presentations()
    assert presentations == {"get_goal": "get_goal()"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.goals.tools'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/goals/tools.py`：

```python
"""The goal tools: read the objective, and try to close it."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

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
from langharness_core.contracts import (
    GoalProvider,
    ToolProvider,
    VerificationProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.goals.gate import evaluate_completion
from langharness_core.plugins.goals.sqlite import BLOCKER_THRESHOLD, GoalConflictError
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


class FinishGoalArgs(BaseModel):
    status: Literal["complete", "blocked"] = Field(
        description="'complete' to close the goal, 'blocked' to give up on it."
    )
    summary: str = Field(
        min_length=1, description="One-paragraph account of what happened."
    )
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps acceptance-criterion id to its evidence: a tool call id, a "
            "file sha256, a test case name, or 'manual/user_review'."
        ),
    )
    blocker: str | None = Field(
        default=None, description="Required when status is 'blocked'."
    )


def _goal_payload(goal: Any) -> dict[str, Any]:
    return {
        "id": goal.id,
        "revision": goal.revision,
        "objective": goal.objective,
        "status": goal.status,
        "rounds_started": goal.rounds_started,
        "max_rounds": goal.max_rounds,
        "acceptance_criteria": [
            {"id": item.id, "text": item.text, "evidence": item.evidence}
            for item in goal.acceptance_criteria
        ],
        "blocker_history": list(goal.blocker_history),
    }


@ComponentFactory("goal-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "goal-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@RequiresBest("_goals", GoalProvider, optional=True, immediate_rebind=True)
@RequiresBest("_verifier", VerificationProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_goals", "_verifier", "_state")
class GoalToolsPlugin:
    """Exposes the goal to the model, and enforces the completion gate."""

    def __init__(self) -> None:
        self._plugin_name = "goal-tools"
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
        return [self._get_tool(), self._finish_tool()]

    def get_tool_presentations(self) -> dict[str, str]:
        """``finish_goal`` is absent: its summary reads better as the bare name."""
        return {"get_goal": "get_goal()"}

    def _get_tool(self) -> StructuredTool:
        plugin = self

        def get_goal() -> dict[str, Any]:
            goal = plugin._goals.current_goal()
            return {"goal": _goal_payload(goal) if goal is not None else None}

        return StructuredTool.from_function(
            func=get_goal,
            name="get_goal",
            description=(
                "Read the current goal: its objective, status, revision, "
                "acceptance criteria and how many rounds have run. Check it "
                "before deciding you are finished, and re-read it after any "
                "rejection — a rejected completion leaves the goal unchanged, "
                "and the rejection names what is still missing. Returns "
                "{\"goal\": {\"id\": str, \"revision\": int, \"objective\": "
                "str, \"status\": str, \"rounds_started\": int, \"max_rounds\": "
                "int, \"acceptance_criteria\": [{\"id\": str, \"text\": str, "
                "\"evidence\": str|null}], \"blocker_history\": [str]}|null}."
            ),
            args_schema=NoArgs,
        )

    def _finish_tool(self) -> StructuredTool:
        plugin = self

        def finish_goal(
            status: str,
            summary: str,
            evidence: dict[str, str] | None = None,
            blocker: str | None = None,
        ) -> dict[str, Any]:
            goal = plugin._goals.current_goal()
            if goal is None:
                return tool_error("no_active_goal", "there is no goal to finish")
            if status == "blocked":
                return plugin._block(goal, summary, blocker)
            if status != "complete":
                return tool_error(
                    "invalid_arguments", f"unknown status {status!r}"
                )
            return plugin._complete(goal, summary, evidence or {})

        return StructuredTool.from_function(
            func=finish_goal,
            name="finish_goal",
            description=(
                "Ask the host to close the current goal. This is the only way a "
                "goal reaches 'complete': the host runs the project's gates and "
                "checks that the report is fresh, that every acceptance "
                "criterion carries evidence, and that nothing is still "
                "outstanding. A rejection tells you exactly what is missing and "
                "leaves the goal untouched — fix it and try again rather than "
                "restating that you are done. Use status='blocked' only when "
                "the same blocker has repeated; the host refuses the first few "
                "attempts so a goal is not abandoned early. Returns {\"status\": "
                "'complete'|'blocked', \"goal_id\": str, \"revision\": int, "
                "\"report_id\": str|null, \"summary\": str}, or {\"error\": "
                "{code, message, retryable, details}} where details lists every "
                "unmet gate condition."
            ),
            args_schema=FinishGoalArgs,
        )

    def _complete(
        self, goal: Any, summary: str, evidence: dict[str, str]
    ) -> dict[str, Any]:
        report = self._verifier.verify(goal.revision)
        candidate = replace(
            goal,
            acceptance_criteria=tuple(
                replace(item, evidence=evidence.get(item.id, item.evidence))
                for item in goal.acceptance_criteria
            ),
        )
        current_generation = self._state.get_generation() if self._state else 0
        rejections = evaluate_completion(
            candidate,
            expected_revision=goal.revision,
            report=report,
            current_generation=current_generation,
            artifacts=report.artifacts,
            blockers=(),
        )
        if rejections:
            return tool_error(
                "completion_rejected",
                "the goal does not meet its completion conditions yet: "
                + "; ".join(item.message for item in rejections),
                retryable=True,
                reasons=[item.code for item in rejections],
                report_id=report.id,
            )
        try:
            updated = self._goals.update_goal(
                goal.id, expected_revision=goal.revision, status="complete"
            )
        except GoalConflictError as exc:
            return tool_error("goal_revision_mismatch", str(exc), retryable=True)
        self._goals.set_criterion_evidence(updated.id, evidence)
        return {
            "status": "complete",
            "goal_id": updated.id,
            "revision": updated.revision,
            "report_id": report.id,
            "summary": summary,
        }

    def _block(self, goal: Any, summary: str, blocker: str | None) -> dict[str, Any]:
        text = (blocker or "").strip()
        if not text:
            return tool_error(
                "invalid_arguments", "status='blocked' requires a blocker text"
            )
        streak = 0
        for previous in reversed(goal.blocker_history):
            if previous != text:
                break
            streak += 1
        if streak + 1 < BLOCKER_THRESHOLD:
            return tool_error(
                "blocker_not_established",
                f"blocker recorded {streak + 1} of {BLOCKER_THRESHOLD} times; "
                "try a different approach before giving up, and describe what "
                "would unblock you",
                retryable=True,
                blocker=text,
            )
        try:
            updated = self._goals.update_goal(
                goal.id,
                expected_revision=goal.revision,
                status="blocked",
                blocker=text,
            )
        except GoalConflictError as exc:
            return tool_error("goal_revision_mismatch", str(exc), retryable=True)
        return {
            "status": "blocked",
            "goal_id": updated.id,
            "revision": updated.revision,
            "report_id": None,
            "summary": summary,
        }
```

**证据落库的时机**：门禁判定的是 `replace(...)` 出来的候选副本，所以一次被拒绝的完成
尝试不会写任何东西；只有真的通过之后才调用 `set_criterion_evidence` 把证据记下来。
这也是 `test_complete_is_rejected_without_evidence` 能断言 "goal 状态不变" 的原因。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_tools.py tests/test_goal_store.py -q`
Expected: PASS（14 + 15 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/goals/tools.py \
        src/langharness_core/plugins/goals/sqlite.py tests/test_goal_tools.py \
        tests/test_goal_store.py
git commit -m "feat: expose get_goal and gate finish_goal on host verification"
```

---

### Task 6: Catalog 装配与作用域 e2e

**Files:**
- Modify: `src/langharness_core/plugin.py`
- Modify: `tests/test_imports.py`
- Test: `tests/test_goal_scope.py`（新建）

**Interfaces:**
- Consumes: Task 3/4/5 的组件。
- Produces：`AGENT_PLUGIN_CATALOG["goal"]`、`["verifier"]`、`["goal-tools"]` 三个条目与对应的 `builtin_package()` template contribution。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_goal_scope.py`：

```python
"""Two agents must not share a goal or a verifier."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

from pathlib import Path
from typing import Any

from langharness_core.contracts import SPEC_GOAL, SPEC_TOOL, SPEC_VERIFIER
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id

DESCRIPTION = (
    "Goal scope test plugin. Implements a test specification. Properties vary "
    "per instance. Requires a restart for property changes. Uninstall when "
    "tests finish."
)


def descriptor(
    name: str, module: str, factory: str, specification: str
) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=DESCRIPTION,
    )


BUNDLES = (
    descriptor(
        "goal",
        "langharness_core.plugins.goals.sqlite",
        "sqlite-goal-plugin-factory",
        SPEC_GOAL,
    ),
    descriptor(
        "verifier",
        "langharness_core.plugins.verifier.project",
        "project-verifier-plugin-factory",
        SPEC_VERIFIER,
    ),
    descriptor(
        "goal-tools",
        "langharness_core.plugins.goals.tools",
        "goal-tools-plugin-factory",
        SPEC_TOOL,
    ),
)


def start_agent(manager: PluginManager, agent_id: str, tmp_path: Path) -> None:
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    manag = {
        "sqlite-goal-plugin-factory": {
            "plugin.goal.path": str(tmp_path / agent_id / "goals.sqlite3")
        },
        "project-verifier-plugin-factory": {},
        "goal-tools-plugin-factory": {},
    }
    for factory, properties in manag.items():
        definition = next(item for item in BUNDLES if item.factory == factory)
        manager.create_instance(
            definition.factory, definition.module, scope, properties=properties
        )


def test_goals_are_per_agent(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        start_agent(manager, "alpha", tmp_path)
        start_agent(manager, "beta", tmp_path)

        alpha_goals: list[Any] = manager.find_services(
            SPEC_GOAL, filter="(plugin.scope_id=agent:alpha)"
        )
        beta_goals: list[Any] = manager.find_services(
            SPEC_GOAL, filter="(plugin.scope_id=agent:beta)"
        )
        assert len(alpha_goals) == 1
        assert len(beta_goals) == 1

        alpha_goals[0].create_goal("alpha objective", [{"id": "c1", "text": "t"}])
        assert alpha_goals[0].current_goal().objective == "alpha objective"
        assert beta_goals[0].current_goal() is None
    finally:
        manager.stop()


def test_the_goal_tool_gets_its_own_scopes_services(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for item in BUNDLES:
            manager.install_descriptor(item)
        start_agent(manager, "alpha", tmp_path)
        goal = manager.find_service(
            SPEC_GOAL, filter="(plugin.scope_id=agent:alpha)"
        )
        goal.create_goal("alpha objective", [{"id": "c1", "text": "t"}])
        tool_providers: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:alpha)"
        )
        assert len(tool_providers) == 1
        assert tool_providers[0]._goals is goal
        names = {item.name for item in tool_providers[0].get_tools()}
        assert names == {"get_goal", "finish_goal"}
    finally:
        manager.stop()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_goal_scope.py -q`
Expected: FAIL — 未安装的描述符无法 `create_instance`。

- [ ] **Step 3: 写实现**

在 `src/langharness_core/plugin.py` 中：

1. 从 `langharness_core.contracts` 导入 `SPEC_GOAL`、`SPEC_VERIFIER`。
2. `AGENT_PLUGIN_CATALOG` 追加：

```python
    "goal": (
        "langharness_core.plugins.goals.sqlite",
        "sqlite-goal-plugin-factory",
        SPEC_GOAL,
    ),
    "verifier": (
        "langharness_core.plugins.verifier.project",
        "project-verifier-plugin-factory",
        SPEC_VERIFIER,
    ),
    "goal-tools": (
        "langharness_core.plugins.goals.tools",
        "goal-tools-plugin-factory",
        SPEC_TOOL,
    ),
```

3. `AGENT_PLUGIN_DESCRIPTIONS` 追加三条同句式说明。
4. `builtin_package()` 追加三个 `agent` template contribution。
5. `tests/test_imports.py` 的 `PUBLIC_MODULES` 追加：

```python
    "langharness_core.plugins.goals.model",
    "langharness_core.plugins.goals.gate",
    "langharness_core.plugins.goals.sqlite",
    "langharness_core.plugins.goals.tools",
    "langharness_core.plugins.verifier.project",
```

（若 Task 1 未单独建 `goals/model.py`，则去掉该行。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_goal_scope.py tests/test_imports.py -q`
Expected: PASS

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过，覆盖率 ≥95%。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugin.py tests/test_imports.py tests/test_goal_scope.py
git commit -m "feat: wire goals and the verifier into the agent catalog"
```

---

## 完成后的验收

计划 3a 落地后应能观测到：

1. `finish_goal(status="complete")` 在缺报告、报告失败、报告过期、验收项无证据、
   有未决活动、或 workspace 从未改动时**一律被拒**，且每次拒绝都列出全部原因
   （Task 2 的 11 个门禁测试）；
2. 一次被拒的完成尝试不会改变 goal 状态与 revision（Task 5）；
3. 验证报告带 `mutation_generation`，验证器自己跑门禁产生的构建产物不会让报告立刻过期
   （Task 4 的 `test_a_command_that_writes_does_not_advance_the_generation`）；
4. 同一 blocker 未连续出现 3 次前不允许转 `blocked`（Task 5）；
5. 两个 agent 的 goal 与 verifier 互不可见（Task 6）。

计划 3b（run controller 自动续跑、`ask_user`、`awaiting_user`、新流事件、`code` preset、
三协议 e2e、真实模型临时仓库验收）与计划 2b（`exec_command` 与 job 控制）的骨架见
`docs/plans/2026-09-22-code-agent-phase1-roadmap.md`。
