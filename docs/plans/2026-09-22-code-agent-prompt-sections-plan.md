# Code Agent Phase 1 / 计划 1：有序 Prompt Sections 与仓库指令 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent loop 在每个 model step 用有序、具名、带字节预算的 `PromptSection` 组装 system prompt，并按 workspace→目标文件路径逐层注入 `AGENTS.md`，指令文件内容变化后无需重启即可失效。

**Architecture:** 新增 `agent.plugin.prompt_section` 与 `agent.plugin.workspace` 两个服务规格。loop 通过 `@Requires(aggregate=True)` 聚合 section providers，在 `create_agent` 的 middleware 链末端挂一个自实现的 `PromptMiddleware`；该 middleware 同时实现 `wrap_model_call` 与 `awrap_model_call`，在每次模型请求前从 `request.state["messages"]` 重新计算 prompt。现有 `SystemPromptProvider` 由 adapter 包成 order=1000 的 legacy section，行为逐字节保持不变。

**Tech Stack:** Python 3.13、Pelix/iPOPO、LangChain 1.4（`langchain.agents.middleware.AgentMiddleware`）、pytest。

## Global Constraints

- 质量门槛（AGENTS.md）：每个 commit 必须过 `make check`（Ruff → mypy → Pyright → clean-process import 检查 → pytest），单元覆盖率 ≥95%。
- TDD：先写失败测试，再写最小实现；每个任务独立 commit。
- 生产代码 mypy strict、Pyright standard 不允许全局放宽；测试文件头已有 `# mypy: ignore-errors` 属既有豁免，不新增豁免。
- 插件间通信只走 Pelix service spec（Protocol）；`@Provides`/`@Requires` 声明 Protocol 类，不写裸 `SPEC_*` 字符串；禁止在 Protocol 类体内写 `__SPECIFICATION__`。
- `PluginDescriptor` 只在 `langharness_core/plugin.py` 构造；组件实现在 `plugins/<topic>/<name>.py`。
- 生产插件禁止 `@Instantiate`；实例由 `PluginManager` / `AgentDirectoryPlugin` 创建。
- 消费方在 `BindField` 回调里用 `ContractGuard` 守卫注入字段。
- 文档（docs/、README）用中文，代码/命令用英文；commit message 用 `feat:` / `test:` / `docs:`。
- 设计文档：`docs/designs/2026-09-22-code-agent-design.md`（第 5 节与第 9 节是本计划的直接依据）。

## 已验证的技术前提

在写本计划前已在本仓库的 `.venv`（langchain 1.4.0 / langchain_core 1.6.2）实测确认：

1. `create_agent(model, system_prompt=None, middleware=[M])` 配合自定义 middleware 可以完全接管 system message；
2. middleware 同时实现 `wrap_model_call` 与 `awrap_model_call` 时，`ainvoke` 与 `astream` 两条路径都会走到它 —— 只实现同步版本会在 `astream` 下抛
   `"Asynchronous implementation of awrap_model_call is not available"`，本仓库的 API server 走的正是异步路径；
3. `request.state["messages"]` 在每个 model step 递增（第 1 步只有用户消息，第 2 步已含 `AIMessage.tool_calls` 与 `ToolMessage`），因此"工具触及更深目录后下一步注入该目录指令"可以只靠 middleware 实现，不需要重建 graph；
4. `class PromptMiddleware(AgentMiddleware):` 裸继承即可通过 mypy strict 与 Pyright，无需 `# type: ignore`。

## 本计划覆盖的 order 区段

设计第 5.1 节的 section 顺序表按阶段落地，本计划只占其中前两段，后续计划不得改动
已占用的区段：

| order | Section | 本计划 | 归属 |
|---:|---|---|---|
| 0 | `identity` | ✅ Task 8 | 计划 1 |
| 100 | `safety` | ✅ Task 8 | 计划 1 |
| 200 | `workflow` | ✅ Task 8 | 计划 1 |
| 300 | `completion` | ✅ Task 8 | 计划 1 |
| 500 | `repository-instructions` | ✅ Task 6 | 计划 1 |
| 599 | `repository-instructions-omitted` | ✅ Task 6 | 计划 1 |
| 700 | `mode-policy` | ❌ | 不在 Phase 1 范围内（设计第 11 节的 Phase 1 清单未包含 plan/default 模式切换），留给后续单独设计 |
| 800 | `ptc-only` | ❌ | Phase 2 |
| 1000 | `legacy:*` | ✅ Task 4 | 计划 1（迁移期适配，之后弃用） |
| 1001+ | `tool-guidance:*` | ❌ | 计划 2 —— code 工具与它们的指导文本一起写；**必须从 1001 起**，不要占用 1000 |
| 5000 | `tools-sdk` | ❌ | Phase 2 |
| 9000 | `goal-round` / `goal-wrapup` | ❌ | 计划 3 |

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `src/langharness_core/common/text.py` | 跨 topic 复用的文本工具：`digest`（内容 hash）、`truncate_bytes`（按行边界截断并报告省略字节数） |
| `src/langharness_core/plugins/workspace/__init__.py` | 包标记 |
| `src/langharness_core/plugins/workspace/root.py` | `WorkspaceRootPlugin`：固定并暴露 workspace realpath |
| `src/langharness_core/plugins/prompts/__init__.py` | 包标记 |
| `src/langharness_core/plugins/prompts/sections.py` | 纯逻辑：排序、同名去重、字节预算、拼装、trace |
| `src/langharness_core/plugins/prompts/legacy.py` | 把 `SystemPromptProvider` 适配成 order=1000 的 section |
| `src/langharness_core/plugins/prompts/context.py` | 从消息历史提取被触及的 workspace 相对路径，构造 `PromptContext` |
| `src/langharness_core/plugins/prompts/middleware.py` | `PromptMiddleware`（同步 + 异步） |
| `src/langharness_core/plugins/prompts/code_agent.py` | `CodeAgentPromptPlugin`：加载随包发布的 code agent 静态 prompt |
| `src/langharness_core/plugins/instructions/__init__.py` | 包标记 |
| `src/langharness_core/plugins/instructions/resolver.py` | 纯逻辑：指令文件发现、读取缓存、字节预算 |
| `src/langharness_core/plugins/instructions/repository.py` | `RepositoryInstructionsPlugin`：把 selection 转成 prompt sections |
| `src/langharness_core/prompts/code_agent/{identity,safety,workflow,completion}.md` | code agent 静态 prompt 文本 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/langharness_core/contracts.py` | 新增 `SPEC_PROMPT_SECTION`、`SPEC_WORKSPACE`、`PromptSection`、`PromptContext`、`PromptSectionProvider`、`WorkspaceProvider` |
| `src/langharness_core/plugin.py` | catalog 增加 `workspace` / `prompt` / `repository-instructions`；`AGENT_LOOP_FIELDS` 增加两个字段；`builtin_package()` 增加三个 template contribution |
| `src/langharness_core/plugins/loop/agent_loop.py` | 新增两个依赖字段，用 `_prompt_for_request` + `PromptMiddleware` 取代 `_collect_system_prompt()` |
| `pyproject.toml` | 为 `langharness_core` 声明 `prompts/**/*.md` package-data |
| `tests/test_imports.py` | `PUBLIC_MODULES` 增加新模块 |
| `tests/test_system_prompt.py` | 改写三个直接调用 `_collect_system_prompt()` 的测试 |
| `tests/test_agent_directory.py` | 更新 `requires.filters` 断言 |
| `tests/test_packaging.py` | 断言 `prompts` package-data 已声明 |

---

### Task 1: Prompt section 契约与值类型

**Files:**
- Modify: `src/langharness_core/contracts.py`
- Test: `tests/test_prompt_contracts.py`（新建）

**Interfaces:**
- Consumes: `langharness_plugin.validation.service_contract`（已有）。
- Produces（后续所有任务复用）：
  - `SPEC_PROMPT_SECTION = "agent.plugin.prompt_section"`
  - `PromptSection(name: str, order: int, text: str, source: str, max_bytes: int | None = None)` —— frozen dataclass，`slots=True`
  - `PromptContext(workspace: str, touched_paths: tuple[str, ...] = (), model: str | None = None)` —— frozen dataclass，`slots=True`
  - `PromptSectionProvider` Protocol：`get_prompt_sections(context: PromptContext) -> list[PromptSection]`、`get_plugin_info() -> dict[str, str]`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_prompt_contracts.py`：

```python
"""Contract tests for the ordered prompt-section service."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_PROMPT_SECTION,
    PromptContext,
    PromptSection,
    PromptSectionProvider,
)
from langharness_plugin.validation import contract_for, validate


class ConformingProvider:
    """Smallest service that satisfies the prompt-section contract."""

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        return [
            PromptSection(
                name="identity", order=0, text=context.workspace, source="test"
            )
        ]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": "conforming", "version": "1.0.0"}


class MissingPluginInfo:
    """Violates the contract by omitting ``get_plugin_info``."""

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        return []


def test_specification_is_pinned_to_the_protocol() -> None:
    assert contract_for(SPEC_PROMPT_SECTION) is PromptSectionProvider
    assert SPEC_PROMPT_SECTION in ALL_PLUGIN_SPECS


def test_pin_does_not_pollute_protocol_members() -> None:
    assert "__SPECIFICATION__" not in getattr(
        PromptSectionProvider, "__protocol_attrs__"
    )


def test_conforming_provider_has_no_violations() -> None:
    assert validate(ConformingProvider(), PromptSectionProvider) == ()


def test_missing_method_is_reported() -> None:
    violations = validate(MissingPluginInfo(), PromptSectionProvider)
    assert [item.method for item in violations] == ["get_plugin_info"]
    assert violations[0].code == "MISSING_METHOD"


def test_isinstance_works_against_the_protocol() -> None:
    assert isinstance(ConformingProvider(), PromptSectionProvider)


def test_prompt_section_defaults_to_no_per_section_budget() -> None:
    section = PromptSection(name="n", order=1, text="t", source="s")
    assert section.max_bytes is None
    assert hash(section) == hash(PromptSection(name="n", order=1, text="t", source="s"))


def test_prompt_context_defaults_to_no_touched_paths() -> None:
    context = PromptContext(workspace="/w")
    assert context.touched_paths == ()
    assert context.model is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompt_contracts.py -q`
Expected: FAIL — `ImportError: cannot import name 'SPEC_PROMPT_SECTION'`

- [ ] **Step 3: 写实现**

在 `src/langharness_core/contracts.py` 顶部加上 dataclass 导入：

```python
from dataclasses import dataclass
```

在 `SPEC_AGENT_SERVER = "agent.server"` 之后加两个规格常量：

```python
SPEC_PROMPT_SECTION = "agent.plugin.prompt_section"
SPEC_WORKSPACE = "agent.plugin.workspace"
```

把 `SPEC_PROMPT_SECTION` 与 `SPEC_WORKSPACE` 追加进 `ALL_PLUGIN_SPECS` 元组（放在
`SPEC_TRANSFORMERS` 之后、`SPEC_AGENT_LOOP` 之前，与声明顺序保持一致）。

在 `ALL_PLUGIN_SPECS` 之后、`LLMProvider` 之前插入值类型：

```python
@dataclass(frozen=True, slots=True)
class PromptSection:
    """One ordered, named contribution to an agent's system prompt.

    ``order`` decides placement (smaller first); ``source`` is free-form
    provenance recorded in the per-request trace; ``max_bytes`` caps this
    section alone, on top of the assembler's total budget.
    """

    name: str
    order: int
    text: str
    source: str
    max_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class PromptContext:
    """What a section provider may use to shape its contribution.

    ``touched_paths`` are workspace-relative POSIX paths the conversation's
    tool calls have already named, shallowest information the provider needs
    to decide how deep into a repository its instructions must reach.
    """

    workspace: str
    touched_paths: tuple[str, ...] = ()
    model: str | None = None
```

在 `SystemPromptProvider` 之后插入两个 Protocol：

