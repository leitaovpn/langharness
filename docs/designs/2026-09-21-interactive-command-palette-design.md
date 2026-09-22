# 交互式命令行补全菜单设计方案

## 目标

把交互式 CLI 的补全体验做成 Claude Code 那种"输入 `/` 弹出候选菜单"
的形态：一行一个命令、右侧一列描述、随输入实时模糊过滤、方向键选择、
超出可视区可滚动。

## 背景与现状

当前实现见 [interactive.py](../../src/langharness_cli/common/interactive.py)：

- `InteractiveCLIRunner._command_completer()` 返回 `WordCompleter`，
  用 `meta_dict` 提供右侧描述；
- `cmdloop()` 用 `CompleteStyle.MULTI_COLUMN` 渲染菜单。

`MULTI_COLUMN` 会把候选项横向铺成多列（`MultiColumnCompletionMenuControl`），
描述列会被挤掉，与目标形态不符。目标形态对应 `CompleteStyle.COLUMN`
（`CompletionMenuControl`，一行一项 + 右列 meta）。

## prompt_toolkit 能力实测

版本 3.0.53。以下结论均为实跑验证，不是文档推断：

| 输入 | 实际候选 | 结论 |
|---|---|---|
| `/ss` | `/session`, `/scroll-speed` | 子序列匹配与排序已内置 |
| `hello wo` | 无 | 普通消息不会触发补全 |
| `write a /he` | 无 | 句子中间不会触发 |
| `/model ` | **无** | 参数段需要绕开模糊层 |
| 自定义 `display="[cmd] /scope"` | 退回 `/scope` | 内置 `_get_display` 会丢弃自定义 display |

内置可用资产：`FuzzyCompleter`（子序列匹配 + 按 `(start_pos, match_length)`
排序）、`display_meta` 透传、`fuzzymatch.outside` / `.inside` /
`.inside.character` 样式类、`completion-menu.completion.current` 选中态。

已知限制：

- 排序只有两级，无法表达"连续命中优于分散命中"；
- 分组所需的分隔线在扁平候选列表里无原生支持；
- `max_number_of_completions` 挂在 `Buffer` 上，`PromptSession` 取不到。

## 总体架构

分层，边界判据是**依赖方向**：

```text
palette.py      候选构造 + 打分排序     ← 零 ptk / 零 rich
completion.py   PaletteCompleter       ← 唯一 import prompt_toolkit 的地方
theme.py        Style 单一来源          ← ptk 样式类名字面量
interactive.py  接线
rich_renderer.py 欢迎横幅
```

`palette.py` 必须能在未安装 prompt_toolkit 的解释器里 import。这条不是
洁癖：打分函数是本功能里唯一有真实逻辑、也唯一会出错的部分（排序稳定性、
大小写、空查询、平局规则），把它做成纯函数才能用确定性测试锁住；
依赖 ptk 就只能靠抓屏碰运气。

## 打分函数

```python
@dataclass(frozen=True)
class Candidate:
    name: str          # "/scope"
    description: str   # "管理 agent 作用域"

@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    score: int
    hits: tuple[int, ...]   # 命中字符下标，供高亮

def match(query: str, target: str) -> tuple[int, ...] | None
def rank(query, candidates, *, limit=None) -> list[Scored]
```

规则：

- 空查询 → 全部候选，按名称字典序（不依赖 dict 插入序）；
- 大小写不敏感子序列匹配，不匹配返回 `None`；
- 加分：连续命中 > 词首命中（`/`、`-`、`_`、`.` 之后）> 靠左命中；
- 排序键 `(-score, len(name), name)`。**末项是平局兜底**，写死以保证
  测试不因候选顺序变化而偶然变红。

测试断言的是**排序关系**而非具体分值——分值是实现细节，排序是契约。

## 参数分流

```python
def get_completions(self, document, complete_event):
    head = document.text_before_cursor
    if not head.startswith("/"):
        return                       # 普通消息不触发
    if " " not in head:
        ...                          # 命令段：走模糊
    else:
        name, _, rest = head[1:].partition(" ")
        if " " in rest:
            return                   # 第三段起不补全
        ...                          # 参数段：直通，start_position=-len(rest)
```

模糊层 `FuzzyCompleter(inner, enable_fuzzy=<Filter>)` 只在命令段生效，
正好补上实测中 `/model ` 返回空的洞。

参数候选来源是 `Mapping[str, Callable[[str], Iterable[str]]]`，本次只接
`model` → `runner.list_providers()` 与 `plugins` → 插件名。

## 视觉

- `CompleteStyle.COLUMN`；
- 命中高亮复用内置 `fuzzymatch.*` 样式类，`display` 由本模块构造——
  不是要加图标，而是内置 `_get_display` 会丢弃自定义 display（见实测表）；
- 候选不截断（当前 11 个命令），靠原生滚动；此点由 demo 证实而非假设；
- 欢迎横幅用 rich `Table.grid` 双列，宽度 < 80 退化为上下堆叠。

## 验证策略

三层，只有第三层需要终端：

1. `palette.py` → 纯函数单测：打分、平局、空查询、大小写、无匹配、limit；
2. `completion.py` → 构造 `Document` / `CompleteEvent` 断言候选与
   `start_position`（现有 [test_interactive_cli.py](../../tests/test_interactive_cli.py)
   已有此模式）；
