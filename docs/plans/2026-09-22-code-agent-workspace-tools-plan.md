# Code Agent 计划 2a：code workspace 工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 code agent 在一个固定 realpath 的 workspace 边界内读取、列举、搜索、打补丁，并让每一次写操作都能被后续的验证门禁引用（mutation generation）。

**Architecture:** 新增 `agent.plugin.workspace_state` 契约承载 mutation generation 与逐路径 hash 记录；新增 `CodeWorkspaceToolsPlugin`（`agent.plugin.tools`），它通过 `@RequiresBest` 取得 `WorkspaceProvider`（计划 1 已交付），用共享的 `resolve_workspace_path` 守卫每条路径，并在每次成功写入后调用 `WorkspaceStateProvider.record_mutation`。所有工具是同步 `StructuredTool`——已实测确认 LangChain 会把同步工具丢到线程池（`BaseTool._arun` → `run_in_executor`），阻塞不会卡住 API server 的事件循环。

**Tech Stack:** Python 3.13、Pelix/iPOPO、LangChain 1.4（`StructuredTool.from_function` + pydantic `args_schema`）、pytest。

## Global Constraints

- 质量门槛（AGENTS.md）：每个 commit 必须过 `make check`（Ruff → mypy → Pyright → clean-process import → pytest），单元覆盖率 ≥95%。**执行前基线是 95.69%，余量很薄**：新增模块必须写全分支测试。
- TDD：先写失败测试，再写最小实现；每个任务独立 commit。
- 生产代码 mypy strict、Pyright standard 不允许全局放宽。`tests/` **不是** mypy 豁免区（`tests.*` override 因缺少 `__init__.py` 从不匹配），新测试文件要么全量注解，要么在 line 2 加 `# mypy: ignore-errors`；需要时按既有惯例补 `# pyright: report...=false`。
- 插件间通信只走 Pelix service spec（Protocol）；`@Provides`/`@Requires` 声明 Protocol 类；禁止在 Protocol 类体内写 `__SPECIFICATION__`。
- `PluginDescriptor` 只在 `langharness_core/plugin.py` 构造；组件实现在 `plugins/<topic>/<name>.py`。
- 消费方在 `BindField` 回调里用 `ContractGuard` 守卫注入字段。
- **工具 schema 必须同时声明输入与 JSON 输出**，异常一律走 `tool_error()`，不得把 Python traceback 当字符串返回（设计第 6.1 节）。
- 文档用中文，代码/命令用英文；commit message 用 `feat:` / `test:` / `docs:`。
- 设计文档：`docs/designs/2026-09-22-code-agent-design.md` 第 6 节。
- 前置：计划 1 已合入本分支（`PromptSectionProvider`、`WorkspaceProvider`、`common/text.py` 的 `digest`/`truncate_bytes`）。

## 补丁格式（本计划冻结，计划 1 的 `touched_paths_from_messages` 已按同一组标记解析）

```text
*** Begin Patch
*** Update File: src/a.py
@@ def existing_function(
 context line
-removed line
+added line
*** Add File: src/b.py
+first line
*** Delete File: src/c.py
*** Move to: src/d.py
*** End Patch
```

规则：`Move to` 只出现在 `Update File` 段内表示重命名；`Add File` 段内所有行必须以 `+`
开头；上下文行以单个空格开头；`@@` 行可选，其后的文本是锚点，用于缩小搜索起点。

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `src/langharness_core/common/tool_error.py` | 统一的 `tool_error()` 构造器与 `ToolErrorCode` 词表 |
| `src/langharness_core/plugins/workspace/paths.py` | `resolve_workspace_path`：realpath 固定、目录穿越与 symlink escape 拒绝 |
| `src/langharness_core/plugins/workspace/state.py` | `WorkspaceStatePlugin`：mutation generation、写入记录、线程安全 |
| `src/langharness_core/plugins/tools/search.py` | 纯逻辑：workspace 文本搜索（纯 Python 主路径 + 可选 ripgrep 快路径） |
| `src/langharness_core/plugins/tools/patch.py` | 纯逻辑：补丁解析与原子应用 |
| `src/langharness_core/plugins/tools/code_workspace.py` | `CodeWorkspaceToolsPlugin`：把上面这些装配成 5 个 LangChain 工具 |
| `tests/test_tool_error.py`、`tests/test_workspace_paths.py`、`tests/test_workspace_state.py`、`tests/test_search_text.py`、`tests/test_apply_patch_parser.py`、`tests/test_code_workspace_tools.py` | 对应测试 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/langharness_core/contracts.py` | 新增 `SPEC_WORKSPACE_STATE`、`MutationRecord`、`WorkspaceStateProvider` |
| `src/langharness_core/plugin.py` | catalog 增加 `code-tools`；`builtin_package()` 增加 template |
| `src/langharness_core/plugins/instructions/resolver.py` | 用 `resolve_workspace_path` 取代自带的 `_directory_for` 包含性判断 |
| `tests/test_imports.py` | `PUBLIC_MODULES` 增加新模块 |

---

### Task 1: 统一错误形状与 workspace 路径守卫

**Files:**
- Create: `src/langharness_core/common/tool_error.py`
- Create: `src/langharness_core/plugins/workspace/paths.py`
- Modify: `src/langharness_core/plugins/instructions/resolver.py`
- Test: `tests/test_tool_error.py`、`tests/test_workspace_paths.py`（新建）

**Interfaces:**
- Produces（后续所有任务复用）：
  - `tool_error(code: ToolErrorCode, message: str, *, retryable: bool = False, **details: Any) -> dict[str, Any]`
  - `ToolErrorCode`：`Literal["invalid_arguments", "workspace_unavailable", "path_outside_workspace", "path_not_found", "not_a_file", "not_a_directory", "patch_invalid", "patch_conflict", "internal_error"]`
  - `paths.WorkspacePathError(code: str, message: str)`，属性 `.code` / `.message`
  - `paths.WorkspacePath(root: Path, absolute: Path, relative: str)`，属性 `is_dir` / `is_file`
  - `paths.resolve_workspace_path(root: str | Path, raw: str) -> WorkspacePath`
  - `paths.to_tool_error(exc: WorkspacePathError) -> dict[str, Any]`

**安全不变量：** `resolve_workspace_path` 必须对 `..` 穿越、绝对路径逃逸、以及**经 symlink 逃逸**的路径一律抛 `WorkspacePathError("path_outside_workspace")`。判断基于 `Path.resolve()` 之后的结果，而不是输入字符串。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tool_error.py`：

```python
"""Tests for the shared tool error shape."""

from __future__ import annotations

from langharness_core.common.tool_error import tool_error


def test_error_shape_is_stable() -> None:
    assert tool_error("path_not_found", "no such file") == {
        "error": {
            "code": "path_not_found",
            "message": "no such file",
            "retryable": False,
            "details": {},
        }
    }


def test_retryable_and_details_are_carried_through() -> None:
    result = tool_error(
        "patch_conflict", "context missing", retryable=True, path="src/a.py", line=12
    )
    assert result["error"]["retryable"] is True
    assert result["error"]["details"] == {"path": "src/a.py", "line": 12}
```

新建 `tests/test_workspace_paths.py`：

```python
"""Tests for the shared workspace path guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from langharness_core.plugins.workspace.paths import (
    WorkspacePath,
    WorkspacePathError,
    resolve_workspace_path,
    to_tool_error,
)


def test_relative_path_resolves_inside_the_workspace(tmp_path: Path) -> None:
    result = resolve_workspace_path(tmp_path, "src/a.py")
    assert isinstance(result, WorkspacePath)
    assert result.relative == "src/a.py"
    assert result.absolute == (tmp_path / "src" / "a.py").resolve()


def test_the_workspace_root_itself_resolves_to_dot(tmp_path: Path) -> None:
    assert resolve_workspace_path(tmp_path, ".").relative == "."
    assert resolve_workspace_path(tmp_path, "").relative == "."


def test_absolute_path_inside_the_workspace_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    assert resolve_workspace_path(tmp_path, str(target)).relative == "a.py"


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspacePathError) as caught:
        resolve_workspace_path(tmp_path, "../outside.py")
    assert caught.value.code == "path_outside_workspace"


def test_absolute_path_outside_the_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkspacePathError) as caught:
        resolve_workspace_path(tmp_path, "/etc/passwd")
    assert caught.value.code == "path_outside_workspace"


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspacePathError) as caught:
        resolve_workspace_path(tmp_path, "link/secret.py")
    assert caught.value.code == "path_outside_workspace"


def test_symlink_staying_inside_the_workspace_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    assert resolve_workspace_path(tmp_path, "link/a.py").relative == "real/a.py"


def test_missing_file_still_resolves(tmp_path: Path) -> None:
    result = resolve_workspace_path(tmp_path, "does/not/exist.py")
    assert result.relative == "does/not/exist.py"
    assert result.is_file is False


def test_is_dir_and_is_file_report_the_target(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "f").write_text("x", encoding="utf-8")
    assert resolve_workspace_path(tmp_path, "d").is_dir is True
    assert resolve_workspace_path(tmp_path, "f").is_file is True


def test_error_maps_to_the_shared_tool_error_shape() -> None:
    error = to_tool_error(WorkspacePathError("path_not_found", "gone"))
    assert error["error"]["code"] == "path_not_found"
    assert error["error"]["message"] == "gone"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_tool_error.py tests/test_workspace_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.common.tool_error'`

- [ ] **Step 3: 写实现**

`src/langharness_core/common/tool_error.py`：

```python
"""The single error shape every code tool returns.

A tool that fails must hand the model something it can act on: a stable code,
a readable message, whether retrying could help, and the structured details.
Python tracebacks are never a tool result — they are a bug in the tool.
"""

from __future__ import annotations

from typing import Any, Literal

ToolErrorCode = Literal[
    "invalid_arguments",
    "workspace_unavailable",
    "path_outside_workspace",
    "path_not_found",
    "not_a_file",
    "not_a_directory",
    "patch_invalid",
    "patch_conflict",
    "internal_error",
]


def tool_error(
    code: ToolErrorCode,
    message: str,
    *,
    retryable: bool = False,
    **details: Any,
) -> dict[str, Any]:
    """Build the ``{"error": {code, message, retryable, details}}`` result."""
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details,
        }
    }
```

`src/langharness_core/plugins/workspace/paths.py`：