```python
@service_contract(SPEC_PROMPT_SECTION)
@runtime_checkable
class PromptSectionProvider(Protocol):
    """Contract implemented by every ``agent.plugin.prompt_section`` service."""

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_WORKSPACE)
@runtime_checkable
class WorkspaceProvider(Protocol):
    """Contract implemented by every ``agent.plugin.workspace`` service."""

    def get_workspace(self) -> str: ...

    def get_plugin_info(self) -> dict[str, str]: ...
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_prompt_contracts.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。若有既有测试因为 `ALL_PLUGIN_SPECS` 变长而失败，按新长度更新该断言，不得删减新规格。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/contracts.py tests/test_prompt_contracts.py
git commit -m "feat: add prompt section and workspace service contracts"
```

---

### Task 2: Workspace root 契约实现

**Files:**
- Create: `src/langharness_core/plugins/workspace/__init__.py`
- Create: `src/langharness_core/plugins/workspace/root.py`
- Modify: `src/langharness_core/plugin.py`
- Test: `tests/test_workspace_root.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `WorkspaceProvider`、`SPEC_WORKSPACE`。
- Produces（Task 6、Task 7、计划 2 复用）：
  - 工厂名 `workspace-root-plugin-factory`，模块 `langharness_core.plugins.workspace.root`
  - 属性 `plugin.workspace.root_dir`（默认 `"."`）
  - `WorkspaceRootPlugin.get_workspace() -> str` 返回 `Path(root_dir).expanduser().resolve()` 的字符串形式
  - `AGENT_PLUGIN_CATALOG["workspace"] = ("langharness_core.plugins.workspace.root", "workspace-root-plugin-factory", SPEC_WORKSPACE)`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_workspace_root.py`：

```python
"""Tests for the workspace-root provider plugin."""
# mypy: ignore-errors

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import SPEC_WORKSPACE, WorkspaceProvider
from langharness_core.plugin import (
    AGENT_PLUGIN_CATALOG,
    agent_plugin_descriptor,
    builtin_package,
)
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_plugin.validation import validate


def test_workspace_is_the_resolved_root_dir(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    plugin = WorkspaceRootPlugin()
    plugin._root = str(tmp_path / "nested" / "..")
    assert plugin.get_workspace() == str(tmp_path.resolve())


def test_workspace_follows_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    plugin = WorkspaceRootPlugin()
    plugin._root = str(link)
    assert plugin.get_workspace() == str(real.resolve())


def test_workspace_defaults_to_the_process_directory() -> None:
    plugin = WorkspaceRootPlugin()
    assert plugin.get_workspace() == str(Path(".").expanduser().resolve())


def test_workspace_plugin_conforms_to_its_contract() -> None:
    assert validate(WorkspaceRootPlugin(), WorkspaceProvider) == ()


def test_catalog_entry_points_at_the_component() -> None:
    assert AGENT_PLUGIN_CATALOG["workspace"] == (
        "langharness_core.plugins.workspace.root",
        "workspace-root-plugin-factory",
        SPEC_WORKSPACE,
    )
    descriptor = agent_plugin_descriptor("workspace")
    assert descriptor.specification == SPEC_WORKSPACE
    assert descriptor.description.strip()


def test_builtin_package_ships_the_workspace_template() -> None:
    contributions = {item.id: item for item in builtin_package().contributions}
    assert "workspace-template" in contributions
    assert contributions["workspace-template"].target == "agent"


def test_plugin_info_reports_the_component_identity() -> None:
    assert WorkspaceRootPlugin().get_plugin_info() == {
        "name": "workspace-root",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_workspace_root.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.workspace'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/workspace/__init__.py`：

```python
"""Workspace boundary plugins."""
```

`src/langharness_core/plugins/workspace/root.py`：

```python
"""Workspace-root provider: the realpath every path-taking plugin resolves against."""

from __future__ import annotations

from pathlib import Path

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import WorkspaceProvider


@ComponentFactory("workspace-root-plugin-factory")
@Provides(WorkspaceProvider)
@Property("_plugin_name", "plugin.name", "workspace-root")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_root", "plugin.workspace.root_dir", ".")
class WorkspaceRootPlugin:
    """Pins the workspace every path-taking tool and prompt section resolves against.

    The path is resolved with symlinks followed and is never cached: a property
    update must take effect on the next read, and ``Path.resolve`` on one
    directory is cheap enough to run once per model step.
    """

    def __init__(self) -> None:
        self._plugin_name = "workspace-root"
        self._plugin_version = "1.0.0"
        self._root = "."

    def get_workspace(self) -> str:
        return str(Path(self._root).expanduser().resolve())

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
```

在 `src/langharness_core/plugin.py` 中：

1. 从 `langharness_core.contracts` 的导入列表里加上 `SPEC_WORKSPACE`（保持字母序，落在 `SPEC_TOOL` 与 `SPEC_TRANSFORMERS` 之间）。

2. 在 `AGENT_PLUGIN_CATALOG` 的 `"name"` 条目之后加入：

```python
    "workspace": (
        "langharness_core.plugins.workspace.root",
        "workspace-root-plugin-factory",
        SPEC_WORKSPACE,
    ),
```

3. 在 `AGENT_PLUGIN_DESCRIPTIONS` 中加入：

```python
    "workspace": (
        "Pins the workspace realpath shared by prompt sections, file tools and "
        "command execution for one agent scope. Implements agent.plugin.workspace. "
        "Properties: plugin.workspace.root_dir, plugin.agent_id. Requires a "
        "restart for property changes. Uninstall when the agent is removed."
    ),
```

4. 在 `builtin_package()` 的 contributions 里，`name-template` 之后加入：

```python
            PluginContribution(
                "workspace-template", "agent", agent_plugin_template_descriptor("workspace")
            ),
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_workspace_root.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。`tests/test_core_descriptors.py::test_builtin_package_*` 一类枚举 contributions 的测试可能需要同步更新。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/workspace tests/test_workspace_root.py \
        src/langharness_core/plugin.py
git commit -m "feat: add workspace root provider plugin"
```

---

### Task 3: 文本工具与 section 组装器

**Files:**
- Create: `src/langharness_core/common/text.py`
- Create: `src/langharness_core/plugins/prompts/__init__.py`
- Create: `src/langharness_core/plugins/prompts/sections.py`
- Test: `tests/test_prompt_sections.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `PromptSection`。
- Produces（Task 4、Task 6、Task 7 复用）：
  - `langharness_core.common.text.digest(text: str) -> str` —— 返回 `"sha256:<hex>"`
  - `langharness_core.common.text.truncate_bytes(text: str, max_bytes: int) -> tuple[str, int]` —— 返回 `(截断后的文本, 被省略的字节数)`；结果长度不超过 `max_bytes`
  - `sections.DEFAULT_TOTAL_BUDGET = 65_536`
  - `sections.SectionTrace(name, order, source, bytes, sha256, omitted_bytes=0, shadowed=False, dropped=False)`
  - `sections.AssembledPrompt(text: str, traces: tuple[SectionTrace, ...])`，带 `empty` property
  - `sections.assemble_sections(sections: Iterable[PromptSection], *, total_budget: int = DEFAULT_TOTAL_BUDGET) -> AssembledPrompt`

**字节预算的精确语义**（测试与实现都按此执行）：
- 排序键是 `(order, name, source)`，因此同一个 `order` 下不同 provider 的输出顺序仍然确定；
- 同名 section 只保留排序后的第一个，其余记入 trace（`shadowed=True`）并打一条 WARNING；
- `SectionTrace.sha256` 永远是 **provider 原始文本** 的摘要 —— 它回答"这个 section 的来源内容是否变过"，与预算无关；
- `bytes` / `omitted_bytes` 描述实际发出的内容；
- 总预算是软上限：被丢弃的 section 名会附在末尾的提示行里，该提示行不计入预算。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_prompt_sections.py`：

```python
"""Unit tests for deterministic prompt-section assembly."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from langharness_core.common.text import digest, truncate_bytes
from langharness_core.contracts import PromptSection
from langharness_core.plugins.prompts.sections import (
    DEFAULT_TOTAL_BUDGET,
    AssembledPrompt,
    assemble_sections,
)


def section(
    name: str, order: int, text: str, source: str = "s", **kwargs: Any
) -> PromptSection:
    return PromptSection(name=name, order=order, text=text, source=source, **kwargs)


def test_digest_is_a_prefixed_sha256() -> None:
    assert digest("abc") == (
        "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_truncate_leaves_short_text_untouched() -> None:
    assert truncate_bytes("abc", 10) == ("abc", 0)


def test_truncate_cuts_on_a_line_boundary_and_reports_omitted_bytes() -> None:
    text = "line one\n" * 10  # 90 bytes
    result, omitted = truncate_bytes(text, 45)
    assert result == "line one\n\n\n[truncated: 81 bytes omitted]"
    assert len(result.encode("utf-8")) <= 45
    assert omitted == 81


def test_truncate_keeps_utf8_valid_when_a_character_straddles_the_cut() -> None:
    text = "中文内容" * 20  # 240 bytes, 3 bytes per character
    result, omitted = truncate_bytes(text, 63)
    assert result.startswith("中文内容")
    assert len(result.encode("utf-8")) <= 63
    assert omitted == 210


def test_truncate_returns_empty_when_the_budget_cannot_hold_the_notice() -> None:
    assert truncate_bytes("abcdef", 3) == ("", 6)


def test_sections_are_ordered_by_order_then_name_then_source() -> None:
    assembled = assemble_sections(
        [section("b", 200, "B"), section("a", 100, "A"), section("c", 100, "C")]
    )
    assert assembled.text == "A\n\nC\n\nB"
    assert [trace.name for trace in assembled.traces] == ["a", "c", "b"]


def test_duplicate_names_keep_the_first_and_record_the_shadowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="langharness.prompt"):
        assembled = assemble_sections(
            [section("same", 10, "first", source="a"), section("same", 10, "second", source="b")]
        )
    assert assembled.text == "first"
    assert [(trace.name, trace.shadowed) for trace in assembled.traces] == [
        ("same", False),
        ("same", True),
    ]
    assert "same" in caplog.text


def test_per_section_budget_truncates_only_that_section() -> None:
    assembled = assemble_sections(
        [section("a", 0, "x" * 100, max_bytes=40), section("b", 1, "keep me")]
    )
    assert assembled.text.endswith("keep me")
    assert assembled.traces[0].omitted_bytes > 0
    assert assembled.traces[1].bytes == len("keep me")


def test_trace_hash_is_of_the_original_text_not_the_truncated_one() -> None:
    assembled = assemble_sections([section("a", 0, "x" * 100, max_bytes=40)])
    assert assembled.traces[0].sha256 == digest("x" * 100)
    assert assembled.traces[0].bytes < 100


def test_total_budget_drops_sections_that_do_not_fit() -> None:
    assembled = assemble_sections(
        [section("a", 0, "x" * 40), section("b", 1, "y" * 40)], total_budget=50
    )
    assert assembled.traces[0].dropped is False
    assert assembled.traces[1].dropped is True
    assert assembled.traces[1].bytes == 0
    assert assembled.text.startswith("x" * 40)
    assert "dropped sections: b" in assembled.text


def test_the_drop_notice_is_appended_outside_the_budget() -> None:
    assembled = assemble_sections([section("only", 0, "z" * 80)], total_budget=20)
    assert assembled.text == "[prompt budget exhausted: dropped sections: only]"
    assert len(assembled.text.encode("utf-8")) > 20


def test_empty_sections_are_skipped_without_a_drop_notice() -> None:
    assembled = assemble_sections([section("empty", 0, ""), section("b", 1, "kept")])
    assert assembled.text == "kept"
    assert [trace.name for trace in assembled.traces] == ["b"]


def test_empty_input_produces_an_empty_prompt() -> None:
    assembled = assemble_sections([])
    assert assembled == AssembledPrompt(text="", traces=())
    assert assembled.empty is True


def test_default_budget_is_64_kib() -> None:
    assert DEFAULT_TOTAL_BUDGET == 65_536
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompt_sections.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.common.text'`

- [ ] **Step 3: 写实现**

`src/langharness_core/common/text.py`：

