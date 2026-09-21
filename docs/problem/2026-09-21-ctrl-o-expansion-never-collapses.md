# ctrl+o 只能展开，不能收回

日期：2026-09-21
状态：已修复（按"修复方向 · 方案 A"实施，单测 + 真实终端 pty 复验通过）

## 现象

交互式 CLI 里，让一次响应产生工具调用、且输出被行内截断
（`⎿ output-line-01 … (12 lines) (ctrl+o to expand)`），按 `ctrl+o`：完整参数与完整输出打印出来。

**再按一次 `ctrl+o`，展开的内容不会收回** —— 而是在下面**再打印一份一模一样的块**。
按几次堆几份，终端里没有任何按键能把它们去掉。

`ctrl+o` 只绑在提示符上（`PromptSession` 的 `key_bindings`），流式进行中按它无效 ——
这一点与设计一致，不是问题所在。

## 最小复现

`docs/problem/repro/repro_ctrl_o_no_collapse.py`，`.venv/bin/python` 直接跑，不需要凭据：

假 LLM 先回一个 `bash` 调用（`printf 'output-line-%02d\n' $(seq 1 12)`，12 行，行内必然被截断），
真实 CLI 跑在 pty 里，发一条消息，然后连按两次 `ctrl+o`，屏幕从字节流重建：

```
$ .venv/bin/python docs/problem/repro/repro_ctrl_o_no_collapse.py
─── after the 1st ctrl+o ───   块一：header + `● Bash(...)` + args + 12 行 output
─── after the 2nd ctrl+o ───   块二：同一份再出现一次，块一仍在屏幕上

=== evidence ===
expand header printed            : 2x
full output still on screen      : True
BUG PRESENT                      : True
```

连跑 3 次，`BUG PRESENT: True` 稳定复现。

搭这个复现踩到的两个坑（都写进脚本注释了）：

1. **必须用 pty**。`InteractiveCLIRunner` 只在 `sys.stdin.isatty()` 时建 `PromptSession`
   （`src/langharness_cli/common/interactive.py:80`），管道输入走 `input()` 回退，
   `ctrl+o` 只是流里一个普通字节，什么都不会发生。
2. **pty 必须回应 CPR**（`CSI 6n`）。不回应时 prompt_toolkit 会放弃自己的布局，
   在终端上打 `WARNING: your terminal doesn't support cursor position requests (CPR)`，
   截出来的屏幕是 harness 的产物而不是真实终端的画面。

## 根因

两层，都在客户端渲染层，与工具调用、agent loop 无关。

**1. `expand_last()` 是单向打印，没有"处于展开态"这个状态，也就没有反向操作。**

`src/langharness_cli/plugins/rich_renderer.py:156`：

```python
def expand_last(self) -> None:
    """Print the tool calls of the last response in full.
    ...
    Printing cannot be undone, so this repeats rather than toggling.
    """
```

`src/langharness_cli/common/interactive.py:88` 的 `_key_bindings()` 无条件调用它，
所以第二次按键 = 第二份拷贝。**折叠这半边不是坏了，是根本没写。**

**2. 打印绕过了 prompt_toolkit 的 renderer。**

按键回调里直接 `console.print` 写 stdout，prompt_toolkit 完全不知道这几行存在，
下一次重绘仍按**过期的光标位置**摆放自己的界面。实测出现过一次工具栏文字盖进展开块
（`output-line-06mple_agent · ctrlo_se · ...`），但重跑 3 次没再复现，
所以这里只记"已知脆弱"：prompt_toolkit 对这种写入的正解是 `run_in_terminal()`，不是裸 `print`。

补充：设计文档和计划都**明确写过**这个取舍
（`docs/designs/2026-09-21-tool-call-presentation-design.md` 的 “## ctrl+o”、
`docs/plans/2026-09-21-tool-call-presentation-plan.md:1281` “repeatable but not a toggle”），
所以这不是实现失误，而是**设计留下的缺口**：终端 scrollback 只能追加，
当时的结论是"重复"而不是"切换"。计划的 `完成标准` 里
“`ctrl+o` 在提示符下能打印完整参数与输出” 一项至今未勾选，
真实终端上的行为也从未端到端验证过（见 `scripts/cli_palette_smoke.py` 的 scope note）。

## 影响

- 按键只能累加：想再看一遍就再堆一份，长输出把 scrollback 冲掉；
- 没有任何按键能收回细节，与 Claude Code 同名键（真·开合切换）的预期不一致；
- `_tool_runs` 只在 `start_response()` 清空，所以下一次响应之后 `ctrl+o` 只会回
  “Nothing to expand yet.”，而上一份展开块还留在屏幕上 —— 进一步坐实"收不回"。

## 修复方向