```python
"""Workspace path resolution shared by every path-taking tool.

Containment is decided after ``Path.resolve()``, so a symlink pointing out of
the workspace is rejected exactly like a ``..`` traversal.  Checking the input
string instead would let either one through.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langharness_core.common.tool_error import ToolErrorCode, tool_error

OUTSIDE_WORKSPACE: ToolErrorCode = "path_outside_workspace"
PATH_NOT_FOUND: ToolErrorCode = "path_not_found"


class WorkspacePathError(Exception):
    """Raised when a path cannot be resolved inside the workspace."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        # Annotated: Pyright widens an unannotated attribute to ``str``, which
        # then does not satisfy ``tool_error``'s Literal parameter.
        self.code: ToolErrorCode = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class WorkspacePath:
    """A path proven to live inside the workspace."""

    root: Path
    absolute: Path
    relative: str

    @property
    def is_dir(self) -> bool:
        return self.absolute.is_dir()

    @property
    def is_file(self) -> bool:
        return self.absolute.is_file()


def resolve_workspace_path(root: str | Path, raw: str | None) -> WorkspacePath:
    """Resolve ``raw`` inside ``root``, rejecting any escape.

    A blank path means the workspace root, which is what a directory listing
    without arguments should show.
    """
    base = Path(root).resolve()
    text = (raw or "").strip()
    candidate = Path(text) if text else base
    absolute = candidate if candidate.is_absolute() else base / candidate
    try:
        resolved = absolute.resolve()
    except OSError as exc:
        raise WorkspacePathError(PATH_NOT_FOUND, f"cannot resolve {text!r}: {exc}") from exc
    try:
        relative = resolved.relative_to(base)
    except ValueError as exc:
        raise WorkspacePathError(
            OUTSIDE_WORKSPACE, f"{text or '.'!r} resolves outside the workspace"
        ) from exc
    return WorkspacePath(
        root=base,
        absolute=resolved,
        relative=relative.as_posix() if relative.parts else ".",
    )


def to_tool_error(exc: WorkspacePathError) -> dict[str, Any]:
    """Render a :class:`WorkspacePathError` as the shared tool error shape."""
    return tool_error(exc.code, exc.message)
```

- [ ] **Step 4: 让 instructions resolver 复用守卫**

在 `src/langharness_core/plugins/instructions/resolver.py` 里删掉 `_directory_for`
的包含性判断，改为调用共享守卫；行为保持不变（workspace 之外的路径返回 `None`）：

```python
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
)

    def _directory_for(self, raw: str) -> Path | None:
        """Resolve one touched path to a directory inside the workspace."""
        if not raw:
            return None
        try:
            resolved = resolve_workspace_path(self._root, raw).absolute
        except WorkspacePathError:
            return None
        if resolved.is_dir():
            return resolved
        return resolved.parent
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_tool_error.py tests/test_workspace_paths.py tests/test_instruction_resolver.py -q`
Expected: 全部 PASS（守卫测试 12 个，resolver 的 15 个保持通过 —— 这一步是行为等价的替换）

- [ ] **Step 6: 跑全量门禁**

Run: `make check`
Expected: 全部通过，覆盖率不低于 95.69%。

- [ ] **Step 7: Commit**

```bash
git add src/langharness_core/common/tool_error.py src/langharness_core/plugins/workspace/paths.py \
        src/langharness_core/plugins/instructions/resolver.py \
        tests/test_tool_error.py tests/test_workspace_paths.py
git commit -m "feat: add the shared tool error shape and workspace path guard"
```

---

### Task 2: Mutation generation 契约与实现

**Files:**
- Modify: `src/langharness_core/contracts.py`
- Create: `src/langharness_core/plugins/workspace/state.py`
- Test: `tests/test_workspace_state.py`（新建）

**Interfaces:**
- Consumes: Task 1 无依赖；`ContractGuard`（已有）。
- Produces（Task 6、计划 2b、计划 3 复用）：
  - `SPEC_WORKSPACE_STATE = "agent.plugin.workspace_state"`
  - `MutationRecord(path: str, generation: int, tool: str, before_sha256: str | None, after_sha256: str | None)` —— frozen dataclass，`slots=True`
  - `WorkspaceStateProvider` Protocol：`get_generation() -> int`、`record_mutation(path, tool, *, before_sha256=None, after_sha256=None) -> int`、`mutations(since: int = 0) -> list[MutationRecord]`、`get_plugin_info() -> dict[str, str]`
  - 工厂 `workspace-state-plugin-factory`，模块 `langharness_core.plugins.workspace.state`，属性 `plugin.workspace.root_dir`

**为什么需要线程锁：** 工具在 LangChain 的线程池里跑（已实测），两个并发写工具会同时改 generation。用 `threading.Lock`，不是 `asyncio.Lock`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_workspace_state.py`：

```python
"""Tests for the workspace mutation-generation service."""
# mypy: ignore-errors

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_WORKSPACE_STATE,
    MutationRecord,
    WorkspaceStateProvider,
)
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import contract_for, validate


def test_specification_is_pinned_to_the_protocol() -> None:
    assert contract_for(SPEC_WORKSPACE_STATE) is WorkspaceStateProvider
    assert SPEC_WORKSPACE_STATE in ALL_PLUGIN_SPECS


def test_plugin_conforms_to_its_contract() -> None:
    assert validate(WorkspaceStatePlugin(), WorkspaceStateProvider) == ()


def test_generation_starts_at_zero() -> None:
    assert WorkspaceStatePlugin().get_generation() == 0


def test_every_mutation_advances_the_generation() -> None:
    plugin = WorkspaceStatePlugin()
    assert plugin.record_mutation("a.py", "apply_patch") == 1
    assert plugin.record_mutation("b.py", "apply_patch") == 2
    assert plugin.get_generation() == 2


def test_records_carry_hashes_and_tool_name() -> None:
    plugin = WorkspaceStatePlugin()
    plugin.record_mutation(
        "a.py", "apply_patch", before_sha256="sha256:old", after_sha256="sha256:new"
    )
    assert plugin.mutations() == [
        MutationRecord(
            path="a.py",
            generation=1,
            tool="apply_patch",
            before_sha256="sha256:old",
            after_sha256="sha256:new",
        )
    ]


def test_mutations_can_be_read_since_a_generation() -> None:
    plugin = WorkspaceStatePlugin()
    plugin.record_mutation("a.py", "apply_patch")
    plugin.record_mutation("b.py", "apply_patch")
    assert [item.path for item in plugin.mutations(since=1)] == ["b.py"]


def test_crossing_ten_generations_is_detected_by_comparison_not_parity() -> None:
    plugin = WorkspaceStatePlugin()
    for _ in range(11):
        plugin.record_mutation("a.py", "apply_patch")
    # A stale report recorded at generation 1 must not look fresh at 11.
    assert plugin.get_generation() == 11


def test_concurrent_mutations_never_lose_a_generation() -> None:
    plugin = WorkspaceStatePlugin()
    with ThreadPoolExecutor(max_workers=8) as pool:
        generations = list(
            pool.map(lambda index: plugin.record_mutation(f"{index}.py", "apply_patch"), range(64))
        )
    assert sorted(generations) == list(range(1, 65))
    assert plugin.get_generation() == 64
    assert len(plugin.mutations()) == 64


def test_plugin_info_reports_the_component_identity() -> None:
    assert WorkspaceStatePlugin().get_plugin_info() == {
        "name": "workspace-state",
        "version": "1.0.0",
    }
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_workspace_state.py -q`
Expected: FAIL — `ImportError: cannot import name 'SPEC_WORKSPACE_STATE'`

- [ ] **Step 3: 写实现**

在 `src/langharness_core/contracts.py` 中，`SPEC_WORKSPACE` 之后加：

```python
SPEC_WORKSPACE_STATE = "agent.plugin.workspace_state"
```

把 `SPEC_WORKSPACE_STATE` 追加进 `ALL_PLUGIN_SPECS`（紧跟 `SPEC_WORKSPACE`）。

在 `PromptContext` 之后加值类型：

```python
@dataclass(frozen=True, slots=True)
class MutationRecord:
    """One write this agent made, at the generation it produced.

    ``before_sha256`` is ``None`` for a created file and ``after_sha256`` is
    ``None`` for a deleted one, so a report can tell creation from deletion
    without a separate flag.
    """

    path: str
    generation: int
    tool: str
    before_sha256: str | None
    after_sha256: str | None
```

在 `WorkspaceProvider` 之后加 Protocol：

```python
@service_contract(SPEC_WORKSPACE_STATE)
@runtime_checkable
class WorkspaceStateProvider(Protocol):
    """Contract implemented by every ``agent.plugin.workspace_state`` service."""

    def get_generation(self) -> int: ...

    def record_mutation(
        self,
        path: str,
        tool: str,
        *,
        before_sha256: str | None = None,
        after_sha256: str | None = None,
    ) -> int: ...

    def mutations(self, since: int = 0) -> list[MutationRecord]: ...

    def get_plugin_info(self) -> dict[str, str]: ...
```

`src/langharness_core/plugins/workspace/state.py`：

```python
"""Workspace mutation generations: what a verification report is valid for."""

from __future__ import annotations

import threading

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import MutationRecord, WorkspaceStateProvider


@ComponentFactory("workspace-state-plugin-factory")
@Provides(WorkspaceStateProvider)
@Property("_plugin_name", "plugin.name", "workspace-state")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_root", "plugin.workspace.root_dir", ".")
class WorkspaceStatePlugin:
    """Counts workspace writes so stale verification can be detected.

    Tools run in LangChain's thread pool, so the counter is guarded by a
    threading lock rather than an asyncio one: two concurrent writes must not
    collapse into one generation.
    """

    def __init__(self) -> None:
        self._plugin_name = "workspace-state"
        self._plugin_version = "1.0.0"
        self._root = "."
        self._lock = threading.Lock()
        self._generation = 0
        self._records: list[MutationRecord] = []

    def get_generation(self) -> int:
        with self._lock:
            return self._generation

    def record_mutation(
        self,
        path: str,
        tool: str,
        *,
        before_sha256: str | None = None,
        after_sha256: str | None = None,
    ) -> int:
        with self._lock:
            self._generation += 1
            self._records.append(
                MutationRecord(
                    path=path,
                    generation=self._generation,
                    tool=tool,
                    before_sha256=before_sha256,
                    after_sha256=after_sha256,
                )
            )
            return self._generation

    def mutations(self, since: int = 0) -> list[MutationRecord]:
        with self._lock:
            return [item for item in self._records if item.generation > since]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_workspace_state.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/contracts.py src/langharness_core/plugins/workspace/state.py \
        tests/test_workspace_state.py
git commit -m "feat: track workspace mutation generations"
```

---

### Task 3: `list_directory` 与 `read_file`

**Files:**
- Create: `src/langharness_core/plugins/tools/code_workspace.py`
- Test: `tests/test_code_workspace_tools.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `resolve_workspace_path` / `to_tool_error` / `tool_error`；Task 2 的 `WorkspaceStateProvider`；计划 1 的 `WorkspaceProvider`、`common/text.py` 的 `digest`。
- Produces（Task 4/6/7 继续往同一组件加工具）：
  - 工厂 `code-workspace-tools-plugin-factory`，模块 `langharness_core.plugins.tools.code_workspace`
  - 组件 `CodeWorkspaceToolsPlugin`，`@Provides(ToolProvider)`，`@RequiresBest` 两个服务，`@ScopedDependencies("_workspace", "_state")`
  - 无 `WorkspaceProvider` 时 `get_tools()` 返回 `[]`（与 `ManagementToolsPlugin` 一致：宁可没有工具，也不要在错误的目录上写文件）
  - `DEFAULT_MAX_READ_BYTES = 131_072`、`DEFAULT_MAX_ENTRIES = 500`