```python
"""Text helpers shared by prompt assembly and instruction discovery."""

from __future__ import annotations

import hashlib

TRUNCATION_NOTICE = "\n\n[truncated: {omitted} bytes omitted]"


def digest(text: str) -> str:
    """Return a stable, prefixed content digest for traces and cache keys."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def truncate_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    """Cut ``text`` to at most ``max_bytes`` UTF-8 bytes.

    The cut prefers a line boundary and appends a notice stating how many bytes
    were dropped, so the returned text never exceeds ``max_bytes``.  A budget
    too small to hold the notice yields an empty string instead of an
    over-budget one; the caller still learns the full byte count from the
    second element of the tuple.

    Room is reserved against a worst-case notice rather than the final one:
    the digit count of ``omitted`` is not known until after the cut, and
    reserving the shorter form would let the notice push the result past
    ``max_bytes``.
    """
    encoded = text.encode("utf-8")
    total = len(encoded)
    if total <= max_bytes:
        return text, 0
    reserve = TRUNCATION_NOTICE.format(omitted="9" * len(str(total)))
    room = max_bytes - len(reserve.encode("utf-8"))
    if room <= 0:
        return "", total
    # A multi-byte character may straddle the cut; dropping it keeps the
    # result decodable.
    head = encoded[:room].decode("utf-8", errors="ignore")
    if "\n" in head:
        head = head[: head.rindex("\n") + 1]
    omitted = total - len(head.encode("utf-8"))
    return head + TRUNCATION_NOTICE.format(omitted=omitted), omitted
```

`src/langharness_core/plugins/prompts/__init__.py`：

```python
"""System-prompt assembly plugins."""
```

`src/langharness_core/plugins/prompts/sections.py`：

```python
"""Deterministic ordering, budgeting and joining of prompt sections."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from langharness_core.common.text import digest, truncate_bytes
from langharness_core.contracts import PromptSection

LOGGER = logging.getLogger("langharness.prompt")

DEFAULT_TOTAL_BUDGET = 65_536
SEPARATOR = "\n\n"
SEPARATOR_BYTES = len(SEPARATOR.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class SectionTrace:
    """What one section contributed to one assembled prompt.

    ``sha256`` digests the text the provider produced, so it only changes when
    that provider's content changes; ``bytes`` and ``omitted_bytes`` describe
    what actually reached the model after budgeting.
    """

    name: str
    order: int
    source: str
    bytes: int
    sha256: str
    omitted_bytes: int = 0
    shadowed: bool = False
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class AssembledPrompt:
    """The system prompt for one model step, plus its provenance."""

    text: str
    traces: tuple[SectionTrace, ...]

    @property
    def empty(self) -> bool:
        return not self.text


def _partition(
    sections: Iterable[PromptSection],
) -> tuple[list[PromptSection], list[PromptSection]]:
    """Return (kept, shadowed) after sorting and deduplicating by name."""
    ordered = sorted(
        sections, key=lambda item: (item.order, item.name, item.source)
    )
    kept: list[PromptSection] = []
    shadowed: list[PromptSection] = []
    seen: set[str] = set()
    for item in ordered:
        if item.name in seen:
            shadowed.append(item)
            LOGGER.warning(
                "prompt section %r from %s is shadowed by an earlier section "
                "with the same name",
                item.name,
                item.source,
            )
            continue
        seen.add(item.name)
        kept.append(item)
    return kept, shadowed


def assemble_sections(
    sections: Iterable[PromptSection],
    *,
    total_budget: int = DEFAULT_TOTAL_BUDGET,
) -> AssembledPrompt:
    """Order, deduplicate, budget and join sections into one system prompt."""
    kept, shadowed = _partition(sections)
    parts: list[str] = []
    traces: list[SectionTrace] = []
    dropped: list[str] = []
    used = 0
    for item in kept:
        if not item.text:
            # An empty contribution is not a budget casualty; name it in the
            # drop notice and the notice becomes a lie about why it is there.
            continue
        separator = SEPARATOR_BYTES if parts else 0
        allowance = total_budget - used - separator
        if item.max_bytes is not None:
            allowance = min(allowance, item.max_bytes)
        if allowance > 0:
            text, omitted = truncate_bytes(item.text, allowance)
        else:
            text, omitted = "", len(item.text.encode("utf-8"))
        if not text:
            dropped.append(item.name)
            traces.append(
                SectionTrace(
                    name=item.name,
                    order=item.order,
                    source=item.source,
                    bytes=0,
                    sha256=digest(item.text),
                    omitted_bytes=len(item.text.encode("utf-8")),
                    dropped=True,
                )
            )
            continue
        parts.append(text)
        used += separator + len(text.encode("utf-8"))
        traces.append(
            SectionTrace(
                name=item.name,
                order=item.order,
                source=item.source,
                bytes=len(text.encode("utf-8")),
                sha256=digest(item.text),
                omitted_bytes=omitted,
            )
        )
    traces.extend(
        SectionTrace(
            name=item.name,
            order=item.order,
            source=item.source,
            bytes=0,
            sha256=digest(item.text),
            omitted_bytes=len(item.text.encode("utf-8")),
            shadowed=True,
        )
        for item in shadowed
    )
    text = SEPARATOR.join(parts)
    if dropped:
        note = f"[prompt budget exhausted: dropped sections: {', '.join(dropped)}]"
        text = f"{text}{SEPARATOR}{note}" if text else note
    return AssembledPrompt(text=text, traces=tuple(traces))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_prompt_sections.py -q`
Expected: PASS（14 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。Ruff 的 `E501` 未启用，但长行仍需手工折行以保持一致风格。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/common/text.py src/langharness_core/plugins/prompts \
        tests/test_prompt_sections.py
git commit -m "feat: assemble ordered prompt sections with byte budgets"
```

---

### Task 4: Legacy SystemPromptProvider 适配

**Files:**
- Create: `src/langharness_core/plugins/prompts/legacy.py`
- Test: `tests/test_prompt_legacy.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `PromptSection`、`PromptSectionProvider` 协议（用于取 `get_plugin_info`）。
- Produces（Task 7 复用）：
  - `legacy.LEGACY_ORDER = 1000`
  - `legacy.legacy_sections(providers: Iterable[Any]) -> list[PromptSection]`

**为什么合并成一个 section：** 组装器用 `"\n\n"` 连接 section，而现行为是
`"\n".join(parts)`。如果每个 legacy provider 各占一个 section，只装了 legacy
provider 的 agent 会平白多出空行 —— 这是对既有 agent 的可观测行为改动。因此
adapter 自己用 `"\n"` 把它们拼成**一个** section，组装器再拼接时就不会引入新空白，
改造前后逐字节一致。

代价是 legacy 内容只留一条 trace，无法分辨是哪个 provider 变了。这是迁移期的临时
状态：插件改用 `PromptSectionProvider` 后就会得到自己的具名 section。`source` 字段
记录所有参与拼接的 provider 名，至少能回答"这条内容由谁贡献"。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_prompt_legacy.py`：

```python
"""Tests for the legacy SystemPromptProvider adapter."""

from __future__ import annotations

from dataclasses import dataclass

from langharness_core.plugins.prompts.legacy import LEGACY_ORDER, legacy_sections
from langharness_core.plugins.prompts.sections import assemble_sections


@dataclass
class FakePrompt:
    text: str
    name: str = "system-prompt-plugin"

    def get_system_prompt(self) -> str:
        return self.text

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self.name, "version": "1.0.0"}


def test_legacy_sections_collapse_into_one_ordered_section() -> None:
    sections = legacy_sections([FakePrompt("You are A."), FakePrompt("You are B.")])
    assert len(sections) == 1
    assert sections[0].name == "legacy"
    assert sections[0].order == LEGACY_ORDER


def test_joined_output_matches_the_old_string_concatenation() -> None:
    providers = [FakePrompt("You are A."), FakePrompt("You are B.")]
    assembled = assemble_sections(legacy_sections(providers))
    assert assembled.text == "You are A.\nYou are B."


def test_a_single_legacy_provider_is_passed_through_unchanged() -> None:
    assembled = assemble_sections(legacy_sections([FakePrompt("You are A.")]))
    assert assembled.text == "You are A."


def test_empty_providers_are_skipped() -> None:
    sections = legacy_sections([FakePrompt(""), FakePrompt("You are B.")])
    assert sections[0].text == "You are B."


def test_source_lists_every_contributing_provider() -> None:
    sections = legacy_sections(
        [FakePrompt("x", name="mine"), FakePrompt("y", name="theirs")]
    )
    assert sections[0].source == "mine@1.0.0, theirs@1.0.0"


def test_no_providers_yields_no_sections() -> None:
    assert legacy_sections([]) == []
    assert legacy_sections(None) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompt_legacy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.prompts.legacy'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/prompts/legacy.py`：

```python
"""Adapter that keeps ``SystemPromptProvider`` plugins working.

Legacy providers are joined into a single section at order 1000 -- after the
authored persona sections but before tool guidance.  They are joined with a
single newline, which is exactly what the pre-assembler loop did, so an agent
whose only prompt source is a legacy provider produces a byte-identical system
prompt.  Giving each provider its own section instead would insert the
assembler's blank-line separator and change that prompt.

``source`` lists every contributing provider so a trace still answers "who put
this text there"; per-provider traces return once a plugin migrates to
``PromptSectionProvider``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langharness_core.contracts import PromptSection

LEGACY_ORDER = 1000
LEGACY_JOIN = "\n"


def legacy_sections(providers: Iterable[Any] | None) -> list[PromptSection]:
    """Merge every legacy provider's string into one ordered section."""
    parts: list[str] = []
    sources: list[str] = []
    for provider in providers or ():
        text = provider.get_system_prompt()
        if not text:
            continue
        info = provider.get_plugin_info()
        name = str(info.get("name") or "system-prompt")
        version = str(info.get("version") or "")
        parts.append(text)
        sources.append(f"{name}@{version}" if version else name)
    if not parts:
        return []
    return [
        PromptSection(
            name="legacy",
            order=LEGACY_ORDER,
            text=LEGACY_JOIN.join(parts),
            source=", ".join(sources),
        )
    ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_prompt_legacy.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/prompts/legacy.py tests/test_prompt_legacy.py
git commit -m "feat: adapt legacy system prompts into ordered sections"
```

---

### Task 5: Prompt context 与被触及路径提取

**Files:**
- Create: `src/langharness_core/plugins/prompts/context.py`
- Test: `tests/test_prompt_context.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `PromptContext`。
- Produces（Task 6、Task 7 复用）：
  - `context.PATH_ARG_KEYS: tuple[str, ...] = ("path", "file_path", "dir_path", "cwd", "notebook_path")`
  - `context.PATCH_ARG_KEY = "patch"`
  - `context.PATCH_PATH_MARKERS: tuple[str, ...] = ("*** Update File:", "*** Add File:", "*** Delete File:", "*** Move to:")`
  - `context.touched_paths_from_messages(messages: Iterable[Any] | None, workspace: str) -> tuple[str, ...]`
  - `context.build_prompt_context(*, workspace: str, messages: Iterable[Any] | None, model: str | None) -> PromptContext`
  - `context.model_display_name(model: Any) -> str | None`

**契约（计划 2 必须沿用）：** `apply_patch` 工具使用
`*** Update File: <path>` / `*** Add File: <path>` / `*** Delete File: <path>` /
`*** Move to: <path>` 作为补丁头行，本任务按同一组标记解析目标路径。

**不变量：** `touched_paths_from_messages` 必须对任意输入都不抛异常，并且只返回
workspace 之内的 workspace-relative POSIX 路径；workspace 之外的路径（含通过
symlink 逃逸的路径）一律丢弃，不做任何提示。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_prompt_context.py`：