关键约束：**提示符下终端归 prompt_toolkit 所有**，rich 的 `Live`（本仓库唯一的可重绘区域）
这时是停着的。所以能被收回的内容必须画在 prompt_toolkit 自己的布局里，不能进 scrollback。

已实测可行（pty + CPR 回应）：把展开块做成 `bottom_toolbar` 的多行内容
（prompt_toolkit 3.0.53 里工具栏是 `Window(FormattedTextControl(...), height=Dimension(min=1))`，
高度随内容增长），`ctrl+o` 只翻转一个布尔量并 `event.app.invalidate()`，
prompt_toolkit 重绘时自己擦掉这块 —— 第二次按键真的收回。

**方案 A（推荐，改动小）**

- `InteractiveRenderer.expand_last()` 换成返回文本的 `expansion_text() -> str | None`，自己不再 print：
  renderer 协议保持"给自己能收回的东西"；
- runner 持有 `self._expanded: bool`；`_key_bindings()` 的 ctrl+o 翻转它并 `event.app.invalidate()`；
- `cmdloop` 的 `bottom_toolbar` 换成 `self._toolbar`：未展开=现状状态行，展开=状态行 + 展开块；
- 展开块按 `console.height - LIVE_MARGIN` 截断并注明（沿用流式那边的预算），别把提示符顶出屏幕；
- `do_stream` 开头复位 `_expanded`（工具栏本来就会随 `prompt()` 返回被擦掉，等于发下一条消息时自动收回）；
- i18n 增加 `tool_collapse_hint`，展开态提示 `ctrl+o` 收起。

代价：展开块出现在提示符**下方**（细节面板），不是原地展开。

**方案 B（原地展开，改动大）**

`cmdloop` 从 `PromptSession.prompt()` 换成自定义 `Application`/`Layout`，在输入行**上方**加一个
Window 放展开块。得到 Claude Code 那种原地形态，但提示符、补全、工具栏都要搬进自建布局，
属于一次结构性重构。若确实要求"展开在原位"，这是唯一干净的路。

**不推荐**

- 裸 ANSI 擦除（`CSI n A` + `CSI J`）原地收回：滚动、改窗口大小、期间任何异步输出都会让行数对不上，
  擦错地方，比现在更糟；
- 继续追加、第二次按键打印一份"折叠版"：并没有把内容收回去，只是又写了一行，用户抱怨的点没解决。

## 验证方式

- 单测（不需要终端）：`expansion_text()` 返回全文 / 无可展开时返回提示语；
  ctrl+o 绑定连按两次的开合状态；工具栏文本在开合两态下的内容；
- 端到端：复用 `docs/problem/repro/repro_ctrl_o_no_collapse.py`，
  修好后应输出 `BUG PRESENT: False`（第二次按键后屏幕上不再有 `output-line-12`，且 header 只出现一次）；
- 顺手把 `docs/plans/2026-09-21-tool-call-presentation-plan.md` 的 `完成标准` 里 ctrl+o 那一项按新语义更新。

## 修复记录（2026-09-21）

按方案 A 落地：

- **renderer**：`expand_last()` → `expansion_text() -> str`（`rich_renderer.py`、`contracts.py` 协议同步）。
  自己不再 `print`，把详情作为文本交回调用方。过长时按 `console.height - LIVE_MARGIN`
  截断，末行用 `tool_expand_cut` 说明丢了多少行 —— 详情现在画在终端有界的区域内，
  不能再靠 scrollback 兜长度。输出按行拆开计数，避免多行输出被算成一行而冲破预算。
- **shell**：runner 持有 `_expanded`；ctrl+o 只翻转它并 `event.app.invalidate()`；
  `cmdloop` 的 `bottom_toolbar` 换成 `_toolbar`（可调用对象，prompt_toolkit 每次重绘都会读它），
  展开时 = 详情 + 状态行，收起时 = 状态行；每个新提示符从收起态开始（含 ctrl-C 之后）。
- **i18n**：新增 `tool_collapse_hint`（详情标题行提示 ctrl+o 收起）与 `tool_expand_cut`。
- **测试**：`tests/test_rich_renderer.py` 5 个（交回详情 / 不打印任何东西 / 无可展开时的提示 /
  标题行提示收起 / 超长截断），`tests/test_interactive_cli.py` 4 个（开合 / 请求重绘 /
  工具栏是活的可调用对象 / 提交一行后收起）；`test_cmdloop_uses_prompt_session_and_injected_renderer`
  改为透过可调用对象读工具栏。
- **真实终端复验**：`docs/problem/repro/repro_ctrl_o_no_collapse.py` →
  `detail pane on screen after 1st: True`、`after 2nd: False`、`pane drawn: 1x`、`BUG PRESENT: False`。
- **形态变化**：详情出现在提示符**下方**（细节面板），不是原地展开。原地展开要换成自建
  `Application`/`Layout`（方案 B），本轮没做。