3. 菜单渲染 → demo 经 `create_pipe_input()` 喂按键、`Vt100_Output` 抓
   ANSI，再用 VT100 模拟器还原成可见屏幕。这是"demo 验证证据"的形态，
   可重跑，不依赖人手操作。

## 实现中发现的事实

以下四条是写 demo 时实测出来的，与设计初稿不同，以本节为准。

**① 不需要 `FuzzyCompleter` 包装。** 初稿打算用
`FuzzyCompleter(inner, enable_fuzzy=Filter)` 补 `/model ` 的洞。但既然
`palette.rank` 已经做了模糊匹配，再包一层只会让它的 `_get_display` 覆盖掉
我们构造的 `display`（实测：自定义 display 被丢弃），反而丢掉命中高亮。
`PaletteCompleter` 直接用自己的引擎，`enable_fuzzy` 那套复杂度整个消失。

**② 选中即插入，无法"只高亮不改文本"。** `Buffer.go_to_completion()`
会把 document 设成候选文本（[buffer.py](../../.venv/lib/python3.13/site-packages/prompt_toolkit/buffer.py)）。
曾按"高亮第一行"的直觉加了 `on_completions_changed` 钩子自动选中，
实测把输入变成了 `/exitss` —— 敲 `/` 就被 `/exit` 替换。
prompt_toolkit 没有"视觉选中但不落字"的模式，**该钩子已删除**。
后果：菜单打开时首行不高亮，按 ↓/Tab 才选中（此时文本会被替换）。
若要完全对齐截图，需要一个自定义 completion menu 控件，本次不做。

**③ 默认样式会经合并泄漏进来。** prompt_toolkit 默认样式含
`completion-menu.completion.current: "fg:#888888 bg:#ffffff reverse"`，
而样式合并是**逐属性**的：我们的规则没写 `reverse`，默认的
`reverse=True` 就保留，实测选中行渲染成 `48;5;25;...;7`（白底蓝字）。
必须在规则里显式写 `noreverse`。
这个 bug 单测样式本身查不出来（隔离样式里没有 merge），只有 demo 抓真帧才能发现。

**④ 裸 `/` 是触发符不是过滤器。** 若把 `"/"` 当查询串交给 `rank`，
排序会退化成"按名称长度"，`/` 之后的第一屏看起来毫无规律。
`PaletteCompleter` 对裸 `/` 传空查询，得到完整字母序列表。

**⑤ 参数候选按前缀过滤，不走模糊。** 命令名是封闭集，模糊合适；
provider 名、路径是开放集，散射匹配错多于对，故参数段用
大小写不敏感前缀匹配。

## 后续修正：嵌套语法

初版只补全**第一个**参数，且候选源签名是 `(prefix) -> values`，拿不到
前面的词。`/plugins` 的语法有 2~4 层（`runtime set <scope> <plugin>
KEY=VALUE`、`config enable|disable <scope> <plugin>`），这个签名表达不了，
所以 `/plugins discover` 不会补全，而且注册候选源也修不好第二层。

两处改动：

- `ArgumentSource` 改为 `(words, prefix) -> values`，`PaletteCompleter`
  把 `head[1:].split(" ")` 拆成 (命令, 已输入的参数词, 当前词前缀)；
  "第三段起一律不补全"那条硬规则删除，改由候选源自己决定。
- `InteractiveCommandSpec` 增加可选 `complete` 字段。补全语法归**命令
  插件**所有，runner 不再持有任何语法。这推翻了本设计初稿的"非目标"
  里"候选来源协议化"一项——当时判为 YAGNI，但嵌套语法的需求出现后，
  把动作表复制到 runner（第三份副本）才是真正的错。

### `/plugins` 语法表

`/plugins` 的合法动作原先写在三处并对不上：argparse `choices` 有
`upgrade` 没有 `set`/`history`/`rollback`，交互 `_handle` 反过来，
`_usage()` 又是第三份手写字符串。git 历史显示这是**逐次提交 accretion**
的结果而非设计决定（`f8664c9` 引入 `_handle` 时只带该提交需要的 6 个
动作，后续提交按需追加，`upgrade` 始终没人需要）。

现在收敛为一张 `ACTIONS` 表，`Position` 描述每个槽位，`Action` 用多个
`forms` 表达 `config` 这类多形态动作。四个消费点全部派生：`_handle`
的动作校验、`usage_text()`、补全、argparse `choices`。每个动作显式声明
`interactive` / `cli` 支持面——漂移从"没人写过"变成"写了但写错"。

补全只提供本地可枚举的槽位（runtime scope、config scope、动词）；插件名、
版本号、`KEY=VALUE` 都在服务端，返回空而不是阻塞按键。

## 非目标

- 分组分隔线（扁平列表无原生支持，11 个命令收益不足）；
- 补全菜单以外的 TUI 改造；
- 统一两个前端的调用约定。交互模式用位置参数传 scope
  （`/plugins install <pkg> <contrib> <scope>`），非交互 CLI 用 `--scope`
  标志（`plugins install <pkg> <contrib> --scope X`）。语法表目前只描述
  交互形态，argparse 侧取动作集。合并两套约定是破坏性的 CLI 行为变更，
  不混在这个修复里。