```python
"""Tests for prompt-context construction and touched-path extraction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from langharness_core.contracts import PromptContext
from langharness_core.plugins.prompts.context import (
    build_prompt_context,
    model_display_name,
    touched_paths_from_messages,
)


def call(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "c1", "type": "tool_call"}],
    )


def test_relative_paths_stay_relative(tmp_path: Path) -> None:
    messages = [call("read_file", file_path="src/deep/a.py")]
    assert touched_paths_from_messages(messages, str(tmp_path)) == ("src/deep/a.py",)


def test_dot_segments_are_normalised(tmp_path: Path) -> None:
    messages = [call("read_file", file_path="./src/./deep/../deep/a.py")]
    assert touched_paths_from_messages(messages, str(tmp_path)) == ("src/deep/a.py",)


def test_absolute_paths_inside_the_workspace_are_kept_relative(tmp_path: Path) -> None:
    target = tmp_path / "src" / "a.py"
    assert touched_paths_from_messages(
        [call("read_file", path=str(target))], str(tmp_path)
    ) == ("src/a.py",)


def test_paths_outside_the_workspace_are_dropped(tmp_path: Path) -> None:
    outside = tmp_path.parent / "elsewhere.py"
    assert touched_paths_from_messages(
        [call("read_file", path=str(outside))], str(tmp_path)
    ) == ()


def test_symlink_escaping_the_workspace_is_dropped(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape"
    link.symlink_to(outside, target_is_directory=True)
    assert touched_paths_from_messages(
        [call("read_file", path="escape/secret.py")], str(tmp_path)
    ) == ()


def test_duplicates_collapse_but_insertion_order_is_kept(tmp_path: Path) -> None:
    messages = [
        call("read_file", path="b.py"),
        call("read_file", path="a.py"),
        call("list_directory", dir_path="b.py"),
    ]
    assert touched_paths_from_messages(messages, str(tmp_path)) == ("b.py", "a.py")


def test_patch_markers_contribute_their_targets(tmp_path: Path) -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: src/a.py\n@@\n-old\n+new\n"
        "*** Add File: src/b.py\n+content\n"
        "*** Delete File: src/c.py\n"
        "*** Move to: src/d.py\n"
        "*** End Patch\n"
    )
    assert touched_paths_from_messages(
        [call("apply_patch", patch=patch)], str(tmp_path)
    ) == ("src/a.py", "src/b.py", "src/c.py", "src/d.py")


def test_the_workspace_root_itself_is_not_a_touched_path(tmp_path: Path) -> None:
    assert touched_paths_from_messages([call("list_directory", dir_path=".")], str(tmp_path)) == ()


def test_malformed_messages_never_raise(tmp_path: Path) -> None:
    messages = [
        None,
        AIMessage(content="hi"),
        # Not an AIMessage: a partially built message must not crash assembly.
        SimpleNamespace(tool_calls=[{"name": "x"}]),
        SimpleNamespace(tool_calls=[{"name": "x", "args": "not-a-dict"}]),
        call("read_file", path=123),
        call("read_file", path="\n"),
        call("apply_patch", patch=None),
    ]
    assert touched_paths_from_messages(messages, str(tmp_path)) == ()


def test_build_prompt_context_carries_workspace_paths_and_model(tmp_path: Path) -> None:
    context = build_prompt_context(
        workspace=str(tmp_path),
        messages=[call("read_file", path="a.py")],
        model="gpt-5",
    )
    assert context == PromptContext(
        workspace=str(tmp_path), touched_paths=("a.py",), model="gpt-5"
    )


def test_model_display_name_probes_the_common_attributes() -> None:
    class WithModelName:
        model_name = "gpt-5"

    class WithModel:
        model = "claude-opus-5"

    class WithNothing:
        model = None

    assert model_display_name(WithModelName()) == "gpt-5"
    assert model_display_name(WithModel()) == "claude-opus-5"
    assert model_display_name(WithNothing()) is None
    assert model_display_name(object()) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompt_context.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.prompts.context'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/prompts/context.py`：

```python
"""Extract the workspace paths a conversation has already touched."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langharness_core.contracts import PromptContext

PATH_ARG_KEYS: tuple[str, ...] = (
    "path",
    "file_path",
    "dir_path",
    "cwd",
    "notebook_path",
)
PATCH_ARG_KEY = "patch"
PATCH_PATH_MARKERS: tuple[str, ...] = (
    "*** Update File:",
    "*** Add File:",
    "*** Delete File:",
    "*** Move to:",
)
MODEL_NAME_ATTRIBUTES: tuple[str, ...] = ("model_name", "model", "model_id")


def _record(seen: dict[str, None], root: Path, value: str) -> None:
    """Keep workspace-relative path ``value``; silently drop everything else."""
    if not value or "\n" in value:
        return
    candidate = Path(value)
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = absolute.resolve()
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        # Either the path cannot be resolved or it leaves the workspace; a
        # traversal or a symlink escape must never widen the instruction scope.
        return
    if not relative.parts:
        return
    seen.setdefault(relative.as_posix(), None)


def touched_paths_from_messages(
    messages: Iterable[Any] | None, workspace: str
) -> tuple[str, ...]:
    """Return workspace-relative paths named by the conversation's tool calls.

    The result preserves first-seen order and never raises: prompt assembly
    runs on the request path, so a malformed message must degrade to "no
    touched paths" instead of failing the model call.
    """
    root = Path(workspace).resolve()
    seen: dict[str, None] = {}
    for message in messages or ():
        for tool_call in getattr(message, "tool_calls", None) or ():
            if not isinstance(tool_call, dict):
                continue
            args = tool_call.get("args")
            if not isinstance(args, dict):
                continue
            for key in PATH_ARG_KEYS:
                value = args.get(key)
                if isinstance(value, str):
                    _record(seen, root, value)
            patch = args.get(PATCH_ARG_KEY)
            if isinstance(patch, str):
                for line in patch.splitlines():
                    for marker in PATCH_PATH_MARKERS:
                        if line.startswith(marker):
                            _record(seen, root, line[len(marker) :].strip())
    return tuple(seen)


def model_display_name(model: Any) -> str | None:
    """Best-effort model identifier for the identity prompt section."""
    for attribute in MODEL_NAME_ATTRIBUTES:
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


def build_prompt_context(
    *,
    workspace: str,
    messages: Iterable[Any] | None,
    model: str | None,
) -> PromptContext:
    """Assemble the context handed to every prompt-section provider."""
    return PromptContext(
        workspace=workspace,
        touched_paths=touched_paths_from_messages(messages, workspace),
        model=model,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_prompt_context.py -q`
Expected: PASS（10 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/prompts/context.py tests/test_prompt_context.py
git commit -m "feat: derive prompt context from touched workspace paths"
```

---

### Task 6: 仓库指令发现与注入

**Files:**
- Create: `src/langharness_core/plugins/instructions/__init__.py`
- Create: `src/langharness_core/plugins/instructions/resolver.py`
- Create: `src/langharness_core/plugins/instructions/repository.py`
- Modify: `src/langharness_core/plugin.py`
- Test: `tests/test_instruction_resolver.py`（新建）
- Test: `tests/test_repository_instructions.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `PromptSection`、`PromptContext`；Task 3 的 `digest`、`truncate_bytes`。
- Produces（Task 7、计划 2/3 复用）：
  - `resolver.DEFAULT_BUDGET_BYTES = 65_536`、`resolver.DEFAULT_NAMES = ("AGENTS.md",)`、`resolver.MAX_INSTRUCTION_BYTES = 262_144`、`resolver.ROOT_BUDGET_SHARE = 2`
  - `resolver.InstructionFile(path, relative, depth, text, sha256, size, truncated)`
  - `resolver.InstructionSelection(files, omitted, budget)`
  - `resolver.InstructionResolver(root: Path, *, names: Sequence[str] = DEFAULT_NAMES, budget: int = DEFAULT_BUDGET_BYTES)`，方法 `resolve(touched_paths) -> InstructionSelection`、`directories(touched_paths) -> list[tuple[Path, int]]`
  - `repository.INSTRUCTION_ORDER = 500`、`repository.OMISSION_ORDER = 599`、`repository.MAX_INSTRUCTION_DEPTH = 90`
  - 工厂名 `repository-instructions-plugin-factory`，属性 `plugin.instructions.include`（额外兼容文件名，逗号分隔或 list，默认空）、`plugin.instructions.budget_bytes`

**预算规则（测试与实现都按此执行）：**
- 根指令（depth 0）永远保留，单独读取上限为 `budget // ROOT_BUDGET_SHARE`；
- 更深的指令按 depth 从大到小（最接近目标文件的优先）在剩余预算内贪心保留，放不下的整份记为 omitted；
- 任何一次读取最多 `min(该层上限, MAX_INSTRUCTION_BYTES)` 字节，防止超大文件把进程读爆；
- section order 是 `500 + min(depth, 90)`，省略说明固定为 599，都排在 `mode-policy`(700) 之前；
- 文件以 `(mtime_ns, size)` 为缓存键，命中则复用；未命中重新读取并重算 hash。mtime 变化但 hash 不变时 section 的 `sha256` 保持稳定，下游无需失效。

- [ ] **Step 1: 写失败测试（resolver）**

新建 `tests/test_instruction_resolver.py`：

```python
"""Tests for repository instruction discovery and budgeting."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from langharness_core.plugins.instructions.resolver import (
    DEFAULT_BUDGET_BYTES,
    InstructionResolver,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_root_instructions_are_found_without_touched_paths(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root rules")
    selection = InstructionResolver(tmp_path).resolve(())
    assert [item.relative for item in selection.files] == ["AGENTS.md"]
    assert selection.files[0].text == "root rules"
    assert selection.files[0].depth == 0


def test_deeper_instructions_cover_their_own_subtree(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "src" / "AGENTS.md", "src")
    write(tmp_path / "src" / "deep" / "AGENTS.md", "deep")
    write(tmp_path / "other" / "AGENTS.md", "other")
    selection = InstructionResolver(tmp_path).resolve(("src/deep/a.py",))
    assert [item.relative for item in selection.files] == [
        "AGENTS.md",
        "src/AGENTS.md",
        "src/deep/AGENTS.md",
    ]


def test_a_sibling_subtree_is_not_loaded(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "other" / "AGENTS.md", "other")
    selection = InstructionResolver(tmp_path).resolve(("src/a.py",))
    assert [item.relative for item in selection.files] == ["AGENTS.md"]


def test_touched_directory_paths_are_followed(tmp_path: Path) -> None:
    write(tmp_path / "src" / "AGENTS.md", "src")
    selection = InstructionResolver(tmp_path).resolve(("src",))
    assert [item.relative for item in selection.files] == ["src/AGENTS.md"]


def test_paths_outside_the_workspace_are_ignored(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    selection = InstructionResolver(tmp_path).resolve(("../../etc/passwd",))
    assert [item.relative for item in selection.files] == ["AGENTS.md"]


def test_symlink_escape_is_ignored(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-escape"
    write(outside / "AGENTS.md", "secret")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    selection = InstructionResolver(tmp_path).resolve(("link/a.py",))
    assert [item.relative for item in selection.files] == []


def test_extra_names_are_loaded_only_when_requested(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "CLAUDE.md", "compat")
    default = InstructionResolver(tmp_path).resolve(())
    assert [item.relative for item in default.files] == ["AGENTS.md"]
    extended = InstructionResolver(tmp_path, names=("AGENTS.md", "CLAUDE.md")).resolve(())
    assert [item.relative for item in extended.files] == ["AGENTS.md", "CLAUDE.md"]


def test_budget_keeps_the_root_and_the_nearest_instruction(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 100)
    write(tmp_path / "src" / "AGENTS.md", "m" * 100)
    write(tmp_path / "src" / "deep" / "AGENTS.md", "n" * 100)
    selection = InstructionResolver(
        tmp_path, budget=250, names=("AGENTS.md",)
    ).resolve(("src/deep/a.py",))
    assert [item.relative for item in selection.files] == [
        "AGENTS.md",
        "src/deep/AGENTS.md",
    ]
    assert selection.omitted == ("src/AGENTS.md",)


def test_oversized_root_instruction_is_truncated_not_dropped(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 1000)
    selection = InstructionResolver(tmp_path, budget=200).resolve(())
    assert len(selection.files) == 1
    assert selection.files[0].truncated is True
    assert len(selection.files[0].text.encode("utf-8")) <= 100
    assert selection.files[0].sha256.startswith("sha256:")


def test_cache_reuses_text_until_the_file_changes(tmp_path: Path) -> None:
    target = write(tmp_path / "AGENTS.md", "first")
    resolver = InstructionResolver(tmp_path)
    assert resolver.resolve(()).files[0].text == "first"
    target.write_text("second version", encoding="utf-8")
    os.utime(target, (1, 1))
    assert resolver.resolve(()).files[0].text == "second version"


def test_hash_is_stable_when_only_the_timestamp_changes(tmp_path: Path) -> None:
    target = write(tmp_path / "AGENTS.md", "stable")
    resolver = InstructionResolver(tmp_path)
    before = resolver.resolve(()).files[0].sha256
    os.utime(target, (1, 1))
    assert resolver.resolve(()).files[0].sha256 == before


def test_a_directory_named_like_an_instruction_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()
    assert InstructionResolver(tmp_path).resolve(()).files == ()


def test_a_file_that_cannot_be_inspected_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / "AGENTS.md", "root")

    def boom(self: Path, **kwargs: object) -> os.stat_result:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "stat", boom)
    assert InstructionResolver(tmp_path).resolve(()).files == ()


def test_directory_chain_is_shallowest_first(tmp_path: Path) -> None:
    resolver = InstructionResolver(tmp_path)
    chain = resolver.directories(("src/deep/a.py",))
    assert [(path.name, depth) for path, depth in chain] == [
        (tmp_path.name, 0),
        ("src", 1),
        ("deep", 2),
    ]


def test_default_budget_is_64_kib() -> None:
    assert DEFAULT_BUDGET_BYTES == 65_536
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_instruction_resolver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.instructions'`