**工具返回形状**（写进 tool description，模型据此解析）：
- `list_directory` → `{"path", "entries": [{"name", "path", "type", "size"}], "truncated"}`
- `read_file` → `{"path", "text", "start_line", "end_line", "total_lines", "sha256", "truncated"}`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_code_workspace_tools.py`：

```python
"""Tests for the code workspace reading tools."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.plugins.tools.code_workspace import CodeWorkspaceToolsPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def plugin(workspace: Path) -> CodeWorkspaceToolsPlugin:
    instance = CodeWorkspaceToolsPlugin()
    root = WorkspaceRootPlugin()
    root._root = str(workspace)
    instance._workspace = root
    instance._state = WorkspaceStatePlugin()
    return instance


def tools(workspace: Path) -> dict[str, object]:
    return {item.name: item for item in plugin(workspace).get_tools()}


def test_no_workspace_provider_means_no_tools() -> None:
    assert CodeWorkspaceToolsPlugin().get_tools() == []


def test_tool_names_are_stable(tmp_path: Path) -> None:
    # Grows by one name per task: search_text (Task 4), apply_patch (Task 6),
    # workspace_status (Task 7).
    assert set(tools(tmp_path)) == {"list_directory", "read_file"}


def test_list_directory_reports_entries_and_types(tmp_path: Path) -> None:
    write(tmp_path / "src" / "a.py", "print('a')\n")
    (tmp_path / "src" / "sub").mkdir()
    result = tools(tmp_path)["list_directory"].invoke({"path": "src"})
    assert result["path"] == "src"
    names = {entry["name"]: entry["type"] for entry in result["entries"]}
    assert names == {"a.py": "file", "sub": "directory"}
    assert result["truncated"] is False


def test_list_directory_defaults_to_the_workspace_root(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x")
    result = tools(tmp_path)["list_directory"].invoke({})
    assert result["path"] == "."
    assert [entry["name"] for entry in result["entries"]] == ["a.py"]


def test_list_directory_truncates_and_says_so(tmp_path: Path) -> None:
    for index in range(5):
        write(tmp_path / f"f{index}.py", "x")
    instance = plugin(tmp_path)
    instance._max_entries = 2
    result = {item.name: item for item in instance.get_tools()}["list_directory"].invoke({})
    assert len(result["entries"]) == 2
    assert result["truncated"] is True


def test_list_directory_rejects_a_file(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x")
    result = tools(tmp_path)["list_directory"].invoke({"path": "a.py"})
    assert result["error"]["code"] == "not_a_directory"


def test_list_directory_rejects_an_escape(tmp_path: Path) -> None:
    result = tools(tmp_path)["list_directory"].invoke({"path": "../.."})
    assert result["error"]["code"] == "path_outside_workspace"


def test_read_file_returns_text_lines_and_hash(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "one\ntwo\nthree\n")
    result = tools(tmp_path)["read_file"].invoke({"path": "a.py"})
    assert result["text"] == "one\ntwo\nthree\n"
    assert result["start_line"] == 1
    assert result["end_line"] == 3
    assert result["total_lines"] == 3
    assert result["sha256"].startswith("sha256:")
    assert result["truncated"] is False


def test_read_file_honours_offset_and_limit(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "".join(f"line{i}\n" for i in range(10)))
    result = tools(tmp_path)["read_file"].invoke({"path": "a.py", "offset": 3, "limit": 2})
    assert result["text"] == "line2\nline3\n"
    assert result["start_line"] == 3
    assert result["end_line"] == 4


def test_read_file_truncates_oversized_content(tmp_path: Path) -> None:
    write(tmp_path / "big.txt", "x" * 5000)
    instance = plugin(tmp_path)
    instance._max_read_bytes = 500
    result = {item.name: item for item in instance.get_tools()}["read_file"].invoke(
        {"path": "big.txt"}
    )
    assert result["truncated"] is True
    assert len(result["text"].encode("utf-8")) <= 500


def test_read_file_rejects_a_directory(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    result = tools(tmp_path)["read_file"].invoke({"path": "d"})
    assert result["error"]["code"] == "not_a_file"


def test_read_file_reports_a_missing_file(tmp_path: Path) -> None:
    result = tools(tmp_path)["read_file"].invoke({"path": "nope.py"})
    assert result["error"]["code"] == "path_not_found"


def test_read_file_refuses_binary_content(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    result = tools(tmp_path)["read_file"].invoke({"path": "blob.bin"})
    assert result["error"]["code"] == "not_a_file"
    assert "binary" in result["error"]["message"]


def test_presentations_name_only_arguments_the_tools_take(tmp_path: Path) -> None:
    presentations = plugin(tmp_path).get_tool_presentations()
    assert presentations["list_directory"] == "list_directory({path})"
    assert presentations["read_file"] == "read_file({path})"
    # apply_patch is deliberately absent: rendering the whole patch inline
    # would flood the transcript, and absent means "show the bare name".
    assert "apply_patch" not in presentations
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_tools.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.tools.code_workspace'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/tools/code_workspace.py`（本任务只放 `list_directory` 与
`read_file`，Task 4/6/7 继续往里加）：

```python
"""Code-agent workspace tools: bounded reads, search and patch editing."""

from __future__ import annotations

from pathlib import Path
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

from langharness_core.common.text import digest, truncate_bytes
from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import (
    ToolProvider,
    WorkspaceProvider,
    WorkspaceStateProvider,
)
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
    to_tool_error,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

DEFAULT_MAX_READ_BYTES = 131_072
DEFAULT_MAX_ENTRIES = 500
BINARY_SNIFF_BYTES = 8_192


class ListDirectoryArgs(BaseModel):
    path: str = Field(
        default=".",
        description="Workspace-relative directory. Defaults to the workspace root.",
    )


class ReadFileArgs(BaseModel):
    path: str = Field(description="Workspace-relative file path.")
    offset: int = Field(
        default=1, ge=1, description="1-based first line to return."
    )
    limit: int = Field(
        default=0,
        ge=0,
        description="Maximum lines to return. 0 means every line from offset.",
    )


def _entry_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _looks_binary(raw: bytes) -> bool:
    """A NUL byte in the first block is the cheap, reliable binary tell."""
    return b"\x00" in raw[:BINARY_SNIFF_BYTES]


@ComponentFactory("code-workspace-tools-plugin-factory")
@Provides(ToolProvider)
@Property("_plugin_name", "plugin.name", "code-workspace-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_max_read_bytes", "plugin.tools.max_read_bytes", DEFAULT_MAX_READ_BYTES)
@Property("_max_entries", "plugin.tools.max_entries", DEFAULT_MAX_ENTRIES)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@RequiresBest("_state", WorkspaceStateProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_workspace", "_state")
class CodeWorkspaceToolsPlugin:
    """Reads and edits files inside one pinned workspace.

    Without a workspace provider this plugin offers no tools at all: guessing a
    directory would mean editing whatever the process happens to sit in.
    """

    def __init__(self) -> None:
        self._plugin_name = "code-workspace-tools"
        self._plugin_version = "1.0.0"
        self._max_read_bytes = DEFAULT_MAX_READ_BYTES
        self._max_entries = DEFAULT_MAX_ENTRIES
        self._workspace: Any = None
        self._state: Any = None
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

    def get_tools(self) -> list[Any]:
        # Task 4/6/7 append search_text, apply_patch and workspace_status here.
        if self._workspace is None:
            return []
        return [
            self._list_tool(),
            self._read_tool(),
        ]

    def get_tool_presentations(self) -> dict[str, str]:
        """Headlines for the CLI transcript.

        ``apply_patch`` is deliberately missing: its argument is a whole patch
        document, and the renderer's absent-means-bare-name rule gives a better
        transcript than inlining it.
        """
        return {
            "list_directory": "list_directory({path})",
            "read_file": "read_file({path})",
            "search_text": "search_text({query})",
            "workspace_status": "workspace_status()",
        }

    def _root(self) -> Path:
        return Path(self._workspace.get_workspace())

    def _resolve(self, raw: str | None) -> Any:
        return resolve_workspace_path(self._root(), raw)

    def _list_tool(self) -> StructuredTool:
        plugin = self

        def list_directory(path: str = ".") -> dict[str, Any]:
            try:
                target = plugin._resolve(path)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            if not target.is_dir:
                return tool_error(
                    "not_a_directory", f"{target.relative!r} is not a directory"
                )
            entries: list[dict[str, Any]] = []
            truncated = False
            for child in sorted(
                target.absolute.iterdir(), key=lambda item: item.name
            ):
                if len(entries) >= plugin._max_entries:
                    truncated = True
                    break
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                entries.append(
                    {
                        "name": child.name,
                        "path": f"{target.relative}/{child.name}"
                        if target.relative != "."
                        else child.name,
                        "type": _entry_type(child),
                        "size": size,
                    }
                )
            return {"path": target.relative, "entries": entries, "truncated": truncated}

        return StructuredTool.from_function(
            func=list_directory,
            name="list_directory",
            description=(
                "List one workspace directory, one level deep. Use it to learn "
                "what exists before reading or editing, instead of guessing "
                "paths. Entries are sorted by name and capped, so check "
                "'truncated' before concluding a directory holds nothing else. "
                "Returns {\"path\": str, \"entries\": [{\"name\": str, "
                "\"path\": str (workspace-relative), \"type\": "
                "'file'|'directory'|'symlink'|'other', \"size\": int}], "
                "\"truncated\": bool}. Returns {\"error\": {code, message, "
                "retryable, details}} when the path leaves the workspace or is "
                "not a directory."
            ),
            args_schema=ListDirectoryArgs,
        )

    def _read_tool(self) -> StructuredTool:
        plugin = self

        def read_file(path: str, offset: int = 1, limit: int = 0) -> dict[str, Any]:
            try:
                target = plugin._resolve(path)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            if not target.is_file:
                return tool_error("not_a_file", f"{target.relative!r} is not a file")
            try:
                raw = target.absolute.read_bytes()
            except OSError as exc:
                return tool_error("path_not_found", f"cannot read {target.relative!r}: {exc}")
            if _looks_binary(raw):
                return tool_error(
                    "not_a_file",
                    f"{target.relative!r} looks like binary content",
                )
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            start = max(offset, 1) - 1
            selected = lines[start:] if limit <= 0 else lines[start : start + limit]
            body = "".join(selected)
            body, omitted = truncate_bytes(body, plugin._max_read_bytes)
            return {
                "path": target.relative,
                "text": body,
                "start_line": start + 1,
                "end_line": start + len(selected),
                "total_lines": len(lines),
                "sha256": digest(text),
                "truncated": omitted > 0,
            }

        return StructuredTool.from_function(
            func=read_file,
            name="read_file",
            description=(
                "Read one workspace file as text. Read before you edit: the "
                "source of truth is the file's current content, not your "
                "recollection of it. Use offset/limit for large files, and "
                "always send the 'sha256' back when reporting what you changed "
                "so the edit can be tied to a revision. Content is truncated "
                "at a byte budget; check 'truncated'. Binary files are "
                "rejected. Returns {\"path\": str, \"text\": str, "
                "\"start_line\": int, \"end_line\": int, \"total_lines\": int, "
                "\"sha256\": str, \"truncated\": bool}, or {\"error\": {code, "
                "message, retryable, details}}."
            ),
            args_schema=ReadFileArgs,
        )

```

`get_tools()` 此刻只返回 `list_directory` 与 `read_file`。**不要**为尚未实现的工具
留占位方法：一个会抛 `NotImplementedError` 的工具一旦被 catalog 装上就是坏的工具。
Task 4/6/7 各自往 `get_tools()` 的列表里追加一个名字，并同步更新
`test_tool_names_are_stable`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_tools.py -q`
Expected: PASS（13 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/tools/code_workspace.py tests/test_code_workspace_tools.py
git commit -m "feat: add workspace listing and reading tools"
```

---

### Task 4: `search_text`

**Files:**
- Create: `src/langharness_core/plugins/tools/search.py`
- Modify: `src/langharness_core/plugins/tools/code_workspace.py`
- Test: `tests/test_search_text.py`（新建）

**Interfaces:**
- Consumes: Task 1 的守卫；`common/text.py`。
- Produces（Task 6/7 与计划 2b 复用）：
  - `search.DEFAULT_IGNORED_DIRECTORIES: frozenset[str]` = `{".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".codegraph"}`
  - `search.DEFAULT_MAX_RESULTS = 200`、`search.DEFAULT_MAX_FILE_BYTES = 2_097_152`
  - `search.SearchMatch(path: str, line: int, column: int, text: str)`
  - `search.SearchOutcome(matches: list[SearchMatch], files_scanned: int, truncated: bool, engine: str)`
  - `search.find_ripgrep() -> str | None` —— 返回可用的 ripgrep 路径，没有则 `None`
  - `search.search_workspace(root: Path, query: str, *, glob: str | None = None, max_results: int = DEFAULT_MAX_RESULTS, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES, ripgrep: str | None = None) -> SearchOutcome`

**为什么纯 Python 是主路径：** 本机 `shutil.which("rg")` 返回 `None`（shell 里的 `rg`
是 Claude Code 的包装函数）。ripgrep 只在真的存在时作为快路径，两条路径都要有测试。

**语义（写进 tool description）：**
- `query` 是 Python 正则；无法编译时返回 `invalid_arguments`；
- 逐行匹配，`column` 是 1-based 的字符位置；
- 跳过忽略目录、大于 `max_file_bytes` 的文件、以及含 NUL 的二进制文件；
- 达到 `max_results` 立即停止并置 `truncated=True`；
- 结果按 `(path, line, column)` 排序，两条路径产出一致顺序。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_search_text.py`：

```python
"""Tests for workspace text search."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from langharness_core.plugins.tools import search


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_finds_matches_with_line_and_column(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "alpha\nbeta needle\ngamma\n")
    outcome = search.search_workspace(tmp_path, "needle")
    assert outcome.matches == [
        search.SearchMatch(path="a.py", line=2, column=6, text="beta needle")
    ]
    assert outcome.engine == "python"
    assert outcome.truncated is False


