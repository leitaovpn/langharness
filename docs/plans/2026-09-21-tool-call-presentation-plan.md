# 工具调用展示改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把交互式 CLI 的工具调用显示从 `✓ bash (250ms · 2.3KB)` 改成 Claude Code 形态：`● Bash(python3 main.py)` + `⎿ <输出>`，并按 `ctrl+o` 展开完整参数与输出。

**Architecture:** 工具插件声明 `name → 模板字符串`，经 `ToolProvider` 契约与 `describe()` 汇聚，由新路由 `GET /agents/{agent_id}/tools` 暴露。CLI runner 按 agent 懒加载并缓存目录，用调用参数把模板填成 headline 后放进 `tool_call` 事件；renderer 只读 `event["headline"]` 并绘制，不知道模板的存在。

**Tech Stack:** Python 3.13、iPOPO/Pelix 插件、FastAPI、rich、prompt_toolkit、pytest（mypy strict + pyright + ruff + 覆盖率门禁 95%）

**设计文档:** [docs/designs/2026-09-21-tool-call-presentation-design.md](../designs/2026-09-21-tool-call-presentation-design.md)

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/langharness_cli/common/toolview.py` | 纯函数：模板填充、输出行摘要 | 新建 |
| `src/langharness_core/contracts.py` | `ToolProvider` 契约加 `get_tool_presentations` | 修改 |
| `src/langharness_core/plugins/tools/workspace.py` | 声明 8 个工具的模板 | 修改 |
| `src/langharness_core/plugins/tools/management.py` | 声明 9 个工具的模板 | 修改 |
| `src/langharness_core/plugins/tools/tools.py` | 声明 `add` 的模板 | 修改 |
| `src/langharness_plugin/package.py` | `ToolExport` 加 `headline` 字段 | 修改 |
| `src/langharness_core/plugins/tools/export_adapter.py` | 透传导出的模板 | 修改 |
| `src/langharness_core/plugins/loop/agent_loop.py` | `describe()` 加 `tool_headlines` 键 | 修改 |
| `src/langharness_api/plugins/routes/agents.py` | 新路由 `/agents/{id}/tools` | 修改 |
| `src/langharness_cli/common/interactive.py` | 目录缓存、headline 注入、`ctrl+o` 绑定 | 修改 |
| `src/langharness_cli/plugins/rich_renderer.py` | 工具行重绘、`expand_last()` | 修改 |
| `src/langharness_cli/common/i18n.py` | 新文案 | 修改 |
| `tests/test_tool_view.py` | 纯函数测试 | 新建 |
| `tests/test_tool_presentations.py` | provider 覆盖度（防漂移） | 新建 |
| `tests/test_api_identity_routes.py` | 新路由测试 | 修改 |
| `tests/test_rich_renderer.py` | 工具行渲染测试 | 修改 |
| `tests/test_interactive_cli.py` | runner 注入与 `ctrl+o` 测试 | 修改 |

---

### Task 1: 模板填充纯函数

**Files:**
- Create: `src/langharness_cli/common/toolview.py`
- Test: `tests/test_tool_view.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_tool_view.py`：

```python
"""Filling a tool's headline template, and summarizing its output line."""

from __future__ import annotations

from langharness_cli.common.toolview import render_headline, render_output


def test_template_is_filled_from_the_call_arguments() -> None:
    assert render_headline("Bash({commands})", {"commands": "pwd"}) == "Bash(pwd)"


def test_a_template_may_use_several_arguments() -> None:
    template = "install_plugin({package_id} → {scope_id})"

    rendered = render_headline(
        template, {"package_id": "real.echo", "scope_id": "server"}
    )

    assert rendered == "install_plugin(real.echo → server)"


def test_a_template_without_placeholders_renders_as_written() -> None:
    assert render_headline("list_runtime_plugins()", {}) == "list_runtime_plugins()"


def test_a_missing_argument_declines_rather_than_leaving_a_hole() -> None:
    assert render_headline("Read({file_path})", {"path": "/tmp/a"}) is None


def test_a_none_argument_declines() -> None:
    assert render_headline("list({scope})", {"scope": None}) is None


def test_an_unparsable_template_declines() -> None:
    assert render_headline("Bash({commands)", {"commands": "pwd"}) is None


def test_long_values_are_collapsed_and_cut() -> None:
    rendered = render_headline("Bash({commands})", {"commands": "echo\n" + "x" * 200})

    assert rendered is not None
    assert "\n" not in rendered
    assert rendered.endswith("…)")
    assert len(rendered) < 100


def test_output_that_fits_is_shown_whole() -> None:
    line = render_output("hello world")

    assert line.text == "hello world"
    assert line.truncated is False


def test_multi_line_output_keeps_the_first_line_and_reports_the_rest() -> None:
    line = render_output("first\nsecond\nthird")

    assert line.text.startswith("first")
    assert "3 lines" in line.text
    assert line.truncated is True


def test_a_single_overlong_line_is_cut_and_marked() -> None:
    line = render_output("y" * 400)

    assert line.truncated is True
    assert line.text.endswith("…")