- [ ] **Step 3: 写 resolver 实现**

`src/langharness_core/plugins/instructions/__init__.py`：

```python
"""Repository instruction plugins."""
```

`src/langharness_core/plugins/instructions/resolver.py`：

```python
"""Discovery, reading and budgeting of repository instruction files."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from langharness_core.common.text import digest, truncate_bytes

DEFAULT_BUDGET_BYTES = 65_536
DEFAULT_NAMES: tuple[str, ...] = ("AGENTS.md",)
# Bounds one read so a pathological instruction file cannot exhaust memory.
MAX_INSTRUCTION_BYTES = 262_144
# The root instruction may use at most this fraction of the budget, leaving
# room for the deeper files that describe the subdirectory being edited.
ROOT_BUDGET_SHARE = 2


@dataclass(frozen=True, slots=True)
class InstructionFile:
    """One instruction file selected for the prompt."""

    path: Path
    relative: str
    depth: int
    text: str
    sha256: str
    size: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class InstructionSelection:
    """The instruction files that fit, plus the ones that did not."""

    files: tuple[InstructionFile, ...]
    omitted: tuple[str, ...]
    budget: int


@dataclass(slots=True)
class _CacheEntry:
    mtime_ns: int
    size: int
    sha256: str
    text: str


class InstructionResolver:
    """Resolves the instruction files applying to a set of touched paths."""

    def __init__(
        self,
        root: Path,
        *,
        names: Sequence[str] = DEFAULT_NAMES,
        budget: int = DEFAULT_BUDGET_BYTES,
    ) -> None:
        self._root = Path(root)
        self._names = tuple(names)
        self._budget = budget
        self._cache: dict[Path, _CacheEntry] = {}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @property
    def budget(self) -> int:
        return self._budget

    def directories(self, touched_paths: Iterable[str] | None) -> list[tuple[Path, int]]:
        """Return (directory, depth) pairs for the root and every touched directory."""
        found: dict[Path, int] = {self._root: 0}
        for raw in touched_paths or ():
            directory = self._directory_for(raw)
            if directory is None:
                continue
            for depth, item in self._chain(directory):
                found[item] = min(depth, found.get(item, depth))
        return sorted(found.items(), key=lambda item: (item[1], item[0].as_posix()))

    def resolve(self, touched_paths: Iterable[str] | None) -> InstructionSelection:
        """Select the instruction files for one prompt context."""
        candidates: list[InstructionFile] = []
        for directory, depth in self.directories(touched_paths):
            for name in self._names:
                found = self._read(directory / name, depth)
                if found is not None:
                    candidates.append(found)
        return self._apply_budget(candidates)

    def _chain(self, directory: Path) -> list[tuple[int, Path]]:
        """(depth, directory) from the workspace root down to ``directory``.

        ``directory`` is always a result of :meth:`_directory_for`, which only
        returns paths inside the root, so the walk needs no containment check.
        """
        chain: list[tuple[int, Path]] = [(0, self._root)]
        parts = directory.relative_to(self._root).parts
        current = self._root
        for depth, part in enumerate(parts, start=1):
            current = current / part
            chain.append((depth, current))
        return chain

    def _directory_for(self, raw: str) -> Path | None:
        """Resolve one touched path to a directory inside the workspace."""
        if not raw:
            return None
        candidate = Path(raw)
        absolute = candidate if candidate.is_absolute() else self._root / candidate
        try:
            resolved = absolute.resolve()
            if resolved != self._root:
                resolved.relative_to(self._root)
        except (OSError, ValueError):
            return None
        if resolved.is_dir():
            return resolved
        return resolved.parent

    def _read(self, path: Path, depth: int) -> InstructionFile | None:
        """Read one instruction file, honouring the per-depth byte limit.

        One ``except OSError`` covers stat and read alike: an instruction file
        that cannot be inspected is simply not part of the prompt.
        """
        try:
            if not path.is_file():
                return None
            stat = path.stat()
            cached = self._cache.get(path)
            if (
                cached is not None
                and cached.mtime_ns == stat.st_mtime_ns
                and cached.size == stat.st_size
            ):
                text, sha256 = cached.text, cached.sha256
            else:
                limit = min(self._limit(depth), MAX_INSTRUCTION_BYTES)
                raw = path.open("rb").read(limit)
                text = raw.decode("utf-8", errors="replace")
                sha256 = digest(text)
                self._cache[path] = _CacheEntry(
                    stat.st_mtime_ns, stat.st_size, sha256, text
                )
        except OSError:
            return None
        truncated = stat.st_size > len(text.encode("utf-8"))
        if truncated:
            text, _ = truncate_bytes(text, self._limit(depth))
        relative = path.relative_to(self._root).as_posix()
        return InstructionFile(
            path=path,
            relative=relative,
            depth=depth,
            text=text,
            sha256=sha256,
            size=len(text.encode("utf-8")),
            truncated=truncated,
        )

    def _limit(self, depth: int) -> int:
        if depth == 0:
            return max(self._budget // ROOT_BUDGET_SHARE, 1)
        return self._budget

    def _apply_budget(
        self, candidates: list[InstructionFile]
    ) -> InstructionSelection:
        """Keep the root instruction and then the nearest-to-target ones."""
        kept: list[InstructionFile] = []
        omitted: list[str] = []
        used = 0
        for item in sorted(candidates, key=lambda entry: (entry.depth, entry.relative)):
            if item.depth == 0:
                kept.append(item)
                used += item.size
        deeper = sorted(
            (item for item in candidates if item.depth > 0),
            key=lambda entry: (-entry.depth, entry.relative),
        )
        for item in deeper:
            if used + item.size > self._budget:
                omitted.append(item.relative)
                continue
            kept.append(item)
            used += item.size
        kept.sort(key=lambda entry: (entry.depth, entry.relative))
        return InstructionSelection(
            files=tuple(kept), omitted=tuple(omitted), budget=self._budget
        )
```

- [ ] **Step 4: 运行 resolver 测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_instruction_resolver.py -q`
Expected: PASS（13 passed）

- [ ] **Step 5: 写失败的 plugin 测试**

新建 `tests/test_repository_instructions.py`：

```python
"""Tests for the repository-instructions prompt-section plugin."""
# mypy: ignore-errors

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import PromptContext, PromptSectionProvider
from langharness_core.plugin import AGENT_PLUGIN_CATALOG, agent_plugin_descriptor
from langharness_core.plugins.instructions.repository import (
    INSTRUCTION_ORDER,
    OMISSION_ORDER,
    RepositoryInstructionsPlugin,
)
from langharness_plugin.validation import validate


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def plugin(**properties) -> RepositoryInstructionsPlugin:
    instance = RepositoryInstructionsPlugin()
    for key, value in properties.items():
        setattr(instance, key, value)
    return instance


def test_root_only_before_any_target_is_known(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root rules")
    write(tmp_path / "src" / "AGENTS.md", "src rules")
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path)))
    assert [item.name for item in sections] == ["repository-instructions:AGENTS.md"]
    assert sections[0].order == INSTRUCTION_ORDER


def test_deeper_files_are_ordered_by_depth(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "src" / "AGENTS.md", "src")
    sections = plugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path), touched_paths=("src/a.py",))
    )
    assert [item.name for item in sections] == [
        "repository-instructions:AGENTS.md",
        "repository-instructions:src/AGENTS.md",
    ]
    assert sections[1].order == INSTRUCTION_ORDER + 1


def test_source_records_the_real_path_and_text_is_verbatim(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root\nrules\n")
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path)))
    assert sections[0].text == "root\nrules\n"
    assert sections[0].source == str((tmp_path / "AGENTS.md").resolve())


def test_omitted_files_are_reported_to_the_model(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 100)
    write(tmp_path / "src" / "AGENTS.md", "m" * 100)
    write(tmp_path / "src" / "deep" / "AGENTS.md", "n" * 100)
    sections = plugin(_budget=250).get_prompt_sections(
        PromptContext(workspace=str(tmp_path), touched_paths=("src/deep/a.py",))
    )
    notice = sections[-1]
    assert notice.name == "repository-instructions-omitted"
    assert notice.order == OMISSION_ORDER
    assert "src/AGENTS.md" in notice.text
    assert "250" in notice.text


def test_no_notice_when_everything_fits(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path)))
    assert [item.name for item in sections] == ["repository-instructions:AGENTS.md"]


def test_truncated_root_is_reported_in_the_notice(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 1000)
    sections = plugin(_budget=200).get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert "AGENTS.md" in sections[-1].text
    assert "truncated" in sections[-1].text.lower()


def test_missing_workspace_yields_no_sections(tmp_path: Path) -> None:
    sections = plugin().get_prompt_sections(PromptContext(workspace=str(tmp_path / "nope")))
    assert sections == []


def test_include_property_accepts_a_comma_separated_string(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "CLAUDE.md", "compat")
    sections = plugin(_include="CLAUDE.md").get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert [item.name for item in sections] == [
        "repository-instructions:AGENTS.md",
        "repository-instructions:CLAUDE.md",
    ]


def test_include_property_accepts_a_list(tmp_path: Path) -> None:
    write(tmp_path / "CLAUDE.md", "compat")
    sections = plugin(_include=["CLAUDE.md"]).get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert [item.name for item in sections] == ["repository-instructions:CLAUDE.md"]


def test_resolvers_are_cached_per_workspace(tmp_path: Path) -> None:
    instance = plugin()
    first = instance._resolver(tmp_path)
    assert instance._resolver(tmp_path) is first


def test_plugin_conforms_to_its_contract() -> None:
    assert validate(plugin(), PromptSectionProvider) == ()


def test_catalog_entry_points_at_the_component() -> None:
    assert AGENT_PLUGIN_CATALOG["repository-instructions"] == (
        "langharness_core.plugins.instructions.repository",
        "repository-instructions-plugin-factory",
        "agent.plugin.prompt_section",
    )
    assert agent_plugin_descriptor("repository-instructions").description.strip()


def test_plugin_info_reports_the_component_identity() -> None:
    assert plugin().get_plugin_info() == {
        "name": "repository-instructions",
        "version": "1.0.0",
    }
```

- [ ] **Step 6: 运行 plugin 测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_repository_instructions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.instructions.repository'`

- [ ] **Step 7: 写 plugin 实现**

`src/langharness_core/plugins/instructions/repository.py`：