def test_results_are_sorted_by_path_then_position(tmp_path: Path) -> None:
    write(tmp_path / "b.py", "hit\n")
    write(tmp_path / "a.py", "hit\nhit\n")
    outcome = search.search_workspace(tmp_path, "hit")
    assert [(m.path, m.line) for m in outcome.matches] == [
        ("a.py", 1),
        ("a.py", 2),
        ("b.py", 1),
    ]


def test_glob_filters_files(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "hit\n")
    write(tmp_path / "a.md", "hit\n")
    outcome = search.search_workspace(tmp_path, "hit", glob="*.md")
    assert [m.path for m in outcome.matches] == ["a.md"]


def test_ignored_directories_are_skipped(tmp_path: Path) -> None:
    write(tmp_path / "src" / "a.py", "hit\n")
    write(tmp_path / ".git" / "config", "hit\n")
    write(tmp_path / "node_modules" / "pkg" / "i.js", "hit\n")
    outcome = search.search_workspace(tmp_path, "hit")
    assert [m.path for m in outcome.matches] == ["src/a.py"]


def test_binary_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"hit\x00hit")
    write(tmp_path / "a.py", "hit\n")
    outcome = search.search_workspace(tmp_path, "hit")
    assert [m.path for m in outcome.matches] == ["a.py"]


def test_oversized_files_are_skipped(tmp_path: Path) -> None:
    write(tmp_path / "big.txt", "hit\n" * 5000)
    write(tmp_path / "a.py", "hit\n")
    outcome = search.search_workspace(tmp_path, "hit", max_file_bytes=100)
    assert [m.path for m in outcome.matches] == ["a.py"]


def test_result_cap_sets_truncated(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "hit\n" * 10)
    outcome = search.search_workspace(tmp_path, "hit", max_results=3)
    assert len(outcome.matches) == 3
    assert outcome.truncated is True


def test_invalid_regex_is_reported(tmp_path: Path) -> None:
    with pytest.raises(search.SearchPatternError) as caught:
        search.search_workspace(tmp_path, "(")
    assert "invalid regular expression" in str(caught.value)