def test_empty_output_says_so() -> None:
    line = render_output("")

    assert line.truncated is False
    assert line.text == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_tool_view.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'langharness_cli.common.toolview'`

- [ ] **Step 3: 实现**

创建 `src/langharness_cli/common/toolview.py`：

```python
"""Turning a declared template and a call's arguments into transcript text.

Free of prompt_toolkit and rich on purpose: this is the part with rules worth
testing (what to do with a missing argument, a long value, a broken
template), and none of it needs a terminal to be checked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter
from typing import Any

#: A substituted value is collapsed to one line and cut here, so a template
#: cannot paste a whole file into the tool line.
VALUE_MAX = 60
#: An output line longer than this is cut; the rest is what ctrl+o is for.
OUTPUT_MAX = 100
#: How many lines of output the summary line reports before it stops reading.
COUNT_MAX = 999


@dataclass(frozen=True)
class OutputLine:
    """The text that follows ``⎿``, and whether more is being withheld."""

    text: str
    truncated: bool


def _collapse(value: Any) -> str:
    text = " ".join(str(value).split())
    return text[:VALUE_MAX] + "…" if len(text) > VALUE_MAX else text


def _fields(template: str) -> set[str] | None:
    try:
        return {
            name for _, name, _, _ in Formatter().parse(template) if name is not None
        }
    except ValueError:
        # Unbalanced braces and the like: refuse rather than guess.
        return None


def render_headline(template: str, args: Mapping[str, Any]) -> str | None:
    """Fill ``template`` from ``args``, or return ``None`` if it cannot be.

    Declining is deliberate. Substituting what is available would print
    ``install_plugin(real.echo → )`` or a bare ``None``; the caller's
    fallback -- the plain tool name -- reads better than either, and the full
    arguments are one ctrl+o away.
    """
    if not template:
        return None
    fields = _fields(template)
    if fields is None:
        return None
    if not fields:
        return template
    values = {key: _collapse(value) for key, value in args.items() if value is not None}
    if not fields <= values.keys():
        return None
    try:
        return template.format_map(values)
    except (KeyError, IndexError, ValueError):
        return None


def render_output(output: str) -> OutputLine:
    """Summarize a tool's output as the single line the transcript shows."""
    stripped = output.rstrip("\n")
    if not stripped:
        return OutputLine("", False)
    lines = stripped.split("\n")
    if len(lines) == 1 and len(lines[0]) <= OUTPUT_MAX:
        return OutputLine(lines[0], False)
    head = lines[0][:OUTPUT_MAX]
    if len(lines[0]) > OUTPUT_MAX:
        head += "…"
    if len(lines) > 1:
        count = min(len(lines), COUNT_MAX)
        head += f" … ({count} lines)"
    return OutputLine(head, True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_tool_view.py -q`
Expected: PASS（11 passed）

- [ ] **Step 5: 提交**

```bash
git add src/langharness_cli/common/toolview.py tests/test_tool_view.py
git commit -m "feat(cli): render a tool call from a declared template"
```

---

### Task 2: ToolProvider 契约与三个静态 provider

**Files:**
- Modify: `src/langharness_core/contracts.py:68-75`
- Modify: `src/langharness_core/plugins/tools/workspace.py:24-34`
- Modify: `src/langharness_core/plugins/tools/management.py`
- Modify: `src/langharness_core/plugins/tools/tools.py:36-44`
- Test: `tests/test_tool_presentations.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_tool_presentations.py`：

```python
"""Every tool a provider offers must be able to say how it reads."""

from __future__ import annotations

from typing import Any

from langharness_core.plugins.tools.tools import ToolPlugin
from langharness_core.plugins.tools.workspace import WorkspaceToolsPlugin


def assert_covers_its_tools(plugin: Any) -> None:
    offered = {tool.name for tool in plugin.get_tools()}
    declared = set(plugin.get_tool_presentations())

    assert offered == declared, f"missing: {sorted(offered - declared)}"


def test_workspace_provider_titles_every_tool_it_offers() -> None:
    assert_covers_its_tools(WorkspaceToolsPlugin())


def test_tool_plugin_titles_every_tool_it_offers() -> None:
    assert_covers_its_tools(ToolPlugin())


def test_workspace_titles_read_as_commands() -> None:
    presentations = WorkspaceToolsPlugin().get_tool_presentations()

    assert presentations["bash"] == "Bash({commands})"
    assert presentations["read_file"] == "Read({file_path})"
    assert presentations["list_directory"] == "list_directory({dir_path})"
```

管理工具的覆盖度测试**不放在这里** —— `ManagementToolsPlugin.get_tools()` 在没有
`_dynamic_manager` 时返回 `[]`，两端都是空集，断言等于没断言。它放进
`tests/test_management_tools.py`，复用那个文件已有的 `FakeManager`（见 Step 6b）。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_tool_presentations.py -q`
Expected: FAIL — `AttributeError: 'WorkspaceToolsPlugin' object has no attribute 'get_tool_presentations'`

- [ ] **Step 3: 给契约加方法**

在 `src/langharness_core/contracts.py` 的 `ToolProvider` 中，`get_plugin_info` 之后插入：

```python
    def get_tool_presentations(self) -> dict[str, str]: ...
```

完整协议变为：

```python
@service_contract(SPEC_TOOL)
@runtime_checkable
class ToolProvider(Protocol):
    """Contract implemented by every ``agent.plugin.tools`` service."""

    def get_tools(self) -> list[Any]: ...

    def get_tool_presentations(self) -> dict[str, str]: ...

    def get_plugin_info(self) -> dict[str, str]: ...
```

- [ ] **Step 4: 实现 workspace 的模板**

在 `src/langharness_core/plugins/tools/workspace.py` 的 `get_tools` 之后加：

```python
    def get_tool_presentations(self) -> dict[str, str]:
        """How each call reads in the CLI transcript.

        The file tools come from LangChain's toolkit and use snake_case
        argument names, so their templates name those exact keys.
        """
        return {
            "read_file": "Read({file_path})",
            "write_file": "write_file({file_path})",
            "list_directory": "list_directory({dir_path})",
            "file_search": "file_search({pattern})",
            "move_file": "move_file({source_path} → {destination_path})",
            "copy_file": "copy_file({source_path} → {destination_path})",
            "file_delete": "file_delete({file_path})",
            "bash": "Bash({commands})",
        }
```

- [ ] **Step 5: 运行测试确认 workspace 通过**

Run: `.venv/bin/python -m pytest tests/test_tool_presentations.py -q -k workspace`
Expected: PASS

- [ ] **Step 6: 实现 management 的模板**

在 `src/langharness_core/plugins/tools/management.py` 的 `get_tools` 之后加：

```python
    def get_tool_presentations(self) -> dict[str, str]:
        return {
            "list_scope_tree": "list_scope_tree()",
            "list_runtime_plugins": "list_runtime_plugins()",
            "discover_plugins": "discover_plugins()",
            "install_plugin": "install_plugin({package_id} → {scope})",
            "enable_plugin": "enable_plugin({name} @ {scope})",
            "disable_plugin": "disable_plugin({name} @ {scope})",
            "upgrade_plugin": "upgrade_plugin({name} @ {scope})",
            "uninstall_plugin": "uninstall_plugin({name} @ {scope})",
            "update_plugin_properties": "update_plugin_properties({name} @ {scope})",
        }
```

参数名已按实际签名核对：`install_plugin(package_id, contribution_id, scope)`、
`enable_plugin(name, scope)`、`disable_plugin(name, scope, confirm)`、
`upgrade_plugin(name, scope)`、`uninstall_plugin(name, scope, confirm)`、
`update_plugin_properties(name, scope, properties)`。**scope 参数叫 `scope`
而不是 `scope_id`** —— `scope_id` 是交互 CLI 侧的叫法。模板里的 key 写错会让
`render_headline` 返回 `None`，表现是工具行只剩名字，不会报错。

`confirm` 故意不进模板：它是个确认开关，不是这一行要传达的信息。

- [ ] **Step 6b: 给 management 写覆盖度测试**

在 `tests/test_management_tools.py` 末尾追加。该文件已有 `FakeManager` 和
`_plugin(manager) -> ManagementToolsPlugin` 辅助函数，直接用：

```python
def test_management_provider_titles_every_tool_it_offers() -> None:
    plugin = _plugin(FakeManager())

    offered = {tool.name for tool in plugin.get_tools()}
    declared = set(plugin.get_tool_presentations())

    assert offered == declared, f"missing: {sorted(offered - declared)}"
```

- [ ] **Step 7: 运行测试确认 management 通过**

Run: `.venv/bin/python -m pytest tests/test_tool_presentations.py tests/test_management_tools.py -q`
Expected: PASS

- [ ] **Step 8: 实现 tools.py 的模板**

在 `src/langharness_core/plugins/tools/tools.py` 的 `get_tools` 之后加：

```python
    def get_tool_presentations(self) -> dict[str, str]:
        return {"add": "add({a}, {b})"}
```

- [ ] **Step 8b: 补上第 5 个实现（demo 插件）**

`grep -rn "@Provides(ToolProvider)" src/ examples/` 会找到五个实现，不是四个。
`examples/plugin_demo/plugins/calculator_tool.py` 带
`@Instantiate("calculator-tool")` —— 加载该 demo 包时组件会自动实例化并走
`validate()`，缺方法报 `MISSING_METHOD`。测试套件目前不加载 examples/，所以
不会变红，但会留下一个坏掉的示例：

在 `examples/plugin_demo/plugins/calculator_tool.py` 的 `get_plugin_info` 之前加：

```python
    def get_tool_presentations(self) -> dict[str, str]:
        return {"add": "add({a}, {b})"}
```

- [ ] **Step 8c: 确认五个实现都齐了**

Run: `grep -rn "@Provides(ToolProvider)" src/ examples/ | wc -l` → 应为 5
Run: `grep -rn "def get_tool_presentations" src/ examples/ | wc -l` → 应为 5

- [ ] **Step 9: 运行全部测试并检查门禁**

Run:
```bash
.venv/bin/python -m pytest tests/test_tool_presentations.py -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
```
Expected: 全 PASS / All checks passed / Success

- [ ] **Step 10: 提交**

```bash
git add src/langharness_core/contracts.py src/langharness_core/plugins/tools/ tests/test_tool_presentations.py
git commit -m "feat(core): let tool plugins declare how their calls read"
```

---

### Task 3: ToolExport 携带模板

**Files:**
- Modify: `src/langharness_plugin/package.py:12-20`
- Modify: `src/langharness_core/plugins/tools/export_adapter.py`
- Test: `tests/test_tool_export_adapter.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_tool_export_adapter.py` 末尾追加（该文件已有 `Target` 与 `AddArgs`
两个测试装置）：

```python
def test_exported_tools_carry_their_declared_headline() -> None:
    adapter = ToolExportAdapter()
    adapter._target = Target()
    adapter._exports = [
        ToolExport("add_numbers", "Add numbers", "add", AddArgs, headline="add({a}, {b})")
    ]

    assert adapter.get_tool_presentations() == {"add_numbers": "add({a}, {b})"}


def test_an_export_without_a_headline_is_simply_absent() -> None:
    adapter = ToolExportAdapter()
    adapter._target = Target()
    adapter._exports = [ToolExport("add_numbers", "Add numbers", "add", AddArgs)]

    assert adapter.get_tool_presentations() == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_tool_export_adapter.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'headline'`

- [ ] **Step 3: 给 ToolExport 加字段**

在 `src/langharness_plugin/package.py` 的 `ToolExport` 中，`destructive` 之后加：

```python
    #: Template for how a call reads in the CLI transcript, e.g.
    #: ``"echo({text})"``. Empty means the call shows as its bare name.
    headline: str = ""
```

- [ ] **Step 4: 实现 export_adapter**

在 `src/langharness_core/plugins/tools/export_adapter.py` 的 `get_tools` 之后加：

```python
    def get_tool_presentations(self) -> dict[str, str]:
        return {
            export.name: export.headline
            for export in self._exports or []
            if export.headline
        }
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_tool_export_adapter.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/langharness_plugin/package.py src/langharness_core/plugins/tools/export_adapter.py tests/test_tool_export_adapter.py
git commit -m "feat(core): let exported tools declare a call headline"
```

---

### Task 4: describe() 汇总模板

**Files:**
- Modify: `src/langharness_core/plugins/loop/agent_loop.py:700-706`
- Test: `tests/test_e2e.py`（在已有 `describe()` 断言附近追加）

- [ ] **Step 1: 写失败的测试**

在 `tests/test_e2e.py` 里 `assert loop.describe()["tools"] == ["add"]` 的那几处之后，各补一行断言（至少补一处）：

```python
    assert loop.describe()["tool_headlines"] == {"add": "add({a}, {b})"}
```

> 已有的 `["tools"] == ["add"]` 断言**保持不动** —— 它验证的是名字列表，本次不改那个键。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_e2e.py -q -k describe`
Expected: FAIL — `KeyError: 'tool_headlines'`

- [ ] **Step 3: 实现**

把 `src/langharness_core/plugins/loop/agent_loop.py` 的 `describe()` 改为：

```python
    def describe(self) -> dict[str, Any]:
        llm_info = self._llm_provider.get_plugin_info() if self._llm_provider else None
        return {
            "llm": llm_info,
            "tools": [getattr(tool, "name", str(tool)) for tool in self._collect_tools()],
            "tool_headlines": self._collect_tool_headlines(),
            "middleware": [middleware.name for middleware in self._collect_middlewares()],
        }

    def _collect_tool_headlines(self) -> dict[str, str]:
        """Template per tool name, for clients that render calls.

        Names are kept in a separate key rather than folded into ``tools`` so
        that consumers reading the plain name list keep working.
        """
        headlines: dict[str, str] = {}
        for provider in self._effective(self._tool_providers):
            headlines.update(provider.get_tool_presentations())
        return headlines
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_e2e.py -q -k "describe or tools"`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_core/plugins/loop/agent_loop.py tests/test_e2e.py
git commit -m "feat(core): expose tool call templates from describe()"
```

---

### Task 5: 新路由 GET /agents/{agent_id}/tools

**Files:**
- Modify: `src/langharness_api/plugins/routes/agents.py`
- Test: `tests/test_api_identity_routes.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_api_identity_routes.py` 末尾追加：

```python
class FakeLoop:
    def describe(self) -> dict[str, Any]:
        return {"tools": ["bash"], "tool_headlines": {"bash": "Bash({commands})"}}


class FakeLoopDirectory(FakeDirectory):
    def get_loop(self, agent_id: str) -> Any:
        return FakeLoop() if agent_id == "simple_agent" else None


def test_agents_route_lists_tool_templates() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_directory = FakeLoopDirectory()

    response = make_client(plugin).get("/agents/simple_agent/tools")

    assert response.status_code == 200
    assert response.json()["tools"] == [
        {"name": "bash", "headline": "Bash({commands})"}
    ]


def test_agents_route_reports_an_unknown_agent() -> None:
    plugin = AgentsRoutePlugin()
    plugin._agent_directory = FakeLoopDirectory()

    assert make_client(plugin).get("/agents/nobody/tools").status_code == 404


def test_agents_route_reports_a_missing_directory() -> None:
    plugin = AgentsRoutePlugin()

    assert make_client(plugin).get("/agents/simple_agent/tools").status_code == 503
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_api_identity_routes.py -q -k tools`
Expected: FAIL — 404 for all three（路由不存在）

- [ ] **Step 3: 实现路由**

在 `src/langharness_api/plugins/routes/agents.py` 的 `list_agents()` 之后、`create_agent` 之前插入：

```python
        @router.get("/agents/{agent_id}/tools")
        def list_agent_tools(agent_id: str) -> dict[str, Any]:
            """Tool names with the template each call reads as.

            Clients render the transcript from this; a tool missing from
            ``tool_headlines`` simply shows as its bare name.
            """
            if self._agent_directory is None:
                raise HTTPException(
                    status_code=503, detail="Agent directory unavailable"
                )
            loop = self._agent_directory.get_loop(agent_id)
            if loop is None:
                raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")
            described = loop.describe()
            headlines = described.get("tool_headlines") or {}
            return {
                "tools": [
                    {"name": name, "headline": headlines.get(name, "")}
                    for name in described.get("tools") or []
                ]
            }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_api_identity_routes.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/langharness_api/plugins/routes/agents.py tests/test_api_identity_routes.py
git commit -m "feat(api): serve tool call templates per agent"
```

---

### Task 6: runner 拉取目录并注入 headline

**Files:**
- Modify: `src/langharness_cli/common/interactive.py`
- Test: `tests/test_interactive_cli.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_interactive_cli.py` 末尾追加：

```python
def test_tool_call_events_arrive_at_the_renderer_with_a_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    class CapturingRenderer(RecordingRenderer):
        def render_event(self, event):
            seen.append(dict(event))

    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream",
        lambda *a, **k: stream_response(
            [
                {
                    "type": "tool_call",
                    "name": "bash",
                    "tool_call_id": "c1",
                    "args": {"commands": "pwd"},
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.get",
        lambda url, **kwargs: httpx.Response(
            200,
            json={"tools": [{"name": "bash", "headline": "Bash({commands})"}]},
            request=httpx.Request("GET", url),
        ),
    )
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=CapturingRenderer(),
        agent_id="simple_agent",
    )
    runner.do_stream("hi")

    tool_call = next(event for event in seen if event["type"] == "tool_call")
    assert tool_call["headline"] == "Bash(pwd)"


def test_an_unknown_tool_falls_back_to_its_bare_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    class CapturingRenderer(RecordingRenderer):
        def render_event(self, event):
            seen.append(dict(event))

    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream",
        lambda *a, **k: stream_response(
            [
                {
                    "type": "tool_call",
                    "name": "mystery",
                    "tool_call_id": "c1",
                    "args": {"x": 1},
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.get",
        lambda url, **kwargs: httpx.Response(
            200, json={"tools": []}, request=httpx.Request("GET", url)
        ),
    )
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=CapturingRenderer(),
        agent_id="simple_agent",
    )
    runner.do_stream("hi")

    tool_call = next(event for event in seen if event["type"] == "tool_call")
    assert "headline" not in tool_call


def test_a_failing_catalogue_leaves_the_transcript_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    class CapturingRenderer(RecordingRenderer):
        def render_event(self, event):
            seen.append(dict(event))

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("no server")

    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream",
        lambda *a, **k: stream_response(
            [
                {
                    "type": "tool_call",
                    "name": "bash",
                    "tool_call_id": "c1",
                    "args": {"commands": "pwd"},
                },
                {"type": "assistant", "content": "done"},
            ]
        ),
    )
    monkeypatch.setattr("langharness_cli.common.interactive.httpx.get", refuse)
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=CapturingRenderer(),
        agent_id="simple_agent",
    )
    runner.do_stream("hi")

    assert any(event["type"] == "assistant" for event in seen)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_interactive_cli.py -q -k headline or catalogue or falls_back`
Expected: FAIL — `KeyError: 'headline'` / `assert 'headline' not in ...`

- [ ] **Step 3: 实现目录缓存**

在 `src/langharness_cli/common/interactive.py` 顶部导入区加：

```python
from langharness_cli.common.toolview import render_headline
```

在 `InteractiveCLIRunner.__init__` 的 `self.session_id = session_id` 之后加：

```python
        self._tool_headlines: dict[str, dict[str, str]] = {}
```

在 `_update_model_status` 之前加两个方法：

```python
    def _headlines_for(self, agent_id: str) -> dict[str, str]:
        """Tool templates for one agent, fetched at most once per response.

        A failed lookup is cached as empty so an unreachable server costs one
        attempt rather than one per tool call; the cache is dropped at the
        start of each response.
        """
        if agent_id not in self._tool_headlines:
            self._tool_headlines[agent_id] = self._fetch_headlines(agent_id)
        return self._tool_headlines[agent_id]

    def _fetch_headlines(self, agent_id: str) -> dict[str, str]:
        try:
            response = httpx.get(
                f"{self.base_url}/agents/{agent_id}/tools",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return {}
        return {
            str(entry.get("name", "")): str(entry.get("headline", ""))
            for entry in payload.get("tools") or []
            if entry.get("name") and entry.get("headline")
        }

    def _with_headline(self, event: dict[str, Any]) -> dict[str, Any]:
        """Stamp the rendered headline onto a tool_call event."""
        if event.get("type") != "tool_call":
            return event
        template = self._headlines_for(self.agent_id).get(str(event.get("name", "")))
        if not template:
            return event
        rendered = render_headline(template, event.get("args") or {})
        if rendered:
            event["headline"] = rendered
        return event
```

- [ ] **Step 4: 接线**

在 `do_stream` 的 `self.renderer.start_response()` 之后加一行：

```python
        self._tool_headlines.clear()
```

把 `do_stream` 里这一行：

```python
                    self.renderer.render_event(event)
```

改为：

```python
                    self.renderer.render_event(self._with_headline(event))
```

同样把 `_resume_approval` 里的：

```python
                if not self._apply_session_event(event):
                    self.renderer.render_event(event)
```

改为：

```python
                if not self._apply_session_event(event):
                    self.renderer.render_event(self._with_headline(event))
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_interactive_cli.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/langharness_cli/common/interactive.py tests/test_interactive_cli.py
git commit -m "feat(cli): resolve tool call headlines before rendering"
```

---

### Task 7: renderer 重绘工具行

**Files:**
- Modify: `src/langharness_cli/common/i18n.py`
- Modify: `src/langharness_cli/plugins/rich_renderer.py`
- Test: `tests/test_rich_renderer.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_rich_renderer.py` 末尾追加：

```python
def test_plain_path_renders_a_call_from_its_headline() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {
            "type": "tool_call",
            "name": "bash",
            "tool_call_id": "c1",
            "args": {"commands": "pwd"},
            "headline": "Bash(pwd)",
        }
    )
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "c1", "output": "/work"}
    )
    renderer.finish_response()
    rendered = output.getvalue()

    assert "● Bash(pwd)" in rendered
    assert "⎿ /work" in rendered


def test_a_call_without_a_headline_falls_back_to_the_bare_name() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "mystery", "tool_call_id": "c1", "args": {"x": 1}}
    )
    renderer.finish_response()

    assert "● mystery" in output.getvalue()
    assert "{'x': 1}" not in output.getvalue()


def test_the_expand_hint_appears_only_when_output_was_cut() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": {}}
    )
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "c1", "output": "short"}
    )
    renderer.finish_response()

    assert "ctrl+o" not in output.getvalue()


def test_cut_output_advertises_the_expand_key() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": {}}
    )
    renderer.render_event(
        {
            "type": "tool_output",
            "name": "bash",
            "tool_call_id": "c1",
            "output": "\n".join(f"line {index}" for index in range(30)),
        }
    )
    renderer.finish_response()

    assert "ctrl+o" in output.getvalue()
```

> 这几条走的是 plain 路径（`make_renderer()` 默认 `force_terminal=False`）。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_rich_renderer.py -q -k "headline or expand_hint or advertises"`
Expected: FAIL — 输出里仍是 `+ bash {'commands': 'pwd'}`

- [ ] **Step 3: 加文案**

在 `src/langharness_cli/common/i18n.py` 的 `"en"` 字典里 `"tool_error"` 之后加：

```python
        "tool_expand_hint": " (ctrl+o to expand)",
        "tool_no_output": "(no output)",
        "tool_expand_header": "Tool calls from the last response:",
        "tool_expand_args": "args:",
        "tool_expand_output": "output:",
        "tool_nothing_to_expand": "Nothing to expand yet.",
```

在 `"zh"` 字典里对应位置加：

```python
        "tool_expand_hint": " (ctrl+o 展开)",
        "tool_no_output": "（无输出）",
        "tool_expand_header": "上一次响应的工具调用：",
        "tool_expand_args": "参数：",
        "tool_expand_output": "输出：",
        "tool_nothing_to_expand": "暂无可展开内容。",
```

- [ ] **Step 4: 扩展 _ToolRun**

在 `src/langharness_cli/plugins/rich_renderer.py` 顶部导入区加：

```python
from langharness_cli.common.toolview import OutputLine, render_output
```

把 `_ToolRun` 改为：

```python
@dataclass
class _ToolRun:
    """One tool invocation as tracked by the renderer."""

    name: str
    headline: str
    args: Any
    started: float
    status: str = "running"
    duration_ms: int = 0
    output_bytes: int = 0
    #: Kept in full, not just counted: ctrl+o prints it back verbatim.
    output: str = ""
```

- [ ] **Step 5: 重写 _on_tool_call / _on_tool_output**

把 `_on_tool_call` 改为：

```python
    def _on_tool_call(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name", "tool"))
        tool_call_id = str(event.get("tool_call_id") or f"run-{len(self._tool_runs)}")
        run = _ToolRun(
            name=name,
            headline=str(event.get("headline") or name),
            args=event.get("args"),
            started=time.monotonic(),
        )
        self._tool_runs[tool_call_id] = run
        self._segments.append(run)
        self._status = "tool"
        if not self.console.is_terminal:
            self.console.print(f"● {run.headline}", markup=False, highlight=False)
            return
        self._refresh(force=True)
```

把 `_on_tool_output` 改为：

```python
    def _on_tool_output(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name", "tool"))
        tool_call_id = str(event.get("tool_call_id", ""))
        output = str(event.get("output", ""))
        run = self._tool_runs.get(tool_call_id) or self._find_running_run(name)
        if run is None:
            run = _ToolRun(
                name=name, headline=name, args=None, started=time.monotonic()
            )
            self._segments.append(run)
        run.status = "error" if self._is_error(output) else "done"
        run.duration_ms = max(0, int((time.monotonic() - run.started) * 1000))
        run.output_bytes = len(output.encode("utf-8"))
        run.output = output
        if not self.console.is_terminal:
            self.console.print(self._output_line(run), markup=False, highlight=False)
            return
        self._refresh(force=True)
```

- [ ] **Step 6: 重写工具行渲染**

把 `_tool_line` 整个替换为下面两个方法，并**删掉 `_tool_result_line`**（它的
唯一调用点在 Step 5 已被 `_output_line` 取代）：

```python
    def _tool_line(self, run: _ToolRun) -> Text:
        if run.status == "running":
            return Text.assemble(
                (f"{self._spinner()} ", "cyan"),
                (run.headline, "bold cyan"),
            )
        if run.status == "error":
            return Text.assemble(
                ("● ", "red"),
                (run.headline, "bold red"),
                (f" ({tr(self._locale, 'tool_error')})", "red"),
            )
        return Text.assemble(("● ", "green"), (run.headline, "bold"))

    def _output_line(self, run: _ToolRun) -> str:
        if run.status == "error":
            summary = render_output(run.output)
            return f"  ⎿ {summary.text}" if summary.text else f"  ⎿ {tr(self._locale, 'tool_error')}"
        summary = render_output(run.output)
        if not summary.text:
            return f"  ⎿ {tr(self._locale, 'tool_no_output')}"
        hint = (
            tr(self._locale, "tool_expand_hint") if summary.truncated else ""
        )
        return f"  ⎿ {summary.text}{hint}"
```

- [ ] **Step 6b: 让输出行真的进到画面里**

`_tool_line` 改成两行形态之后，**光换渲染函数不够** —— 有两个地方在拼装画面，
不一起改的话输出行永远不会出现。这是最容易漏的一步。

把 `_build_frame` 里的这一行：

```python
            if isinstance(segment, str):
                items.append(Markdown(segment))
            else:
                items.append(self._tool_line(segment))
```

改为：

```python
            if isinstance(segment, str):
                items.append(Markdown(segment))
            else:
                items.append(self._tool_line(segment))
                if segment.status != "running":
                    items.append(Text(self._output_line(segment), style="dim"))
```

把 `_commit_overflow` 里的：

```python
            if not isinstance(head, str):
                committed.append(self._tool_line(head))
                self._segments.pop(0)
                continue
```

改为：

```python
            if not isinstance(head, str):
                committed.append(self._tool_line(head))
                if head.status != "running":
                    committed.append(Text(self._output_line(head), style="dim"))
                self._segments.pop(0)
                continue
```

运行中（`status == "running"`）不画输出行：那时还没有输出，画一行空的 `⎿`
是噪音。

- [ ] **Step 6c: 确认没有孤儿方法**

Run: `grep -n "_tool_result_line\|_result_meta" src/ tests/`
Expected: 只剩 `_result_meta` 的定义与 Task 8 里 `expand_last` 的使用。
`_tool_result_line` 应已无任何引用。`_result_meta` 不能删 —— 耗时与字节数
从工具行移走后由展开视图承接（Task 8）。

- [ ] **Step 7: 决定提示行是否算“可展开”**

`render_output` 返回的 `OutputLine` 已经带了 `truncated`，`_output_line` 直接用它。**注意 plain 路径下不显示 `ctrl+o`**：那路径没有按键处理，写了就是骗人。把 `_output_line` 的 hint 那三行改为：

```python
        show_hint = summary.truncated and self.console.is_terminal
        hint = tr(self._locale, "tool_expand_hint") if show_hint else ""
```

- [ ] **Step 8: 更新既有断言**

既有测试断言 `"+ bash {'commands': 'pwd'}"`、`"✓ bash (250ms · 2.3KB)"`、`"✗ bash (error)"`。按新形态改为：

- `test_plain_path_renders_tool_rows_with_duration_and_size`：断言 `"● bash"`（无 headline 时回退到名字）与 `"⎿ f" * 2400` 的首行存在；同时把测试名改为 `test_plain_path_renders_a_tool_row_with_its_output`，因为耗时不在这里显示了
- `test_plain_path_matches_output_to_running_tool_without_id`：断言 `"● bash"` 与 `"⎿ /workspace"`
- `test_plain_path_marks_error_outputs`：断言 `"● bash (error)"`
- `tests/test_interactive_cli.py::test_interactive_runner_stream_request`：断言 `"+ bash {'commands': 'pwd'}"` 改为 `"● bash"`，`"✓ bash ("` 与 `"10B"` 改为 `"⎿ /workspace"`

- [ ] **Step 9: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_rich_renderer.py tests/test_interactive_cli.py -q`
Expected: PASS

- [ ] **Step 10: 提交**

```bash
git add src/langharness_cli/common/i18n.py src/langharness_cli/plugins/rich_renderer.py tests/test_rich_renderer.py tests/test_interactive_cli.py
git commit -m "feat(cli): draw tool calls as a headline and an output line"
```

---

### Task 8: ctrl+o 展开

**Files:**
- Modify: `src/langharness_cli/plugins/rich_renderer.py`
- Modify: `src/langharness_cli/common/interactive.py`
- Test: `tests/test_rich_renderer.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_rich_renderer.py` 末尾追加：

```python
def test_expand_last_prints_full_arguments_and_output() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {
            "type": "tool_call",
            "name": "bash",
            "tool_call_id": "c1",
            "args": {"commands": "pwd"},
            "headline": "Bash(pwd)",
        }
    )
    renderer.render_event(
        {
            "type": "tool_output",
            "name": "bash",
            "tool_call_id": "c1",
            "output": "\n".join(f"line {i}" for i in range(30)),
        }
    )
    renderer.finish_response()
    output.truncate(0)
    output.seek(0)

    renderer.expand_last()

    expanded = output.getvalue()
    assert "Bash(pwd)" in expanded
    assert "commands" in expanded
    assert "line 29" in expanded


def test_expand_last_before_any_call_says_so() -> None:
    renderer, output = make_renderer()

    renderer.expand_last()

    assert "Nothing to expand" in output.getvalue()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_rich_renderer.py -q -k expand_last`
Expected: FAIL — `AttributeError: 'RichInteractiveRenderer' object has no attribute 'expand_last'`

- [ ] **Step 3: 实现 expand_last**

在 `src/langharness_cli/plugins/rich_renderer.py` 的 `finish_response` 之后加：

```python
    def expand_last(self) -> None:
        """Print the tool calls of the last response in full.

        This is the other half of the collapsed line: the transcript shows
        one line per call, and this is where the arguments and the whole
        output come back. Unprintable, so it is repeatable but not a toggle.
        """
        runs = [run for run in self._tool_runs.values()]
        if not runs:
            self.console.print(
                tr(self._locale, "tool_nothing_to_expand"), style="dim"
            )
            return
        self.console.print(tr(self._locale, "tool_expand_header"), style="dim")
        for run in runs:
            meta = self._result_meta(run)
            self.console.print(
                Text.assemble(
                    ("● ", "green"),
                    (run.headline, "bold"),
                    (f" ({meta})" if meta else "", "dim"),
                )
            )
            self.console.print(
                Text.assemble(
                    (f"  {tr(self._locale, 'tool_expand_args')} ", "dim"),
                    (str(run.args), ""),
                ),
                markup=False,
                highlight=False,
            )
            self.console.print(
                Text.assemble(
                    (f"  {tr(self._locale, 'tool_expand_output')} ", "dim"),
                    (run.output.rstrip() or tr(self._locale, "tool_no_output"), ""),
                ),
                markup=False,
                highlight=False,
            )
```

- [ ] **Step 4: 绑定按键**

在 `src/langharness_cli/common/interactive.py` 的导入区加：

```python
from prompt_toolkit.key_binding import KeyBindings
```

把构造 `PromptSession` 的那段改为：

```python
        self._session = session
        if self._session is None and self._interactive_input:
            bindings = KeyBindings()

            @bindings.add("c-o")
            def _expand(event: Any) -> None:
                """Take ctrl+o from readline's operate-and-get-next.

                The transcript is the thing worth acting on here, and that
                readline binding is obscure enough to be worth the trade.
                """
                self.renderer.expand_last()

            self._session = PromptSession(
                history=history,
                auto_suggest=AutoSuggestFromHistory(),
                style=build_palette_style(),
                key_bindings=bindings,
            )
```

- [ ] **Step 5: 写绑定测试**

在 `tests/test_interactive_cli.py` 末尾追加：

```python
def test_ctrl_o_asks_the_renderer_to_expand() -> None:
    expanded: list[bool] = []

    class Recording(RichInteractiveRenderer):
        def expand_last(self) -> None:
            expanded.append(True)

    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=Recording(),
    )
    runner.renderer.expand_last()

    assert expanded == [True]
```

> 这条只证明 renderer 契约成立。按键到回调的那一段用 Task 9 的真实 CLI 冒烟验证 —— prompt_toolkit 的键绑定不做端到端按键模拟，测起来成本高于收益。

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_rich_renderer.py tests/test_interactive_cli.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/langharness_cli/plugins/rich_renderer.py src/langharness_cli/common/interactive.py tests/test_rich_renderer.py tests/test_interactive_cli.py
git commit -m "feat(cli): expand the last response's tool calls with ctrl+o"
```

---

### Task 9: 门禁与真实 CLI 证据

**Files:**
- Modify: `scripts/cli_palette_smoke.py`（顺手把工具行纳入冒烟）

- [ ] **Step 1: 跑完整门禁**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m mypy && .venv/bin/python -m pyright --pythonpath .venv/bin/python`
Expected: All checks passed / Success / 0 errors

- [ ] **Step 2: 跑覆盖率**

Run:
```bash
.venv/bin/python -m pytest -q --cov=langharness --cov=langharness_scope --cov=langharness_config --cov=langharness_logging --cov=langharness_core --cov=langharness_plugin --cov=langharness_api --cov=langharness_cli --cov-report=term-missing --cov-fail-under=95
```
Expected: 覆盖率 ≥ 95%。若新代码拉低到阈值以下，按 `term-missing` 补齐缺失分支再重跑。

- [ ] **Step 3: 真实 CLI 观察**

先确认补全没被这次改动带坏：

Run: `.venv/bin/python scripts/cli_palette_smoke.py`
Expected: exit 0

然后起一次真实 CLI，输入一句会触发 `bash` 的话（例如「用 bash 看看当前目录」），
用 pty 抓屏观察工具行。抓屏装置直接复用 `scripts/cli_palette_smoke.py` 里的
`drain` / `render` / `Screen`，把 `STEPS` 换成一句自然语言再加一次回车即可 ——
不需要新写模拟器。

**这一步需要可用的模型 provider。** 若环境里没有可用的 `[providers.default]`，
跳过本步，并在交付说明里写明：

> 工具行形态未做真实 LLM 端到端验证，仅有单元测试与渲染测试覆盖。

不要用假模型或伪造的抓到输出冒充端到端 —— 那会让这条交付看起来很可信，
实际没验过。

- [ ] **Step 4: 提交**

```bash
git add scripts/cli_palette_smoke.py
git commit -m "test: cover the tool call rows in the real-binary smoke"
```

---

## 完成标准

- [ ] `● ToolName(args)` 与 `⎿ 输出` 在真实 CLI 上按设计形态出现
- [ ] 未声明模板的工具显示为裸名，不显示 repr
- [ ] `(ctrl+o to expand)` 只在输出被截断时出现，且只在终端下出现
- [ ] `ctrl+o` 在提示符下能打印完整参数与输出
- [ ] `get_tool_presentations` 覆盖度测试对四个 provider 都通过
- [ ] ruff / mypy / pyright 全清，覆盖率 ≥ 95%
- [ ] 服务端不可达时 transcript 不中断，工具行回退到裸名