```python
"""Prompt sections carrying repository instructions such as ``AGENTS.md``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import (
    PromptContext,
    PromptSection,
    PromptSectionProvider,
)
from langharness_core.plugins.instructions.resolver import (
    DEFAULT_BUDGET_BYTES,
    DEFAULT_NAMES,
    InstructionResolver,
    InstructionSelection,
)

INSTRUCTION_ORDER = 500
OMISSION_ORDER = 599
# Keeps the deepest instruction sections ahead of the mode-policy slot (700).
MAX_INSTRUCTION_DEPTH = 90


@ComponentFactory("repository-instructions-plugin-factory")
@Provides(PromptSectionProvider)
@Property("_plugin_name", "plugin.name", "repository-instructions")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_include", "plugin.instructions.include", None)
@Property("_budget", "plugin.instructions.budget_bytes", DEFAULT_BUDGET_BYTES)
class RepositoryInstructionsPlugin:
    """Contributes the instruction files between the workspace root and the target.

    Only the root instruction is loaded before the conversation names a deeper
    path; each tool call that reaches a new directory brings that directory's
    instructions into the next model step, because the prompt is assembled per
    request rather than baked into the graph.
    """

    def __init__(self) -> None:
        self._plugin_name = "repository-instructions"
        self._plugin_version = "1.0.0"
        self._include: Any = None
        self._budget = DEFAULT_BUDGET_BYTES
        self._resolvers: dict[str, InstructionResolver] = {}

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        resolver = self._resolver(Path(context.workspace))
        if resolver is None:
            return []
        return self._sections(resolver.resolve(context.touched_paths))

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _resolver(self, workspace: Path) -> InstructionResolver | None:
        root = workspace.resolve()
        if not root.is_dir():
            return None
        key = str(root)
        resolver = self._resolvers.get(key)
        if resolver is None:
            resolver = InstructionResolver(
                root, names=self._names(), budget=int(self._budget)
            )
            self._resolvers[key] = resolver
        return resolver

    def _names(self) -> tuple[str, ...]:
        extra = self._include
        if isinstance(extra, str):
            extra = [item.strip() for item in extra.split(",") if item.strip()]
        if isinstance(extra, (list, tuple)):
            return tuple(
                dict.fromkeys([*DEFAULT_NAMES, *(str(item) for item in extra)])
            )
        return DEFAULT_NAMES

    def _sections(self, selection: InstructionSelection) -> list[PromptSection]:
        sections = [
            PromptSection(
                name=f"repository-instructions:{item.relative}",
                order=INSTRUCTION_ORDER + min(item.depth, MAX_INSTRUCTION_DEPTH),
                text=item.text,
                source=str(item.path),
            )
            for item in selection.files
        ]
        notice = self._notice(selection)
        if notice is not None:
            sections.append(
                PromptSection(
                    name="repository-instructions-omitted",
                    order=OMISSION_ORDER,
                    text=notice,
                    source=self._plugin_name,
                )
            )
        return sections

    def _notice(self, selection: InstructionSelection) -> str | None:
        truncated = [item.relative for item in selection.files if item.truncated]
        if not selection.omitted and not truncated:
            return None
        parts = [
            f"Some repository instructions did not fit the {selection.budget}-byte "
            "budget and are not reproduced above."
        ]
        if selection.omitted:
            parts.append("Omitted: " + ", ".join(selection.omitted) + ".")
        if truncated:
            parts.append("Truncated: " + ", ".join(truncated) + ".")
        parts.append(
            "Read those files directly if their directory becomes relevant."
        )
        return " ".join(parts)
```

在 `src/langharness_core/plugin.py` 中：

1. 从 `langharness_core.contracts` 的导入列表里加上 `SPEC_PROMPT_SECTION`（字母序：在 `SPEC_NAME` 与 `SPEC_RESPONSE_FORMAT` 之间）。
2. 在 `AGENT_PLUGIN_CATALOG` 的 `"workspace"` 之后加入：

```python
    "repository-instructions": (
        "langharness_core.plugins.instructions.repository",
        "repository-instructions-plugin-factory",
        SPEC_PROMPT_SECTION,
    ),
```

3. 在 `AGENT_PLUGIN_DESCRIPTIONS` 中加入：

```python
    "repository-instructions": (
        "Contributes AGENTS.md and compatible instruction files from the "
        "workspace root down to the directory being edited. Implements "
        "agent.plugin.prompt_section. Properties: plugin.instructions.include, "
        "plugin.instructions.budget_bytes, plugin.agent_id. Requires a restart "
        "for property changes. Uninstall when the agent is removed."
    ),
```

4. 在 `builtin_package()` 的 contributions 里，`workspace-template` 之后加入：

```python
            PluginContribution(
                "repository-instructions-template",
                "agent",
                agent_plugin_template_descriptor("repository-instructions"),
            ),
```

- [ ] **Step 8: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_repository_instructions.py tests/test_instruction_resolver.py -q`
Expected: PASS（13 + 13 passed）

- [ ] **Step 9: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 10: Commit**

```bash
git add src/langharness_core/plugins/instructions src/langharness_core/plugin.py \
        tests/test_instruction_resolver.py tests/test_repository_instructions.py
git commit -m "feat: load repository instructions into ordered prompt sections"
```

---

### Task 7: Prompt middleware 与 agent loop 集成

**Files:**
- Create: `src/langharness_core/plugins/prompts/middleware.py`
- Modify: `src/langharness_core/plugins/loop/agent_loop.py`
- Modify: `src/langharness_core/plugin.py`
- Modify: `tests/test_system_prompt.py`
- Modify: `tests/test_agent_directory.py`
- Test: `tests/test_prompt_middleware.py`（新建）
- Test: `tests/test_prompt_integration.py`（新建）

**Interfaces:**
- Consumes: Task 1 的两个规格与值类型；Task 3 的 `assemble_sections`；Task 4 的 `legacy_sections`；Task 5 的 `build_prompt_context`、`model_display_name`。
- Produces（计划 3 复用）：
  - `middleware.PromptMiddleware(build: Callable[[Mapping[str, Any]], AssembledPrompt])` —— `AgentMiddleware` 子类，同时实现 `wrap_model_call` 与 `awrap_model_call`
  - `PluginAgentLoop._prompt_for_request(state) -> AssembledPrompt`
  - `PluginAgentLoop._workspace_root() -> str`
  - `PluginAgentLoop._model_name() -> str | None`
  - `AGENT_LOOP_FIELDS` 新增 `SPEC_PROMPT_SECTION: "_prompt_section_providers"`、`SPEC_WORKSPACE: "_workspace_provider"`

**为什么 middleware 放在链尾：** LangChain 的 middleware 是"列表第一个为最外层"。把
`PromptMiddleware` 放在最后使它在最接近模型的位置生效，成为 system message 的最终
裁定者。

**为什么 provider 失败不抛出：** prompt 组装在请求路径上执行，一个坏 provider
不应该让整次模型调用失败。失败被记录为 ERROR 日志，其余 sections 照常生效。

- [ ] **Step 1: 写失败的 middleware 测试**

新建 `tests/test_prompt_middleware.py`：

```python
"""Tests for the per-step prompt middleware."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import SystemMessage

from langharness_core.plugins.prompts.middleware import PromptMiddleware
from langharness_core.plugins.prompts.sections import AssembledPrompt


class FakeRequest:
    """Stands in for ``ModelRequest``; only ``override`` is exercised here."""

    def __init__(self, state: dict[str, Any], system_message: Any = None) -> None:
        self.state = state
        self.system_message = system_message

    def override(self, **overrides: Any) -> FakeRequest:
        return FakeRequest(
            self.state, overrides.get("system_message", self.system_message)
        )


def request(system_message: Any = None) -> FakeRequest:
    return FakeRequest({"messages": []}, system_message)


def recording_handler(seen: list) -> Any:
    def handler(req: FakeRequest) -> str:
        seen.append(req.system_message)
        return "response"

    return handler


def build(text: str, empty: bool = False) -> Any:
    def builder(state: Any) -> AssembledPrompt:
        return AssembledPrompt(text="" if empty else text, traces=())

    return builder


def test_sync_path_sets_the_system_message() -> None:
    seen: list = []
    middleware = PromptMiddleware(build("assembled"))
    assert middleware.wrap_model_call(request(), recording_handler(seen)) == "response"
    assert seen[0] == SystemMessage(content="assembled")


def test_async_path_sets_the_system_message() -> None:
    seen: list = []

    async def handler(req: FakeRequest) -> str:
        seen.append(req.system_message)
        return "response"

    middleware = PromptMiddleware(build("assembled"))
    assert asyncio.run(middleware.awrap_model_call(request(), handler)) == "response"
    assert seen[0] == SystemMessage(content="assembled")


def test_empty_prompt_leaves_the_request_untouched() -> None:
    seen: list = []
    existing = SystemMessage(content="pre-existing")
    middleware = PromptMiddleware(build("", empty=True))
    middleware.wrap_model_call(
        request(system_message=existing), recording_handler(seen)
    )
    assert seen[0] is existing


def test_non_empty_prompt_replaces_an_existing_system_message() -> None:
    seen: list = []
    middleware = PromptMiddleware(build("assembled"))
    middleware.wrap_model_call(
        request(system_message=SystemMessage(content="old")), recording_handler(seen)
    )
    assert seen[0] == SystemMessage(content="assembled")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompt_middleware.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.prompts.middleware'`

- [ ] **Step 3: 写 middleware 实现**

`src/langharness_core/plugins/prompts/middleware.py`：

```python
"""Middleware that assembles the system prompt on every model step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage

from langharness_core.plugins.prompts.sections import AssembledPrompt

PromptBuilder = Callable[[Mapping[str, Any]], AssembledPrompt]