def test_files_scanned_counts_only_candidates(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x\n")
    write(tmp_path / "b.py", "x\n")
    write(tmp_path / ".git" / "c", "x\n")
    assert search.search_workspace(tmp_path, "x").files_scanned == 2


def fake_ripgrep(directory: Path, body: str) -> str:
    """A stand-in rg that prints pre-baked ripgrep output."""
    script = directory / "fake-rg"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_ripgrep_output_is_parsed_when_the_binary_runs(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "needle here\n")
    binary = fake_ripgrep(tmp_path, "printf './a.py:1:1:needle here\\n'")
    outcome = search.search_workspace(tmp_path, "needle", ripgrep=binary)
    assert outcome.engine == "ripgrep"
    assert outcome.matches == [
        search.SearchMatch(path="a.py", line=1, column=1, text="needle here")
    ]


def test_a_missing_ripgrep_binary_falls_back_to_python(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "needle\n")
    outcome = search.search_workspace(
        tmp_path, "needle", ripgrep="/nonexistent/rg-does-not-exist"
    )
    assert outcome.engine == "python"
    assert [m.path for m in outcome.matches] == ["a.py"]


def test_a_failing_ripgrep_exit_still_returns_its_matches(tmp_path: Path) -> None:
    # rg exits 1 when it finds nothing; that is a result, not a failure.
    write(tmp_path / "a.py", "x\n")
    binary = fake_ripgrep(tmp_path, "exit 1")
    outcome = search.search_workspace(tmp_path, "needle", ripgrep=binary)
    assert outcome.engine == "ripgrep"
    assert outcome.matches == []


def test_ripgrep_absent_falls_back_to_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ripgrep=None means "auto-detect"; pin the detection so this test does not
    # change meaning on a machine that happens to have ripgrep installed.
    write(tmp_path / "a.py", "needle\n")
    monkeypatch.setattr(search, "find_ripgrep", lambda: None)
    outcome = search.search_workspace(tmp_path, "needle", ripgrep=None)
    assert outcome.engine == "python"
    assert [m.path for m in outcome.matches] == ["a.py"]


def test_find_ripgrep_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(search.shutil, "which", lambda name: None)
    assert search.find_ripgrep() is None


def test_find_ripgrep_returns_the_resolved_path(monkeypatch) -> None:
    monkeypatch.setattr(search.shutil, "which", lambda name: f"/opt/bin/{name}")
    assert search.find_ripgrep() == "/opt/bin/rg"


def test_regex_metacharacters_are_honoured(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "def foo():\ndef bar():\n")
    outcome = search.search_workspace(tmp_path, r"^def \w+\(")
    assert len(outcome.matches) == 2
    assert re.match(r"^def ", outcome.matches[0].text)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_search_text.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.tools.search'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/tools/search.py`：

```python
"""Workspace text search.

The pure-Python walk is the main path, not a fallback: a machine without a
real ``rg`` binary is the normal case, so both engines return the same shape
and the same ordering.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".codegraph",
    }
)
DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_FILE_BYTES = 2_097_152
BINARY_SNIFF_BYTES = 8_192


class SearchPatternError(ValueError):
    """Raised when the query is not a usable regular expression."""


@dataclass(frozen=True, slots=True)
class SearchMatch:
    path: str
    line: int
    column: int
    text: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    matches: list[SearchMatch]
    files_scanned: int
    truncated: bool
    engine: str


def find_ripgrep() -> str | None:
    """Return the ripgrep binary path, or None when it is not installed."""
    return shutil.which("rg")


def _candidates(root: Path, glob: str | None) -> list[Path]:
    """Files to search, skipping ignored directories.

    The ignore check runs on workspace-relative parts: an absolute path whose
    own ancestors happen to be called ``build`` or ``dist`` must not silently
    exclude the whole workspace.
    """
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in DEFAULT_IGNORED_DIRECTORIES for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if glob and not fnmatch.fnmatch(path.name, glob):
            continue
        found.append(path)
    return found


def _matches_in(
    root: Path, path: Path, pattern: re.Pattern[str], max_file_bytes: int
) -> list[SearchMatch]:
    try:
        if path.stat().st_size > max_file_bytes:
            return []
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        return []
    relative = path.relative_to(root).as_posix()
    text = raw.decode("utf-8", errors="replace")
    found: list[SearchMatch] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for hit in pattern.finditer(line):
            found.append(
                SearchMatch(
                    path=relative,
                    line=number,
                    column=hit.start() + 1,
                    text=line,
                )
            )
    return found


def _search_with_ripgrep(
    root: Path, query: str, glob: str | None, binary: str
) -> tuple[list[SearchMatch], int]:
    command = [binary, "--line-number", "--column", "--no-heading", "--color=never"]
    if glob:
        command += ["--glob", glob]
    for ignored in sorted(DEFAULT_IGNORED_DIRECTORIES):
        command += ["--glob", f"!**/{ignored}/**"]
    command += [query, "."]
    completed = subprocess.run(
        command, cwd=root, capture_output=True, text=True, check=False
    )
    matches: list[SearchMatch] = []
    files: set[str] = set()
    for raw_line in completed.stdout.splitlines():
        parts = raw_line.split(":", 3)
        if len(parts) != 4:
            continue
        raw_path, raw_number, raw_column, text = parts
        relative = Path(raw_path).as_posix()
        if relative.startswith("./"):
            relative = relative[2:]
        files.add(relative)
        try:
            line_number = int(raw_number)
            column = int(raw_column)
        except ValueError:
            continue
        matches.append(
            SearchMatch(path=relative, line=line_number, column=column, text=text)
        )
    return matches, len(files)


def search_workspace(
    root: Path,
    query: str,
    *,
    glob: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ripgrep: str | None = None,
) -> SearchOutcome:
    """Search ``root`` for ``query``, returning a capped, ordered result set."""
    try:
        pattern = re.compile(query)
    except re.error as exc:
        raise SearchPatternError(f"invalid regular expression {query!r}: {exc}") from exc
    binary = ripgrep or find_ripgrep()
    if binary:
        try:
            found, scanned = _search_with_ripgrep(root, query, glob, binary)
        except OSError:
            # A binary that cannot be executed is not an error the model can
            # act on; the pure-Python path answers the same question.
            pass
        else:
            found.sort(key=lambda item: (item.path, item.line, item.column))
            return SearchOutcome(
                matches=found[:max_results],
                files_scanned=scanned,
                truncated=len(found) > max_results,
                engine="ripgrep",
            )
    matches: list[SearchMatch] = []
    scanned = 0
    truncated = False
    for path in _candidates(root, glob):
        scanned += 1
        for match in _matches_in(root, path, pattern, max_file_bytes):
            if len(matches) >= max_results:
                truncated = True
                break
            matches.append(match)
        if truncated:
            break
    matches.sort(key=lambda item: (item.path, item.line, item.column))
    return SearchOutcome(
        matches=matches, files_scanned=scanned, truncated=truncated, engine="python"
    )
```

在 `code_workspace.py` 中把 `_search_tool` 换成：

```python
class SearchTextArgs(BaseModel):
    query: str = Field(min_length=1, description="Python regular expression to find.")
    glob: str | None = Field(
        default=None, description="Optional filename glob, e.g. '*.py'."
    )


    def _search_tool(self) -> StructuredTool:
        plugin = self

        def search_text(query: str, glob: str | None = None) -> dict[str, Any]:
            try:
                outcome = search_module.search_workspace(
                    plugin._root(), query, glob=glob
                )
            except search_module.SearchPatternError as exc:
                return tool_error("invalid_arguments", str(exc))
            return {
                "query": query,
                "matches": [
                    {
                        "path": item.path,
                        "line": item.line,
                        "column": item.column,
                        "text": item.text,
                    }
                    for item in outcome.matches
                ],
                "files_scanned": outcome.files_scanned,
                "truncated": outcome.truncated,
                "engine": outcome.engine,
            }

        return StructuredTool.from_function(
            func=search_text,
            name="search_text",
            description=(
                "Search file contents across the workspace with a Python "
                "regular expression. Use it to locate call sites, definitions "
                "and configuration instead of walking directories by hand. "
                "Common vendored and cache directories (.git, .venv, "
                "node_modules, __pycache__, dist, build) are skipped, as are "
                "binary and oversized files, so a zero-result search means "
                "'not found in searchable files', not 'not anywhere'. Results "
                "are capped; check 'truncated' before concluding there are no "
                "more. Returns {\"query\": str, \"matches\": [{\"path\": str, "
                "\"line\": int, \"column\": int, \"text\": str}], "
                "\"files_scanned\": int, \"truncated\": bool, \"engine\": "
                "'python'|'ripgrep'}, or {\"error\": {code, message, "
                "retryable, details}} when the query is not a valid regex."
            ),
            args_schema=SearchTextArgs,
        )
```

并在文件顶部加 `from langharness_core.plugins.tools import search as search_module`。

同时把 `search_text` 加进 `get_tools()` 与 `get_tool_presentations()`，并把
`test_tool_names_are_stable` 的期望集合改为 `{"list_directory", "read_file",
"search_text"}`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_search_text.py tests/test_code_workspace_tools.py -q`
Expected: PASS（17 + 14 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/tools/search.py src/langharness_core/plugins/tools/code_workspace.py \
        tests/test_search_text.py
git commit -m "feat: add workspace text search with a python engine and a ripgrep fast path"
```

---

### Task 5: 补丁解析器（纯逻辑）

**Files:**
- Create: `src/langharness_core/plugins/tools/patch.py`
- Test: `tests/test_apply_patch_parser.py`（新建）

**Interfaces:**
- Produces（Task 6 复用）：
  - `patch.BEGIN_MARKER = "*** Begin Patch"`、`END_MARKER = "*** End Patch"`
  - `patch.UPDATE_MARKER = "*** Update File: "`、`ADD_MARKER = "*** Add File: "`、`DELETE_MARKER = "*** Delete File: "`、`MOVE_MARKER = "*** Move to: "`
  - `patch.HunkLine(kind: Literal["context","remove","add"], text: str)`
  - `patch.Hunk(lines: tuple[HunkLine, ...], anchor: str | None = None)`
  - `patch.FilePatch(kind: Literal["update","add","delete"], path: str, move_to: str | None, hunks: tuple[Hunk, ...], lines: tuple[str, ...])`
  - `patch.PatchPlan(files: tuple[FilePatch, ...])`
  - `patch.PatchError(code: Literal["patch_invalid","patch_conflict"], message: str)`
  - `patch.parse_patch(text: str) -> PatchPlan`
  - `patch.apply_to_text(original: str, file_patch: FilePatch) -> str`

**不变量：** `parse_patch` 只做语法校验，不碰文件系统；`apply_to_text` 是纯函数，
冲突时抛 `PatchError("patch_conflict", ...)`，绝不返回半应用的结果。这样 Task 6 才能
做到"先全部算出来，再全部写下去"。

**`anchor` 为什么必须保留：** `@@` 行后面的文本是锚点。一个只有 `+` 行的 hunk
（纯插入）没有任何上下文可供匹配，没有锚点就只能追加到文件末尾——那不是模型想要的
位置。`Hunk.anchor` 记下 `@@` 行的文本，应用时先定位它。锚点语义（冻结）：

- 有锚点 → 在文件中找到**第一处**包含该文本的行，以它作为搜索起点；
- 有 `old` 块 → 从搜索起点向后找第一个完全相等的连续块；
- 无 `old` 块但有锚点 → 插到锚点行之后；
- 无 `old` 块也无锚点 → 追加到文件末尾。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_apply_patch_parser.py`：

```python
"""Tests for the apply_patch parser and text applier."""

from __future__ import annotations

import pytest

from langharness_core.plugins.tools.patch import (
    FilePatch,
    Hunk,
    HunkLine,
    PatchError,
    PatchPlan,
    apply_to_text,
    parse_patch,
)


def update(*hunks: Hunk) -> FilePatch:
    return FilePatch(
        kind="update", path="a.py", move_to=None, hunks=hunks, lines=()
    )


def test_parses_an_update_with_context_and_changes() -> None:
    plan = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@ def target(\n"
        " unchanged\n"
        "-old line\n"
        "+new line\n"
        "*** End Patch\n"
    )
    assert len(plan.files) == 1
    file_patch = plan.files[0]
    assert file_patch.kind == "update"
    assert file_patch.path == "a.py"
    assert [line.kind for line in file_patch.hunks[0].lines] == [
        "context",
        "remove",
        "add",
    ]
    assert file_patch.hunks[0].lines[1].text == "old line"
    assert file_patch.hunks[0].anchor == "def target("


def test_parses_an_add_file() -> None:
    plan = parse_patch(
        "*** Begin Patch\n*** Add File: b.py\n+first\n+second\n*** End Patch\n"
    )
    assert plan.files == (
        FilePatch(
            kind="add", path="b.py", move_to=None, hunks=(), lines=("first", "second")
        ),
    )


def test_parses_a_delete_file() -> None:
    plan = parse_patch("*** Begin Patch\n*** Delete File: c.py\n*** End Patch\n")
    assert plan.files[0].kind == "delete"
    assert plan.files[0].path == "c.py"


def test_parses_a_move_inside_an_update() -> None:
    plan = parse_patch(
        "*** Begin Patch\n*** Update File: a.py\n*** Move to: d.py\n"
        "@@\n-a\n+b\n*** End Patch\n"
    )
    assert plan.files[0].move_to == "d.py"


def test_missing_begin_marker_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Update File: a.py\n-a\n+b\n*** End Patch\n")
    assert caught.value.code == "patch_invalid"
    assert "Begin Patch" in caught.value.message


def test_missing_end_marker_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** Delete File: a.py\n")
    assert caught.value.code == "patch_invalid"
    assert "End Patch" in caught.value.message


def test_empty_patch_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** End Patch\n")
    assert "no file sections" in caught.value.message


def test_unknown_marker_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** Rename File: a.py\n*** End Patch\n")
    assert "unrecognised" in caught.value.message


def test_an_update_with_no_hunks_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** Update File: a.py\n*** End Patch\n")
    assert "no hunks" in caught.value.message


def test_apply_replaces_a_matched_block() -> None:
    file_patch = update(
        Hunk(
            (
                HunkLine("context", "keep"),
                HunkLine("remove", "old"),
                HunkLine("add", "new"),
            )
        )
    )
    assert apply_to_text("keep\nold\n", file_patch) == "keep\nnew\n"


def test_apply_picks_the_occurrence_after_the_anchor() -> None:
    # Both blocks match the hunk; the anchor decides which one is edited.
    original = "def target(\n    old\n\ndef other(\n    old\n"
    file_patch = update(
        Hunk(
            (HunkLine("remove", "    old"), HunkLine("add", "    new")),
            anchor="def other(",
        )
    )
    assert apply_to_text(original, file_patch) == (
        "def target(\n    old\n\ndef other(\n    new\n"
    )


def test_context_lines_survive_on_both_sides_of_a_change() -> None:
    file_patch = update(
        Hunk(
            (
                HunkLine("context", "before"),
                HunkLine("remove", "old"),
                HunkLine("add", "new"),
                HunkLine("context", "after"),
            )
        )
    )
    assert apply_to_text("before\nold\nafter\n", file_patch) == (
        "before\nnew\nafter\n"
    )


def test_an_insertion_with_an_anchor_lands_after_it() -> None:
    file_patch = update(
        Hunk((HunkLine("add", "inserted"),), anchor="def target(")
    )
    assert apply_to_text("def target(\nbody\n", file_patch) == (
        "def target(\ninserted\nbody\n"
    )


def test_an_insertion_without_an_anchor_appends() -> None:
    file_patch = update(Hunk((HunkLine("add", "appended"),)))
    assert apply_to_text("body\n", file_patch) == "body\nappended\n"


def test_apply_reports_a_conflict_when_context_is_absent() -> None:
    file_patch = update(Hunk((HunkLine("remove", "missing"),)))
    with pytest.raises(PatchError) as caught:
        apply_to_text("present\n", file_patch)
    assert caught.value.code == "patch_conflict"
    assert "missing" in caught.value.message


def test_apply_reports_a_missing_anchor() -> None:
    file_patch = update(
        Hunk((HunkLine("add", "inserted"),), anchor="nowhere(")
    )
    with pytest.raises(PatchError) as caught:
        apply_to_text("body\n", file_patch)
    assert caught.value.code == "patch_conflict"
    assert "nowhere(" in caught.value.message


def test_apply_preserves_a_missing_trailing_newline() -> None:
    file_patch = update(Hunk((HunkLine("remove", "old"), HunkLine("add", "new"))))
    assert apply_to_text("old", file_patch) == "new"


def test_apply_preserves_a_trailing_newline() -> None:
    file_patch = update(Hunk((HunkLine("remove", "old"), HunkLine("add", "new"))))
    assert apply_to_text("old\n", file_patch) == "new\n"


def test_apply_add_file_returns_its_lines() -> None:
    file_patch = FilePatch(
        kind="add", path="b.py", move_to=None, hunks=(), lines=("x", "y")
    )
    assert apply_to_text("", file_patch) == "x\ny\n"


def test_apply_delete_file_returns_empty_text() -> None:
    file_patch = FilePatch(kind="delete", path="c.py", move_to=None, hunks=(), lines=())
    assert apply_to_text("anything\n", file_patch) == ""


def test_plan_round_trips_through_the_markers() -> None:
    plan = parse_patch(
        "*** Begin Patch\n*** Add File: b.py\n+only\n*** End Patch\n"
    )
    assert isinstance(plan, PatchPlan)
    assert plan.files[0].lines == ("only",)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_apply_patch_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_core.plugins.tools.patch'`

- [ ] **Step 3: 写实现**

`src/langharness_core/plugins/tools/patch.py`：

```python
"""Parsing and atomic application of the code agent's patch format.

The format is frozen in docs/plans/2026-09-22-code-agent-workspace-tools-plan.md
and is already understood by ``touched_paths_from_messages`` in
``plugins/prompts/context.py``.  Parsing never touches the filesystem and
application is a pure text function, so a caller can compute every file's new
content before writing any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BEGIN_MARKER = "*** Begin Patch"
END_MARKER = "*** End Patch"
UPDATE_MARKER = "*** Update File: "
ADD_MARKER = "*** Add File: "
DELETE_MARKER = "*** Delete File: "
MOVE_MARKER = "*** Move to: "
ANCHOR_MARKER = "@@"
CONTEXT_PREFIX = " "
REMOVE_PREFIX = "-"
ADD_PREFIX = "+"

PatchErrorCode = Literal["patch_invalid", "patch_conflict"]


class PatchError(Exception):
    """A patch that cannot be parsed, or cannot be applied to this content."""

    def __init__(self, code: PatchErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HunkLine:
    kind: Literal["context", "remove", "add"]
    text: str


@dataclass(frozen=True, slots=True)
class Hunk:
    """One contiguous edit. ``anchor`` is the text after the ``@@`` marker."""

    lines: tuple[HunkLine, ...]
    anchor: str | None = None


@dataclass(frozen=True, slots=True)
class FilePatch:
    kind: Literal["update", "add", "delete"]
    path: str
    move_to: str | None
    hunks: tuple[Hunk, ...]
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchPlan:
    files: tuple[FilePatch, ...]


def parse_patch(text: str) -> PatchPlan:
    """Parse a patch document into a plan, rejecting anything malformed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != BEGIN_MARKER:
        raise PatchError("patch_invalid", f"patch must start with {BEGIN_MARKER!r}")
    if lines[-1].strip() != END_MARKER:
        raise PatchError("patch_invalid", f"patch must end with {END_MARKER!r}")
    body = lines[1:-1]
    files: list[FilePatch] = []
    index = 0
    while index < len(body):
        line = body[index]
        if line.startswith(UPDATE_MARKER):
            path = line[len(UPDATE_MARKER):].strip()
            index += 1
            move_to = None
            if index < len(body) and body[index].startswith(MOVE_MARKER):
                move_to = body[index][len(MOVE_MARKER):].strip()
                index += 1
            hunks: list[Hunk] = []
            current: list[HunkLine] = []
            anchor: str | None = None
            while index < len(body) and not _is_marker(body[index]):
                raw = body[index]
                if raw.startswith(ANCHOR_MARKER):
                    if current:
                        hunks.append(Hunk(tuple(current), anchor))
                        current = []
                    anchor = raw[len(ANCHOR_MARKER):].strip() or None
                    index += 1
                    continue
                if raw.startswith(ADD_PREFIX):
                    current.append(HunkLine("add", raw[1:]))
                elif raw.startswith(REMOVE_PREFIX):
                    current.append(HunkLine("remove", raw[1:]))
                elif raw.startswith(CONTEXT_PREFIX):
                    current.append(HunkLine("context", raw[1:]))
                elif raw == "":
                    current.append(HunkLine("context", ""))
                else:
                    raise PatchError(
                        "patch_invalid", f"unrecognised patch line: {raw!r}"
                    )
                index += 1
            if current:
                hunks.append(Hunk(tuple(current), anchor))
            if not hunks:
                raise PatchError("patch_invalid", f"{path!r} has no hunks")
            files.append(
                FilePatch(
                    kind="update",
                    path=path,
                    move_to=move_to,
                    hunks=tuple(hunks),
                    lines=(),
                )
            )
            continue
        if line.startswith(ADD_MARKER):
            path = line[len(ADD_MARKER):].strip()
            index += 1
            added: list[str] = []
            while index < len(body) and not _is_marker(body[index]):
                raw = body[index]
                if not raw.startswith(ADD_PREFIX):
                    raise PatchError(
                        "patch_invalid",
                        f"added file {path!r} may only contain '+' lines: {raw!r}",
                    )
                added.append(raw[1:])
                index += 1
            files.append(
                FilePatch(
                    kind="add",
                    path=path,
                    move_to=None,
                    hunks=(),
                    lines=tuple(added),
                )
            )
            continue
        if line.startswith(DELETE_MARKER):
            files.append(
                FilePatch(
                    kind="delete",
                    path=line[len(DELETE_MARKER):].strip(),
                    move_to=None,
                    hunks=(),
                    lines=(),
                )
            )
            index += 1
            continue
        raise PatchError("patch_invalid", f"unrecognised patch line: {line!r}")
    if not files:
        raise PatchError("patch_invalid", "patch contains no file sections")
    return PatchPlan(files=tuple(files))


def _is_marker(line: str) -> bool:
    return line.startswith(
        (UPDATE_MARKER, ADD_MARKER, DELETE_MARKER, MOVE_MARKER, END_MARKER)
    )


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _apply_hunk(lines: list[str], hunk: Hunk, index: int) -> list[str]:
    old = [item.text for item in hunk.lines if item.kind in {"context", "remove"}]
    new = [item.text for item in hunk.lines if item.kind in {"context", "add"}]
    start = 0
    if hunk.anchor:
        start = next(
            (offset for offset, line in enumerate(lines) if hunk.anchor in line),
            -1,
        )
        if start < 0:
            raise PatchError(
                "patch_conflict",
                f"hunk {index} anchor {hunk.anchor!r} not found",
            )
        start += 1
    if not old:
        # Nothing to match on: land right after the anchor, or at the end.
        return lines[:start] + new + lines[start:] if hunk.anchor else lines + new
    for offset in range(start, len(lines) - len(old) + 1):
        if lines[offset : offset + len(old)] == old:
            return lines[:offset] + new + lines[offset + len(old) :]
    raise PatchError(
        "patch_conflict",
        f"hunk {index} context not found; expected {old!r}",
    )


def apply_to_text(original: str, file_patch: FilePatch) -> str:
    """Return the new content for one file, or raise before changing anything."""
    if file_patch.kind == "delete":
        return ""
    if file_patch.kind == "add":
        return "".join(f"{line}\n" for line in file_patch.lines)
    newline = _newline_of(original)
    had_trailing = original.endswith(newline) or original == ""
    lines = original.split(newline)
    if had_trailing and lines and lines[-1] == "":
        lines.pop()
    for index, hunk in enumerate(file_patch.hunks):
        lines = _apply_hunk(lines, hunk, index)
    if not lines:
        return ""
    return newline.join(lines) + (newline if had_trailing else "")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_apply_patch_parser.py -q`
Expected: PASS（16 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/tools/patch.py tests/test_apply_patch_parser.py
git commit -m "feat: parse and apply the code agent patch format"
```

---

### Task 6: `apply_patch` 工具（原子写入 + mutation 记录）

**Files:**
- Modify: `src/langharness_core/plugins/tools/code_workspace.py`
- Test: `tests/test_code_workspace_tools.py`（追加）

**Interfaces:**
- Consumes: Task 5 的 `parse_patch` / `apply_to_text` / `PatchError`；Task 2 的 `WorkspaceStateProvider`。
- Produces：`apply_patch` 工具返回 `{"changed": [{"path", "change", "before_sha256", "after_sha256"}], "generation": int}`

**原子性契约（设计第 6.1 节"失败不部分写"）：** 先把每个文件的新内容全部算进内存，
任何一个冲突就整体放弃并且**一个字节都不写**；只有全部算完才落盘。重命名按
"写新路径 + 删旧路径"两步在同一批次内完成。

- [ ] **Step 1: 写失败测试**

在 `tests/test_code_workspace_tools.py` 末尾追加：

```python
def test_apply_patch_updates_a_file_and_records_a_mutation(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "old\n")
    instance = plugin(tmp_path)
    result = {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {"patch": "*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** End Patch\n"}
    )
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new\n"
    assert result["changed"][0]["path"] == "a.py"
    assert result["changed"][0]["change"] == "update"
    assert result["changed"][0]["before_sha256"].startswith("sha256:")
    assert result["generation"] == 1
    assert instance._state.get_generation() == 1


def test_apply_patch_adds_and_deletes_files(tmp_path: Path) -> None:
    write(tmp_path / "gone.py", "x\n")
    instance = plugin(tmp_path)
    tools_ = {item.name: item for item in instance.get_tools()}
    tools_["apply_patch"].invoke(
        {
            "patch": "*** Begin Patch\n*** Add File: new.py\n+hello\n"
            "*** Delete File: gone.py\n*** End Patch\n"
        }
    )
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "hello\n"
    assert not (tmp_path / "gone.py").exists()
    assert instance._state.get_generation() == 2


def test_apply_patch_moves_a_file(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "body\n")
    instance = plugin(tmp_path)
    {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {
            "patch": "*** Begin Patch\n*** Update File: a.py\n*** Move to: b.py\n"
            "@@\n-body\n+moved\n*** End Patch\n"
        }
    )
    assert not (tmp_path / "a.py").exists()
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "moved\n"


def test_a_conflicting_hunk_writes_nothing(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "present\n")
    write(tmp_path / "b.py", "untouched\n")
    instance = plugin(tmp_path)
    result = {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {
            "patch": "*** Begin Patch\n*** Update File: b.py\n@@\n-untouched\n+changed\n"
            "*** Update File: a.py\n@@\n-absent\n+changed\n*** End Patch\n"
        }
    )
    assert result["error"]["code"] == "patch_conflict"
    # b.py was valid, but the batch is all-or-nothing.
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "untouched\n"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "present\n"
    assert instance._state.get_generation() == 0


def test_a_malformed_patch_writes_nothing(tmp_path: Path) -> None:
    instance = plugin(tmp_path)
    result = {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {"patch": "*** Update File: a.py\n-x\n+y\n"}
    )
    assert result["error"]["code"] == "patch_invalid"
    assert instance._state.get_generation() == 0


def test_apply_patch_refuses_to_escape_the_workspace(tmp_path: Path) -> None:
    instance = plugin(tmp_path)
    result = {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {"patch": "*** Begin Patch\n*** Add File: ../escape.py\n+x\n*** End Patch\n"}
    )
    assert result["error"]["code"] == "path_outside_workspace"
    assert not (tmp_path.parent / "escape.py").exists()


def test_apply_patch_creates_missing_parent_directories(tmp_path: Path) -> None:
    instance = plugin(tmp_path)
    {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {"patch": "*** Begin Patch\n*** Add File: deep/nested/new.py\n+x\n*** End Patch\n"}
    )
    assert (tmp_path / "deep" / "nested" / "new.py").read_text(encoding="utf-8") == "x\n"


def test_apply_patch_reports_a_missing_target_file(tmp_path: Path) -> None:
    instance = plugin(tmp_path)
    result = {item.name: item for item in instance.get_tools()}["apply_patch"].invoke(
        {"patch": "*** Begin Patch\n*** Update File: nope.py\n@@\n-a\n+b\n*** End Patch\n"}
    )
    assert result["error"]["code"] == "path_not_found"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_tools.py -q -k apply_patch`
Expected: FAIL — 占位实现返回 `internal_error`

- [ ] **Step 3: 写实现**

在 `code_workspace.py` 中补充导入（`dataclass`、补丁模块的三个名字、以及
`WorkspacePath`），加入 `PatchArgs`，并把 `_patch_tool` 换成下面的实现：

```python
from dataclasses import dataclass

from langharness_core.plugins.tools.patch import (
    PatchError,
    PatchPlan,
    apply_to_text,
    parse_patch,
)
from langharness_core.plugins.workspace.paths import WorkspacePath


class PatchArgs(BaseModel):
    patch: str = Field(
        min_length=1,
        description=(
            "Patch document starting with '*** Begin Patch' and ending with "
            "'*** End Patch'."
        ),
    )


    def _patch_tool(self) -> StructuredTool:
        plugin = self

        def apply_patch(patch: str) -> dict[str, Any]:
            try:
                plan = parse_patch(patch)
                staged = plugin._stage(plan)
            except WorkspacePathError as exc:
                return to_tool_error(exc)
            except PatchError as exc:
                return tool_error(exc.code, exc.message)
            # Every file's outcome is known; only now does anything change.
            changed: list[dict[str, Any]] = []
            for item in staged:
                if item.new_text is None:
                    item.destination.absolute.unlink(missing_ok=True)
                else:
                    item.destination.absolute.parent.mkdir(
                        parents=True, exist_ok=True
                    )
                    item.destination.absolute.write_text(
                        item.new_text, encoding="utf-8"
                    )
                if item.remove_original is not None:
                    # A move keeps the old copy until the new one is on disk,
                    # so a crash in between never loses both.
                    item.remove_original.absolute.unlink(missing_ok=True)
                before = digest(item.original) if item.original is not None else None
                after = digest(item.new_text) if item.new_text is not None else None
                changed.append(
                    {
                        "path": item.destination.relative,
                        "change": item.change,
                        "before_sha256": before,
                        "after_sha256": after,
                    }
                )
                if plugin._state is not None:
                    plugin._state.record_mutation(
                        item.destination.relative,
                        "apply_patch",
                        before_sha256=before,
                        after_sha256=after,
                    )
            generation = plugin._state.get_generation() if plugin._state else 0
            return {"changed": changed, "generation": generation}
```

配套的模块级类型与组件方法：

```python
@dataclass(frozen=True, slots=True)
class _StagedWrite:
    """One fully-computed file outcome, before anything is written.

    ``new_text`` is ``None`` for a deletion; ``remove_original`` is the old
    path of a move, unlinked only after the destination is on disk.
    """

    destination: WorkspacePath
    remove_original: WorkspacePath | None
    original: str | None
    new_text: str | None
    change: str


    def _read_text(self, target: WorkspacePath) -> str | None:
        """Current text of a file, or None when it is absent or unreadable."""
        if not target.is_file:
            return None
        try:
            return target.absolute.read_text(encoding="utf-8")
        except OSError:
            return None

    def _stage(self, plan: PatchPlan) -> list[_StagedWrite]:
        """Compute every file's outcome; raise before writing anything."""
        staged: list[_StagedWrite] = []
        for file_patch in plan.files:
            target = self._resolve(file_patch.path)
            original = self._read_text(target)
            if file_patch.kind == "add":
                if original is not None:
                    raise PatchError(
                        "patch_conflict", f"{file_patch.path!r} already exists"
                    )
                staged.append(
                    _StagedWrite(
                        destination=target,
                        remove_original=None,
                        original=None,
                        new_text=apply_to_text("", file_patch),
                        change="add",
                    )
                )
                continue
            if original is None:
                # A precise code beats a generic conflict: the model can fix
                # a wrong path, but not a message that only says "conflict".
                raise WorkspacePathError(
                    "path_not_found", f"{file_patch.path!r} does not exist"
                )
            if file_patch.kind == "delete":
                staged.append(
                    _StagedWrite(
                        destination=target,
                        remove_original=None,
                        original=original,
                        new_text=None,
                        change="delete",
                    )
                )
                continue
            destination = (
                self._resolve(file_patch.move_to) if file_patch.move_to else target
            )
            moved = destination.absolute != target.absolute
            staged.append(
                _StagedWrite(
                    destination=destination,
                    remove_original=target if moved else None,
                    original=original,
                    new_text=apply_to_text(original, file_patch),
                    change="update",
                )
            )
        return staged
```

`_read_text` 对目录也返回 `None`（`is_file` 为假），因此删除一个目录会得到
`path_not_found`，而不是把目录当成空文件。工具描述：

```python
            description=(
                "Apply a patch document to the workspace. This is the only way "
                "to modify files: it reports the before/after sha256 of every "
                "file it touched, which later verification is tied to. The "
                "patch is applied atomically — if any hunk fails to match, "
                "nothing is written at all — so a single call either lands "
                "completely or not at all. Format: '*** Begin Patch', then one "
                "or more sections, then '*** End Patch'. Section headers are "
                "'*** Update File: <path>' (with optional '*** Move to: "
                "<path>' to rename), '*** Add File: <path>' and '*** Delete "
                "File: <path>'. Within an update, lines are prefixed with a "
                "space (context), '-' (remove) or '+' (add); an '@@' line "
                "starts a new hunk. Context must match the current file "
                "exactly, so read the file first. Returns {\"changed\": "
                "[{\"path\": str, \"change\": 'update'|'add'|'delete', "
                "\"before_sha256\": str|null, \"after_sha256\": str|null}], "
                "\"generation\": int}, or {\"error\": {code, message, "
                "retryable, details}}; a conflict leaves the workspace "
                "untouched."
            ),
```

把 `apply_patch` 加进 `get_tools()`，并把 `test_tool_names_are_stable` 的期望集合改为
`{"list_directory", "read_file", "search_text", "apply_patch"}`。`apply_patch` 不进
`get_tool_presentations()`（理由见 Task 3 的注释）。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_tools.py -q`
Expected: PASS（22 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/tools/code_workspace.py tests/test_code_workspace_tools.py
git commit -m "feat: apply patches atomically and record each mutation"
```

---

### Task 7: `workspace_status`

**Files:**
- Modify: `src/langharness_core/plugins/tools/code_workspace.py`
- Test: `tests/test_code_workspace_tools.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `WorkspaceStateProvider.mutations()`；Task 1 的 `tool_error`。
- Produces：`workspace_status` 工具返回 `{"is_git_repo", "branch", "head", "generation", "dirty": [{"path","status","origin"}], "truncated"}`

**`origin` 的判定（设计第 6.1 节"区分 agent 与用户已有改动"）：** 一次 dirty 路径若
出现在本实例的 `mutations()` 记录里，`origin="agent"`，否则 `origin="pre_existing"`。
不需要基线快照——mutation 记录本身就是 agent 写过的确切集合。

- [ ] **Step 1: 写失败测试**

在 `tests/test_code_workspace_tools.py` 末尾追加：

```python
import subprocess


def git_repo(path: Path) -> None:
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(command, cwd=path, check=True, capture_output=True)
    write(path / "tracked.py", "original\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "init"], cwd=path, check=True, capture_output=True
    )


def test_workspace_status_reports_branch_and_head(tmp_path: Path) -> None:
    git_repo(tmp_path)
    result = tools(tmp_path)["workspace_status"].invoke({})
    assert result["is_git_repo"] is True
    assert result["branch"] in {"main", "master"}
    assert len(result["head"]) == 40
    assert result["generation"] == 0


def test_workspace_status_distinguishes_agent_edits_from_yours(tmp_path: Path) -> None:
    git_repo(tmp_path)
    write(tmp_path / "yours.py", "user change\n")
    instance = plugin(tmp_path)
    tools_ = {item.name: item for item in instance.get_tools()}
    tools_["apply_patch"].invoke(
        {"patch": "*** Begin Patch\n*** Update File: tracked.py\n@@\n-original\n+mine\n*** End Patch\n"}
    )
    result = tools_["workspace_status"].invoke({})
    by_path = {item["path"]: item["origin"] for item in result["dirty"]}
    assert by_path["tracked.py"] == "agent"
    assert by_path["yours.py"] == "pre_existing"


def test_workspace_status_on_a_non_git_workspace(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "x\n")
    result = tools(tmp_path)["workspace_status"].invoke({})
    assert result["is_git_repo"] is False
    assert result["dirty"] == []
    assert result["generation"] == 0


def test_workspace_status_truncates_a_flood_of_changes(tmp_path: Path) -> None:
    git_repo(tmp_path)
    for index in range(5):
        write(tmp_path / f"new{index}.py", "x\n")
    instance = plugin(tmp_path)
    instance._max_entries = 2
    result = {item.name: item for item in instance.get_tools()}["workspace_status"].invoke({})
    assert len(result["dirty"]) == 2
    assert result["truncated"] is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_tools.py -q -k workspace_status`
Expected: FAIL — 占位实现返回 `internal_error`

- [ ] **Step 3: 写实现**

在 `code_workspace.py` 中替换 `_status_tool`：

```python
    def _status_tool(self) -> StructuredTool:
        plugin = self

        def workspace_status() -> dict[str, Any]:
            root = plugin._root()
            touched = (
                {record.path for record in plugin._state.mutations()}
                if plugin._state is not None
                else set()
            )
            generation = plugin._state.get_generation() if plugin._state else 0
            branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
            if branch is None:
                return {
                    "is_git_repo": False,
                    "branch": None,
                    "head": None,
                    "generation": generation,
                    "dirty": [],
                    "truncated": False,
                }
            head = _git(root, "rev-parse", "HEAD") or ""
            porcelain = _git(root, "status", "--porcelain") or ""
            dirty: list[dict[str, str]] = []
            truncated = False
            for raw in porcelain.splitlines():
                if not raw.strip():
                    continue
                if len(dirty) >= plugin._max_entries:
                    truncated = True
                    break
                status, _, raw_path = raw[:2], raw[2:3], raw[3:]
                path = raw_path.strip().strip('"')
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                dirty.append(
                    {
                        "path": path,
                        "status": status,
                        "origin": "agent" if path in touched else "pre_existing",
                    }
                )
            return {
                "is_git_repo": True,
                "branch": branch,
                "head": head,
                "generation": generation,
                "dirty": dirty,
                "truncated": truncated,
            }

        return StructuredTool.from_function(
            func=workspace_status,
            name="workspace_status",
            description=(
                "Show the workspace's git state and which changes this agent "
                "made versus which were already there. Check it before "
                "claiming a clean tree, and before reverting anything: an "
                "entry marked 'pre_existing' is the user's work and must not "
                "be touched. 'generation' is the workspace mutation counter — "
                "it increases on every write, and a verification report is "
                "only valid for the generation it was produced at. Returns "
                "{\"is_git_repo\": bool, \"branch\": str|null, \"head\": "
                "str|null, \"generation\": int, \"dirty\": [{\"path\": str, "
                "\"status\": str (two-letter git porcelain code), \"origin\": "
                "'agent'|'pre_existing'}], \"truncated\": bool}. Returns "
                "{\"error\": {code, message, retryable, details}} when the "
                "workspace itself is unusable."
            ),
            args_schema=NoArgs,
        )
```

模块级辅助函数与 `NoArgs`：

```python
class NoArgs(BaseModel):
    """Empty schema for parameterless tools."""


def _git(root: Path, *arguments: str) -> str | None:
    """Run one git query in the workspace; None when git or the repo is absent."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
```

（在文件顶部加 `import subprocess`。）

把 `workspace_status` 加进 `get_tools()` 与 `get_tool_presentations()`，并把
`test_tool_names_are_stable` 的期望集合补齐为全部五个工具名。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_tools.py -q`
Expected: PASS（26 passed）

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugins/tools/code_workspace.py tests/test_code_workspace_tools.py
git commit -m "feat: report workspace git state and attribute each change"
```

---

### Task 8: Catalog 装配与作用域 e2e

**Files:**
- Modify: `src/langharness_core/plugin.py`
- Modify: `tests/test_imports.py`
- Test: `tests/test_code_workspace_scope.py`（新建）

**Interfaces:**
- Consumes: Task 3–7 的组件；计划 1 的 `WorkspaceProvider`。
- Produces：`AGENT_PLUGIN_CATALOG["code-tools"] = ("langharness_core.plugins.tools.code_workspace", "code-workspace-tools-plugin-factory", SPEC_TOOL)`、`AGENT_PLUGIN_CATALOG["workspace-state"] = ("langharness_core.plugins.workspace.state", "workspace-state-plugin-factory", SPEC_WORKSPACE_STATE)`，以及 `builtin_package()` 中对应的两个 agent template contribution。

**这是本计划唯一直接验证设计第 12.7 节"两个 agent 的 workspace 不串 scope"的任务。**

- [ ] **Step 1: 写失败测试**

新建 `tests/test_code_workspace_scope.py`：

```python
"""Two agents must not see each other's workspace."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path
from typing import Any

from langharness_core.contracts import (
    SPEC_TOOL,
    SPEC_WORKSPACE,
    SPEC_WORKSPACE_STATE,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id
from langharness_scope import ScopeId

DESCRIPTION = (
    "Scope e2e test plugin. Implements a test specification. Properties vary "
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
    (
        descriptor(
            "workspace",
            "langharness_core.plugins.workspace.root",
            "workspace-root-plugin-factory",
            SPEC_WORKSPACE,
        ),
    ),
    (
        descriptor(
            "workspace-state",
            "langharness_core.plugins.workspace.state",
            "workspace-state-plugin-factory",
            SPEC_WORKSPACE_STATE,
        ),
    ),
    (
        descriptor(
            "code-tools",
            "langharness_core.plugins.tools.code_workspace",
            "code-workspace-tools-plugin-factory",
            SPEC_TOOL,
        ),
    ),
)


def install_bundles(manager: PluginManager) -> None:
    """Install each descriptor exactly once, as the directory does."""
    for (item,) in BUNDLES:
        manager.install_descriptor(item)


def start_agent(
    manager: PluginManager, agent_id: str, workspace: Path
) -> ScopeId:
    """Give one agent its own scope, workspace and tool instance."""
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    manager.create_instance(
        "workspace-root-plugin-factory",
        "langharness_core.plugins.workspace.root",
        scope,
        properties={"plugin.workspace.root_dir": str(workspace)},
    )
    manager.create_instance(
        "workspace-state-plugin-factory",
        "langharness_core.plugins.workspace.state",
        scope,
    )
    manager.create_instance(
        "code-workspace-tools-plugin-factory",
        "langharness_core.plugins.tools.code_workspace",
        scope,
    )
    return scope


def test_two_agents_get_their_own_workspace_and_tools(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        (first / "only-in-one.py").write_text("x\n", encoding="utf-8")
        install_bundles(manager)
        start_agent(manager, "alpha", first)
        start_agent(manager, "beta", second)

        alpha_tools: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:alpha)"
        )
        beta_tools: list[Any] = manager.find_services(
            SPEC_TOOL, filter="(plugin.scope_id=agent:beta)"
        )
        assert len(alpha_tools) == 1
        assert len(beta_tools) == 1
        alpha = {item.name: item for item in alpha_tools[0].get_tools()}
        beta = {item.name: item for item in beta_tools[0].get_tools()}

        assert alpha["list_directory"].invoke({})["entries"][0]["name"] == (
            "only-in-one.py"
        )
        assert beta["list_directory"].invoke({})["entries"] == []
        # A relative read must resolve inside the caller's own workspace.
        assert beta["read_file"].invoke({"path": "only-in-one.py"})["error"]["code"] == (
            "path_not_found"
        )
    finally:
        manager.stop()


def test_mutation_generations_are_not_shared(tmp_path: Path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_bundles(manager)
        for name in ("alpha", "beta"):
            workspace = tmp_path / name
            workspace.mkdir()
            start_agent(manager, name, workspace)
        alpha_state = manager.find_service(
            SPEC_WORKSPACE_STATE, filter="(plugin.scope_id=agent:alpha)"
        )
        alpha_state.record_mutation("a.py", "apply_patch")
        beta_state = manager.find_service(
            SPEC_WORKSPACE_STATE, filter="(plugin.scope_id=agent:beta)"
        )
        assert alpha_state.get_generation() == 1
        assert beta_state.get_generation() == 0
    finally:
        manager.stop()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_scope.py -q`
Expected: FAIL — catalog 未声明 `code-tools` / `workspace-state`，`install_descriptor` 或
`create_instance` 报未安装。同时在 `plugin.py` 补上 catalog 后应转为通过。

- [ ] **Step 3: 写实现**

在 `src/langharness_core/plugin.py` 中：

1. 从 `langharness_core.contracts` 导入 `SPEC_WORKSPACE_STATE`。
2. `AGENT_PLUGIN_CATALOG` 追加：

```python
    "workspace-state": (
        "langharness_core.plugins.workspace.state",
        "workspace-state-plugin-factory",
        SPEC_WORKSPACE_STATE,
    ),
    "code-tools": (
        "langharness_core.plugins.tools.code_workspace",
        "code-workspace-tools-plugin-factory",
        SPEC_TOOL,
    ),
```

3. `AGENT_PLUGIN_DESCRIPTIONS` 追加两条（与既有条目同样的句式，说明实现哪个
   specification、有哪些属性、改属性要不要重启、什么时候卸载）。
4. `builtin_package()` 的 contributions 追加：

```python
            PluginContribution(
                "workspace-state-template",
                "agent",
                agent_plugin_template_descriptor("workspace-state"),
            ),
            PluginContribution(
                "code-tools-template",
                "agent",
                agent_plugin_template_descriptor("code-tools"),
            ),
```

5. `tests/test_imports.py` 的 `PUBLIC_MODULES` 追加：

```python
    "langharness_core.common.tool_error",
    "langharness_core.plugins.workspace.paths",
    "langharness_core.plugins.workspace.state",
    "langharness_core.plugins.tools.search",
    "langharness_core.plugins.tools.patch",
    "langharness_core.plugins.tools.code_workspace",
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_code_workspace_scope.py tests/test_imports.py -q`
Expected: PASS

- [ ] **Step 5: 跑全量门禁**

Run: `make check`
Expected: 全部通过，覆盖率 ≥95%。

- [ ] **Step 6: Commit**

```bash
git add src/langharness_core/plugin.py tests/test_imports.py tests/test_code_workspace_scope.py
git commit -m "feat: wire the code workspace tools into the agent catalog"
```

---

## 完成后的验收

计划 2a 落地后应能观测到：

1. 两个 agent 各自拿到自己的 workspace 与工具实例，`list_directory` / `read_file`
   只看到自己那个目录（Task 8 的 e2e 断言）；
2. 所有路径守卫都基于 realpath：`..` 穿越与 symlink escape 都被拒（Task 1）；
3. 补丁要么整体生效、要么一个字节都不写，冲突时 workspace 与 generation 都不变（Task 6）；
4. 每次成功写入都推进 generation，且能按路径回溯到 before/after hash（Task 2、6）；
5. `workspace_status` 能把 agent 自己改的文件与用户既有改动分开（Task 7）。

计划 2b（`exec_command` / `write_stdin` / `wait_job` / `stop_job`）与计划 3
（goal / verify / run controller / code preset）的骨架见
`docs/plans/2026-09-22-code-agent-phase1-roadmap.md`。
