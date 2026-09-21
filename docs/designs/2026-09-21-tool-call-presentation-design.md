# 工具调用展示改造设计方案

## 目标

把交互式 CLI 的前端显示改成 Claude Code 那种形态：助手消息用 `●` 起行，
工具调用折叠成一行语义标题（`● Bash(python3 main.py)`），输出跟在
`⎿` 之后，需要时可按 `ctrl+o` 展开细节。

## 现状

[rich_renderer.py](../../src/langharness_cli/plugins/rich_renderer.py) 目前
把工具调用画成 `✓ bash (250ms · 2.3KB)`：显示工具名、耗时和字节数，**不显
示参数语义**。运行中显示 `⠋ bash {'commands': 'pwd'}` —— 直接把 Python
repr 丢给人看。

真实工具面比看上去大：

| 来源 | 工具 |
|---|---|
| `FileManagementToolkit`（第三方） | `read_file` `write_file` `list_directory` `file_search` `move_file` `copy_file` `file_delete` |
| `management.py` | `list_scope_tree` `list_runtime_plugins` `discover_plugins` `install_plugin` `enable_plugin` `disable_plugin` `upgrade_plugin` `uninstall_plugin` `update_plugin_properties` |
| `workspace.py` / `tools.py` | `bash` / `add` |
| `export_adapter.py` | 由 `ToolExport` 描述符在运行时生成 |

## 数据流

```text
工具插件声明        name → 模板
      ↓  ToolProvider.get_tool_presentations()
agent_loop.describe()  聚合（新增 tool_headlines 键）
      ↓  GET /agents/{agent_id}/tools
CLI runner        懒加载 + 按 agent 缓存，套模板
      ↓  render_event({... "headline": "Bash(pwd)"})
renderer          画 headline，不知道模板的存在
```

职责线：**runner 管传输与解析（HTTP、身份、套模板），renderer 管画**。
模板跨进程传，填充在 CLI 本地完成 —— API 里不出现展示文案。

套模板放在 runner 而不是 renderer，是因为解析模板需要当前 agent 身份和
一次 HTTP 取目录，那本来就是传输层的事。renderer 只读
`event["headline"]`，缺失时回退到工具名 —— 因此**不需要给
`InteractiveRenderer` 协议加任何方法**，renderer 也不必知道 agent 是谁。

## 描述符：模板字符串

```python
"bash":           "Bash({commands})"
"read_file":      "Read({file_path})"
"install_plugin": "install_plugin({package_id} → {scope_id})"
```

JSON 可表达、无代码过线，多参数与自定义分隔符都能写。

渲染规则（纯函数，见测试策略）：

- 占位符逐个替换，值先压成单行再按长度截断；
- **任一占位符缺失即整体返回 `None`**，由调用方回退。缺参数时打印
  `install_plugin(real.echo → ?)` 比不打印更糟；
- 模板本身的格式错误（括号不配等）一律捕获并返回 `None`。渲染器永远
  不能因为一个坏模板崩掉。

`list_runtime_plugins` 这类可选参数由作者写成 `list_runtime_plugins()`
——作者知道自己工具哪些参数必然存在。

## 目录获取

`AgentLoopProvider.describe()` 新增 `tool_headlines: {name: template}` 键。
**不改动已有的 `tools` 键**：`test_e2e.py` 有五处断言它的形状、一个 e2e
辅助函数和 demo 在读它，把 `["add"]` 换成对象是纯破坏。

新路由 `GET /agents/{agent_id}/tools` → `{"tools": [{"name", "headline"}]}`。

CLI 侧按 agent 缓存目录：成功缓存结果，失败缓存空表并且在下一次
`start_response()` 清空缓存 —— 这样服务端故障时每个响应最多只重试一次，
不会变成每个工具调用一次。

`ToolProvider` 协议增加 `get_tool_presentations() -> Mapping[str, str]`，
四个实现都要提供（`workspace`、`management`、`export_adapter`、
`tools`）。`ToolExport` 增加可选 `headline` 字段，让导出的方法也能声明。
每个 provider 有一个测试断言「它返回的每个工具都有标题」，缺一个就红 ——
和 `/plugins` 语法表同样的防漂移手法。

## 渲染形态

```text
> 评价一下 main.py

● 我先看一下 main.py 和项目结构。

  ● Read(main.py)
    ⎿ 42 lines
  ● Bash(python3 main.py)
    ⎿ hello world
  ● write_file(notes.md)
    ⎿ 12 lines, 8.4KB (ctrl+o to expand)
```

`(ctrl+o to expand)` **只在输出确实被截断时出现**。它是提示不是装饰：
输出放得下就不显示，用户不必去按一个没有新信息的键。

不做调用聚合（`Read 1 file, listed 1 directory` 那种跨调用计数行）——
一次调用一行。

## 回退

未声明模板的工具显示 `● tool_name`，**不显示裸参数**。理由：`ctrl+o`
是兜底通道 —— 名字行给概览，展开给全部细节，比回退到 repr 更干净。

## ctrl+o

- 绑在 `PromptSession` 的 `key_bindings` 上，优先于默认绑定；
- **覆盖 readline 的 `operate-and-get-next`**（prompt_toolkit 的 emacs 绑定
  把 `c-o` 给了它）。取舍：那个功能罕用，而目标形态明确要求 ctrl+o；
- 回调 `renderer.expand_last()`：重打上一次响应中每个工具调用的完整参数
  与完整输出；
- **流式进行中不可用**：那时终端由 rich `Live` 占有，不在提示符上。这与
  Claude Code 一致 —— 它也在提示符上才响应。

## 不改的东西

提示符保持 `langharness> `。截图里的 `>` 就是提示符本身，用户消息不需要
额外回显渲染 —— 终端已经在打它了。

## 测试

四层，只有最后一层需要终端：

1. 模板渲染 → 纯函数单测：缺占位符、坏模板、超长值、None 值、空 args；
2. 每个 `ToolProvider` 的工具标题覆盖 → 断言无遗漏（防漂移）；
3. 目录路由 → API 契约测试，沿用 [test_api_contracts.py](../../tests/test_api_contracts.py)；
4. 渲染形态与 `ctrl+o` → 复用 [test_rich_renderer.py](../../tests/test_rich_renderer.py)
   的 VT100 模拟器；键绑定用直调回调测，不依赖真按键。

## 非目标

- 调用聚合行；
- 服务端生成展示文案（模板跨进程传，渲染在 CLI）；
- 把 `ToolProvider` 的标题映射改成 `tool.metadata` 承载 —— 可行但会给第三方
  工具对象打补丁，收益不足以抵消隐蔽性；
- 除工具行以外的 transcript 排版。