class PromptMiddleware(AgentMiddleware):
    """Replaces the model request's system message with freshly assembled sections.

    Both hooks are implemented on purpose: the agent loop streams through the
    async path, and ``AgentMiddleware`` raises when only the synchronous one
    exists.  Recomputing per request is what lets a provider react to what the
    conversation has already touched without rebuilding the graph.
    """

    def __init__(self, build: PromptBuilder) -> None:
        super().__init__()
        self._build = build

    def _apply(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        prompt = self._build(request.state)
        if prompt.empty:
            return request
        return request.override(system_message=SystemMessage(content=prompt.text))

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._apply(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._apply(request))
```

- [ ] **Step 4: 运行 middleware 测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_prompt_middleware.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 写失败的 loop 集成测试**

把 `tests/test_system_prompt.py` 整体替换为下面这版。`make_provider` 现在必须
同时提供 `get_plugin_info`（`legacy_sections` 要读它），原有的三个
`_collect_system_prompt()` 断言改为断言 `_prompt_for_request()`：

```python
"""Tests for the system-prompt plugin vertical slice."""
# mypy: ignore-errors

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import langharness_core.plugins.loop.agent_loop as agent_loop_module
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.prompts.middleware import PromptMiddleware
from langharness_core.plugins.system_prompt.template_system_prompt import (
    TemplateSystemPromptPlugin,
)


def make_provider(text: str, name: str = "system-prompt-plugin"):
    return SimpleNamespace(
        get_system_prompt=lambda: text,
        get_plugin_info=lambda: {"name": name, "version": "1.0.0"},
    )


def test_system_prompt_plugin_exposes_configured_prompt() -> None:
    plugin = TemplateSystemPromptPlugin()
    plugin._system_prompt = "You are a calculator."
    assert plugin.get_system_prompt() == "You are a calculator."
    assert plugin.get_plugin_info() == {
        "name": "system-prompt-plugin",
        "version": "1.0.0",
    }


def test_legacy_providers_are_joined_in_effective_order() -> None:
    loop = PluginAgentLoop()
    loop._system_prompt_providers = [
        make_provider("You are A.", "a"),
        make_provider("You are B.", "b"),
    ]
    assert loop._prompt_for_request({"messages": []}).text == "You are A.\nYou are B."


def test_no_providers_produce_an_empty_prompt() -> None:
    loop = PluginAgentLoop()
    loop._system_prompt_providers = []
    assert loop._prompt_for_request({"messages": []}).empty is True


def test_nearest_scope_provider_shadows_the_same_plugin_further_out() -> None:
    loop = PluginAgentLoop()
    loop._scope_chain = ["agent:a", "agent", "root"]
    near = make_provider("near", "persona")
    far = make_provider("far", "persona")
    loop._system_prompt_providers = [far, near]
    loop._service_metadata[id(far)] = ("root", "persona", 0)
    loop._service_metadata[id(near)] = ("agent:a", "persona", 0)
    assert loop._prompt_for_request({"messages": []}).text == "near"


def test_a_failing_section_provider_does_not_break_assembly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Boom:
        def get_prompt_sections(self, context):
            raise RuntimeError("provider exploded")

    loop = PluginAgentLoop()
    loop._system_prompt_providers = [make_provider("legacy survives")]
    loop._prompt_section_providers = [Boom()]
    assembled = loop._prompt_for_request({"messages": []})
    assert assembled.text == "legacy survives"
    assert "provider exploded" in caplog.text


def test_rebuild_installs_the_prompt_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_agent(
        model, tools=None, *, system_prompt=None, middleware=None, **kwargs
    ):
        captured["system_prompt"] = system_prompt
        captured["middleware"] = middleware
        return object()

    monkeypatch.setattr(agent_loop_module, "create_agent", fake_create_agent)

    loop = PluginAgentLoop()
    loop._llm_provider = SimpleNamespace(get_model=lambda: object())
    loop._tool_providers = []
    loop._middleware_providers = []
    loop._system_prompt_providers = [make_provider("You are A.")]

    loop._rebuild()

    assert captured["system_prompt"] is None
    middlewares = captured["middleware"]
    assert isinstance(middlewares[-1], PromptMiddleware)
```

再新建 `tests/test_prompt_integration.py`，用真实 Pelix 框架验证整条链路：

```python
"""End-to-end prompt assembly through the real Pelix/iPOPO framework."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from langharness_core.contracts import (
    SPEC_AGENT_LOOP,
    SPEC_LLM,
    SPEC_PROMPT_SECTION,
    SPEC_SYSTEM_PROMPT,
    SPEC_TOOL,
    SPEC_WORKSPACE,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_scope import ROOT_SCOPE_ID

DESCRIPTION = (
    "Prompt integration test plugin. Implements a test specification. "
    "Properties vary per instance. Requires a restart for property changes. "
    "Uninstall when tests finish."
)

SYSTEM_PROMPTS: list[str] = []


@tool
def read_file(path: str) -> str:
    """Read a file from the workspace."""
    return f"contents of {path}"


class CapturingModel(BaseChatModel):
    """Records every system prompt it is asked to answer under."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for message in messages:
            if message.type == "system":
                SYSTEM_PROMPTS.append(str(message.content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    @property
    def _llm_type(self) -> str:
        return "capturing"

    def bind_tools(self, tools, **kwargs):
        return self


class ToolCallingModel(CapturingModel):
    """Calls ``read_file`` once, then stops."""

    called: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for message in messages:
            if message.type == "system":
                SYSTEM_PROMPTS.append(str(message.content))
        if not self.called:
            self.called = True
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "read_file",
                                    "args": {"path": "deep/a.py"},
                                    "id": "c1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def bind_tools(self, tools, **kwargs):
        return self


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


def start(
    installs: list[tuple[PluginDescriptor, dict[str, Any]]],
) -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager.start()
    for item, _ in installs:
        manager.install_descriptor(item)
    for item, properties in installs:
        manager.create_instance(
            item.factory, item.module, ROOT_SCOPE_ID, properties=properties
        )
    return manager


def installs(
    workspace: Path, model: BaseChatModel
) -> list[tuple[PluginDescriptor, dict[str, Any]]]:
    return [
        (
            descriptor(
                "llm",
                "langharness_core.plugins.llm.llm",
                "llm-plugin-factory",
                SPEC_LLM,
            ),
            {"plugin.model.instance": model},
        ),
        (
            descriptor(
                "tools",
                "langharness_core.plugins.tools.tools",
                "tools-plugin-factory",
                SPEC_TOOL,
            ),
            {"plugin.tool_functions": [read_file]},
        ),
        (
            descriptor(
                "workspace",
                "langharness_core.plugins.workspace.root",
                "workspace-root-plugin-factory",
                SPEC_WORKSPACE,
            ),
            {"plugin.workspace.root_dir": str(workspace)},
        ),
        (
            descriptor(
                "system-prompt",
                "langharness_core.plugins.system_prompt.template_system_prompt",
                "system-prompt-plugin-factory",
                SPEC_SYSTEM_PROMPT,
            ),
            {"plugin.system_prompt": "legacy persona"},
        ),
        (
            descriptor(
                "repository-instructions",
                "langharness_core.plugins.instructions.repository",
                "repository-instructions-plugin-factory",
                SPEC_PROMPT_SECTION,
            ),
            {},
        ),
        (
            descriptor(
                "agent-loop",
                "langharness_core.plugins.loop.agent_loop",
                "agent-loop-factory",
                SPEC_AGENT_LOOP,
            ),
            {},
        ),
    ]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_legacy_and_repository_sections_reach_the_model(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root instruction")
    SYSTEM_PROMPTS.clear()
    manager = start(installs(tmp_path, CapturingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("hello")
    finally:
        manager.stop()
    assert SYSTEM_PROMPTS, "the model never saw a system prompt"
    prompt = SYSTEM_PROMPTS[-1]
    # order 500 (instructions) sorts before order 1000 (legacy persona)
    assert prompt.index("root instruction") < prompt.index("legacy persona")


def test_deeper_instructions_arrive_on_the_next_model_step(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root instruction")
    write(tmp_path / "deep" / "AGENTS.md", "deep instruction")
    SYSTEM_PROMPTS.clear()
    manager = start(installs(tmp_path, ToolCallingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("touch deep/a.py")
    finally:
        manager.stop()
    assert len(SYSTEM_PROMPTS) >= 2, SYSTEM_PROMPTS
    assert "deep instruction" not in SYSTEM_PROMPTS[0]
    assert "deep instruction" in SYSTEM_PROMPTS[1]


def test_repository_instructions_drop_out_when_the_workspace_is_gone() -> None:
    SYSTEM_PROMPTS.clear()
    manager = start(installs(Path("/nonexistent-workspace-root"), CapturingModel()))
    try:
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop.invoke("hello")
    finally:
        manager.stop()
    assert SYSTEM_PROMPTS[-1] == "legacy persona"
```

- [ ] **Step 6: 运行集成测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompt_integration.py tests/test_system_prompt.py -q`
Expected: FAIL — `PluginAgentLoop` 还没有 `_prompt_for_request`，
`_rebuild` 仍然把 `system_prompt` 直接传给 `create_agent`。

- [ ] **Step 7: 改 agent loop**

在 `src/langharness_core/plugins/loop/agent_loop.py` 中：

1. 从 `collections.abc` 导入里加上 `Mapping`；从 `pathlib` 导入 `Path`。

2. 在 `langharness_core.contracts` 导入块中加上 `PromptSection`、
   `PromptSectionProvider`、`WorkspaceProvider`（字母序）。

3. 新增导入：

```python
from langharness_core.plugins.prompts.context import (
    build_prompt_context,
    model_display_name,
)
from langharness_core.plugins.prompts.legacy import legacy_sections
from langharness_core.plugins.prompts.middleware import PromptMiddleware
from langharness_core.plugins.prompts.sections import AssembledPrompt, assemble_sections
```

4. 在 `@Requires("_system_prompt_providers", ...)` 之后加两个依赖声明：

```python
@Requires(
    "_prompt_section_providers", PromptSectionProvider, aggregate=True, optional=True
)
@RequiresBest(
    "_workspace_provider", WorkspaceProvider, optional=True, immediate_rebind=True
)
```

5. 在 `@ScopedDependencies(...)` 的字段列表里加入 `"_prompt_section_providers"` 与
   `"_workspace_provider"`。

6. 在 `__init__` 中加入字段与守卫：

```python
        self._prompt_section_providers: list[Any] = []
        self._workspace_provider: Any = None
```

```python
            "_prompt_section_providers": ContractGuard(
                self, "_prompt_section_providers", PromptSectionProvider
            ),
            "_workspace_provider": ContractGuard(
                self, "_workspace_provider", WorkspaceProvider
            ),
```

7. 在 `_on_system_prompt_unbind` 之后加入四个回调：

```python
    @BindField("_prompt_section_providers", if_valid=True)
    def _on_prompt_section_bind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_prompt_section_providers", if_valid=True)
    def _on_prompt_section_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_workspace_provider", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_workspace_provider")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None
```

`_prompt_section_providers` 是聚合字段，因此和 `_system_prompt_providers` 一样要
`_remember_service` / `_forget_service`（scope 遮蔽依赖这些元数据）；
`_workspace_provider` 是 `RequiresBest`，和 `_name_provider` 一样不需要元数据。

注意：四个回调都调用 `_rebuild()`。middleware 每次请求都重新读取活的 provider
列表，所以 prompt 变化本身不需要重建 graph；这里保持与其它字段一致只是为了让
`describe()` 与绑定生命周期同步，代价是一次重建。若实测发现重建过于频繁，可以
把 `_rebuild()` 换成 `pass`，届时在 `_prompt_for_request` 的日志里验证生效。

8. 删除 `_collect_system_prompt()`，替换为：

```python
    def _workspace_root(self) -> str:
        """The realpath every path-taking plugin resolves against."""
        if self._workspace_provider is not None:
            return str(self._workspace_provider.get_workspace())
        return str(Path.cwd())

    def _model_name(self) -> str | None:
        if self._llm_provider is None:
            return None
        return model_display_name(self._llm_provider.get_model())

    def _prompt_for_request(self, state: Mapping[str, Any]) -> AssembledPrompt:
        """Assemble the system prompt for one model step.

        A failing section provider must not abort the model call: its section is
        dropped, the failure is logged, and the remaining sections still reach
        the model.
        """
        workspace = self._workspace_root()
        context = build_prompt_context(
            workspace=workspace,
            messages=state.get("messages") or (),
            model=self._model_name(),
        )
        sections: list[PromptSection] = list(
            legacy_sections(self._effective(self._system_prompt_providers))
        )
        for provider in self._effective(self._prompt_section_providers):
            try:
                sections.extend(provider.get_prompt_sections(context))
            except Exception:
                LOGGER.exception(
                    "Prompt section provider %s failed; its section was dropped",
                    getattr(provider, "_plugin_key", provider),
                )
        assembled = assemble_sections(sections)
        if assembled.traces:
            LOGGER.debug(
                "prompt sections: %s",
                ", ".join(
                    f"{trace.name}[{trace.bytes}B {trace.sha256[7:19]}]"
                    for trace in assembled.traces
                ),
            )
        return assembled
```

9. 在 `_rebuild()` 里把 `system_prompt=self._collect_system_prompt(),` 换成：

```python
                middleware=[
                    *self._collect_middlewares(),
                    # Innermost: the assembled prompt is the last word on the
                    # request's system message.
                    PromptMiddleware(self._prompt_for_request),
                ],
                system_prompt=None,
```

（删掉原来的 `middleware=self._collect_middlewares(),` 行。）

在 `src/langharness_core/plugin.py` 中，给 `AGENT_LOOP_FIELDS` 增加两行：

```python
    SPEC_PROMPT_SECTION: "_prompt_section_providers",
    SPEC_WORKSPACE: "_workspace_provider",
```

并给 `AGENT_PLUGIN_DESCRIPTIONS` 增加 `"agent-loop"` 的说明补充（可选，不强制）。

同时更新 `tests/test_agent_directory.py` 的 filter 断言，加入两个新字段：

```python
    assert loop.properties["requires.filters"] == {
        "_llm_provider": visibility,
        "_scoped_llm_providers": visibility,
        "_tool_providers": visibility,
        "_name_provider": visibility,
        "_prompt_section_providers": visibility,
        "_workspace_provider": visibility,
    }
```

- [ ] **Step 8: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_prompt_integration.py tests/test_system_prompt.py tests/test_agent_directory.py -q`
Expected: PASS

- [ ] **Step 9: 跑全量门禁**

Run: `make check`
Expected: 全部通过。重点确认 `tests/test_e2e.py` 中断言 system prompt 内容的用例
（例如 `test_real_plugin_agent_stream_renders_response_once`）仍然通过 —— 它们现在
走的是 middleware 注入路径，内容应当逐字节不变。

- [ ] **Step 10: Commit**

```bash
git add src/langharness_core/plugins/prompts/middleware.py \
        src/langharness_core/plugins/loop/agent_loop.py \
        src/langharness_core/plugin.py \
        tests/test_prompt_middleware.py tests/test_prompt_integration.py \
        tests/test_system_prompt.py tests/test_agent_directory.py
git commit -m "feat: assemble the system prompt per model step in the agent loop"
```

---

### Task 8: Code agent 静态 prompt

**Files:**
- Create: `src/langharness_core/prompts/code_agent/identity.md`
- Create: `src/langharness_core/prompts/code_agent/safety.md`
- Create: `src/langharness_core/prompts/code_agent/workflow.md`
- Create: `src/langharness_core/prompts/code_agent/completion.md`
- Create: `src/langharness_core/plugins/prompts/code_agent.py`
- Modify: `src/langharness_core/plugin.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_imports.py`
- Modify: `tests/test_packaging.py`
- Test: `tests/test_code_agent_prompt.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `PromptSectionProvider`、`PromptContext`、`PromptSection`。
- Produces（计划 3 复用）：
  - `code_agent.IDENTITY_ORDER = 0`、`SAFETY_ORDER = 100`、`WORKFLOW_ORDER = 200`、`COMPLETION_ORDER = 300`
  - `code_agent.SECTIONS: tuple[tuple[str, int], ...]`
  - `code_agent.RESOURCE_PACKAGE = "langharness_core"`
  - `code_agent.load_section(name: str) -> str`
  - 工厂名 `code-agent-prompt-plugin-factory`

**资源加载：** 用 `importlib.resources.files("langharness_core") / "prompts" / "code_agent" / f"{name}.md"`。
`prompts/code_agent/` 是纯数据目录，不放 `__init__.py`；这与
`langharness_config/config/langharness.toml` 的既有做法一致，并在
`pyproject.toml` 里声明 package-data。

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_code_agent_prompt.py`：

```python
"""Tests for the shipped code-agent prompt sections."""
# mypy: ignore-errors

from __future__ import annotations

from langharness_core.contracts import PromptContext, PromptSectionProvider
from langharness_core.plugin import AGENT_PLUGIN_CATALOG, agent_plugin_descriptor
from langharness_core.plugins.prompts.code_agent import (
    COMPLETION_ORDER,
    IDENTITY_ORDER,
    SAFETY_ORDER,
    SECTIONS,
    WORKFLOW_ORDER,
    CodeAgentPromptPlugin,
    load_section,
)
from langharness_plugin.validation import validate


def test_every_shipped_section_loads_and_is_non_empty() -> None:
    for name, _ in SECTIONS:
        assert load_section(name).strip()


def test_sections_have_the_designed_order() -> None:
    assert (IDENTITY_ORDER, SAFETY_ORDER, WORKFLOW_ORDER, COMPLETION_ORDER) == (
        0,
        100,
        200,
        300,
    )
    assert [order for _, order in SECTIONS] == sorted(order for _, order in SECTIONS)


def test_identity_and_completion_cover_the_required_points() -> None:
    assert "coding agent" in load_section("identity").lower()
    completion = load_section("completion").lower()
    assert "finish_goal" in completion
    assert "evidence" in completion
    assert "blocked" in completion


def test_safety_names_the_approval_and_secret_rules() -> None:
    safety = load_section("safety").lower()
    assert "workspace" in safety
    assert "secret" in safety or "token" in safety
    assert "git reset" in safety


def test_plugin_renders_sections_in_order(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path), model="gpt-5")
    )
    assert [item.name for item in sections] == ["identity", "safety", "workflow", "completion"]
    assert [item.order for item in sections] == [0, 100, 200, 300]
    assert "gpt-5" in sections[0].text
    assert str(tmp_path) in sections[0].text


def test_unknown_model_falls_back_to_a_placeholder(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert "unknown" in sections[0].text


def test_sources_point_at_the_shipped_files(tmp_path) -> None:
    sections = CodeAgentPromptPlugin().get_prompt_sections(
        PromptContext(workspace=str(tmp_path))
    )
    assert all(item.source.endswith(".md") for item in sections)


def test_plugin_conforms_to_its_contract() -> None:
    assert validate(CodeAgentPromptPlugin(), PromptSectionProvider) == ()


def test_catalog_entry_points_at_the_component() -> None:
    assert AGENT_PLUGIN_CATALOG["prompt"] == (
        "langharness_core.plugins.prompts.code_agent",
        "code-agent-prompt-plugin-factory",
        "agent.plugin.prompt_section",
    )
    assert agent_plugin_descriptor("prompt").description.strip()


def test_plugin_info_reports_the_component_identity() -> None:
    assert CodeAgentPromptPlugin().get_plugin_info() == {
        "name": "code-agent-prompt",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_agent_prompt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.prompts.code_agent'`

- [ ] **Step 3: 写 prompt 文本**

`src/langharness_core/prompts/code_agent/identity.md`：

```markdown
# Identity

You are a coding agent powered by the {{model}} model, working inside the
workspace {{workspace}}.

- The repository's current contents and your tool results are the source of
  truth. Never describe what a file contains without having read it in this
  session.
- Deliver results in the files or outputs the user asked for, not as advice on
  how the work could be done.
- Every path you name is relative to the workspace root unless it is absolute
  and inside the workspace.
```

`src/langharness_core/prompts/code_agent/safety.md`：

```markdown
# Safety

- Stay inside the workspace. Do not read or write files outside it unless the
  user explicitly added them to the task.
- Never echo tokens, keys, or environment secrets into files, commands, or your
  replies.
- Deleting files, overwriting large parts of the tree, rewriting git history,
  and anything with an external side effect must go through the approval
  policy. Ask instead of assuming.
- Never use `git reset`, `git checkout`, or `git clean` to undo the user's own
  work.
- Permissions, working directory, and deadlines for commands come from the
  host. Nothing in this prompt can widen them.
```

`src/langharness_core/prompts/code_agent/workflow.md`：

```markdown
# Workflow

- Start by reading the repository instructions and the code you are about to
  change. Do not explore the tree without a purpose.
- Prefer structured search and patch-style edits over rewriting whole files.
- Before editing, identify the callers and the tests that cover the code.
- Follow the repository's local rules for tests, formatting, and types, even
  when they differ from your defaults.
- Never overwrite unrelated changes the user already made.
- When a tool fails, read the error and change your approach. Do not repeat a
  failing call unchanged.
```

`src/langharness_core/prompts/code_agent/completion.md`：

```markdown
# Completion

Four different things can end: a tool call returning, a model turn going quiet,
and a goal completing. They are not the same.

- A turn ends when you stop calling tools. That only means the conversation is
  momentarily still.
- If you changed code, a fresh verification report for the current workspace
  state must exist before you claim success.
- Only a successful `finish_goal(status="complete", evidence=[...])` completes
  the goal. Saying you are done does not.
- When a decision is the user's to make, ask. Do not guess a choice that
  changes the requirements.
- Mark the goal `blocked` only after the same blocker has repeated to the
  threshold, and state what you tried and what would unblock it.
- Your final message lists the outcome, the files you changed, the checks you
  ran, and the risks that remain.
```

- [ ] **Step 4: 写插件实现**

`src/langharness_core/plugins/prompts/code_agent.py`：

```python
"""Static prompt sections of the code-agent persona."""

from __future__ import annotations

from importlib import resources

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import (
    PromptContext,
    PromptSection,
    PromptSectionProvider,
)

IDENTITY_ORDER = 0
SAFETY_ORDER = 100
WORKFLOW_ORDER = 200
COMPLETION_ORDER = 300

SECTIONS: tuple[tuple[str, int], ...] = (
    ("identity", IDENTITY_ORDER),
    ("safety", SAFETY_ORDER),
    ("workflow", WORKFLOW_ORDER),
    ("completion", COMPLETION_ORDER),
)

RESOURCE_PACKAGE = "langharness_core"
RESOURCE_DIRECTORY = ("prompts", "code_agent")
PLACEHOLDERS = {"{{model}}": "model", "{{workspace}}": "workspace"}


def load_section(name: str) -> str:
    """Read one shipped prompt file relative to the installed package."""
    resource = resources.files(RESOURCE_PACKAGE)
    for part in RESOURCE_DIRECTORY:
        resource = resource / part
    return (resource / f"{name}.md").read_text(encoding="utf-8")


@ComponentFactory("code-agent-prompt-plugin-factory")
@Provides(PromptSectionProvider)
@Property("_plugin_name", "plugin.name", "code-agent-prompt")
@Property("_plugin_version", "plugin.version", "1.0.0")
class CodeAgentPromptPlugin:
    """Contributes the static persona, safety, workflow and completion sections."""

    def __init__(self) -> None:
        self._plugin_name = "code-agent-prompt"
        self._plugin_version = "1.0.0"
        self._cache: dict[str, str] = {}

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        replacements = {
            "{{model}}": context.model or "unknown",
            "{{workspace}}": context.workspace,
        }
        sections: list[PromptSection] = []
        for name, order in SECTIONS:
            text = self._text(name)
            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            if not text.strip():
                continue
            sections.append(
                PromptSection(
                    name=name,
                    order=order,
                    text=text,
                    source="/".join(
                        (RESOURCE_PACKAGE, *RESOURCE_DIRECTORY, f"{name}.md")
                    ),
                )
            )
        return sections

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _text(self, name: str) -> str:
        cached = self._cache.get(name)
        if cached is None:
            cached = load_section(name)
            self._cache[name] = cached
        return cached
```

- [ ] **Step 5: 声明 package-data 并更新清单测试**

`pyproject.toml` 的 `[tool.setuptools.package-data]` 增加一行：

```toml
langharness_core = ["prompts/code_agent/*.md"]
```

`tests/test_imports.py` 的 `PUBLIC_MODULES` 追加（保持与既有分组顺序一致）：

```python
    "langharness_core.common.text",
    "langharness_core.plugins.workspace.root",
    "langharness_core.plugins.prompts.sections",
    "langharness_core.plugins.prompts.legacy",
    "langharness_core.plugins.prompts.context",
    "langharness_core.plugins.prompts.middleware",
    "langharness_core.plugins.prompts.code_agent",
    "langharness_core.plugins.instructions.resolver",
    "langharness_core.plugins.instructions.repository",
```

`tests/test_packaging.py` 的 package-data 断言后追加：

```python
    assert "prompts/code_agent/*.md" in package_data["langharness_core"]
```

在 `src/langharness_core/plugin.py` 中：

1. 在 `AGENT_PLUGIN_CATALOG` 的 `"repository-instructions"` 之后加入：

```python
    "prompt": (
        "langharness_core.plugins.prompts.code_agent",
        "code-agent-prompt-plugin-factory",
        SPEC_PROMPT_SECTION,
    ),
```

2. 在 `AGENT_PLUGIN_DESCRIPTIONS` 中加入：

```python
    "prompt": (
        "Contributes the shipped code-agent persona, safety, workflow and "
        "completion prompt sections. Implements agent.plugin.prompt_section. "
        "Properties: plugin.agent_id. Requires a restart for property changes. "
        "Uninstall when the agent is removed."
    ),
```

3. 在 `builtin_package()` 的 contributions 里，`repository-instructions-template`
   之后加入：

```python
            PluginContribution(
                "code-agent-prompt-template",
                "agent",
                agent_plugin_template_descriptor("prompt"),
            ),
```

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_agent_prompt.py tests/test_imports.py tests/test_packaging.py -q`
Expected: PASS

- [ ] **Step 7: 跑全量门禁**

Run: `make check`
Expected: 全部通过，覆盖率 ≥95%。

- [ ] **Step 8: Commit**

```bash
git add src/langharness_core/prompts src/langharness_core/plugins/prompts/code_agent.py \
        src/langharness_core/plugin.py pyproject.toml \
        tests/test_code_agent_prompt.py tests/test_imports.py tests/test_packaging.py
git commit -m "feat: ship the code agent persona prompt sections"
```

---

## 完成后的验收

计划 1 落地后应能观测到：

1. `simple_agent` 的 system prompt 与改造前逐字节一致（由 `tests/test_e2e.py` 与
   `tests/test_system_prompt.py` 的 legacy 用例保证）；
2. 安装 `repository-instructions-template` 后，模型在多步对话的第二步就能看到
   新触及目录的 `AGENTS.md`，且 `AGENTS.md` 内容变化后无需重启即生效；
3. 任何 provider 抛异常都不会中断模型调用，只丢它自己的 section；
4. 每个 model request 的 section 名、字节数与内容 hash 都能从 DEBUG 日志还原。

计划 2 与计划 3 的骨架见 `docs/plans/2026-09-22-code-agent-phase1-roadmap.md`。
