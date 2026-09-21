"""Localized UI strings for the CLI."""

from __future__ import annotations

import os

LOCALES = ("en", "zh")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "intro": (
            "Type a message to start a conversation.\n"
            "/help shows commands · /exit leaves safely"
        ),
        "toolbar_hint": "/help for commands",
        "tokens": "{count} tokens",
        "cancelled": "Cancelled",
        "unknown_command": "Unknown command: /{name}. Type /help for available commands.",
        "error_prefix": "Error",
        "thinking": "thinking…",
        "tool_error": "error",
        "tool_expand_hint": " (ctrl+o to expand)",
        "tool_no_output": "(no output)",
        "tool_expand_header": "Tool calls from the last response:",
        "tool_expand_args": "args:",
        "tool_expand_output": "output:",
        "tool_nothing_to_expand": "Nothing to expand yet.",
        "tool_collapse_hint": " (ctrl+o to collapse)",
        "tool_expand_cut": "… ({count} more lines)",
        "usage_io": "{in_tokens} in / {out_tokens} out",
        "help_exit": "Exit the interactive shell",
        "help_help": "Show available interactive commands",
        "help_unknown": "Unknown command: /{name}",
        "help_model": "Show or switch the active model provider",
        "model_current": "Current provider: {name}",
        "model_available": "Available providers: {names}",
        "model_unknown": "Unknown provider: {name}",
        "model_switched": "Switched to {name} ({model}, {protocol})",
        "model_none": "No model providers are configured",
        "session_new": "new session",
        "help_agents": "List the agents available on the API server",
        "help_agent": "Show or switch the active agent",
        "help_sessions": "List recent sessions for the current user",
        "help_new": "Start a fresh session with the next message",
        "help_whoami": "Show user, agent, and session identity",
        "agents_header": "Available agents:",
        "agents_none": "No agents are registered",
        "agent_current": "Current agent: {name}",
        "agent_switched": "Switched to agent {name}",
        "agent_unknown": "Unknown agent: {name}",
        "agent_usage": "Usage: /agent <id>",
        "sessions_header": "Recent sessions for {user}:",
        "sessions_none": "No sessions found for {user}",
        "session_disabled": "disabled",
        "new_session_pending": "Next message starts a new session",
        "whoami": "user {user} · agent {agent} · session {session} · {base_url}",
        "identity_error": "Identity request failed: {detail}",
    },
    "zh": {
        "intro": "输入消息开始对话。\n/help 查看命令 · /exit 安全退出",
        "toolbar_hint": "/help 查看命令",
        "tokens": "{count} 令牌",
        "cancelled": "已取消",
        "unknown_command": "未知命令: /{name}。输入 /help 查看可用命令。",
        "error_prefix": "错误",
        "thinking": "思考中…",
        "tool_error": "错误",
        "tool_expand_hint": " (ctrl+o 展开)",
        "tool_no_output": "（无输出）",
        "tool_expand_header": "上一次响应的工具调用：",
        "tool_expand_args": "参数：",
        "tool_expand_output": "输出：",
        "tool_nothing_to_expand": "暂无可展开内容。",
        "tool_collapse_hint": " (ctrl+o 收起)",
        "tool_expand_cut": "…（还有 {count} 行）",
        "usage_io": "输入 {in_tokens} / 输出 {out_tokens}",
        "help_exit": "退出交互式 shell",
        "help_help": "显示可用的交互命令",
        "help_unknown": "未知命令: /{name}",
        "help_model": "显示或切换当前模型 provider",
        "model_current": "当前 provider：{name}",
        "model_available": "可用 providers：{names}",
        "model_unknown": "未知 provider：{name}",
        "model_switched": "已切换到 {name}（{model}，{protocol}）",
        "model_none": "没有配置模型 provider",
        "session_new": "新会话",
        "help_agents": "列出 API 服务上可用的 agent",
        "help_agent": "查看或切换当前 agent",
        "help_sessions": "列出当前用户的最近会话",
        "help_new": "下一条消息开始一个新会话",
        "help_whoami": "显示 user、agent、session 身份",
        "agents_header": "可用 agent：",
        "agents_none": "没有注册任何 agent",
        "agent_current": "当前 agent：{name}",
        "agent_switched": "已切换到 agent {name}",
        "agent_unknown": "未知 agent：{name}",
        "agent_usage": "用法：/agent <id>",
        "sessions_header": "{user} 的最近会话：",
        "sessions_none": "{user} 没有历史会话",
        "session_disabled": "已禁用",
        "new_session_pending": "下一条消息将开始新会话",
        "whoami": "user {user} · agent {agent} · session {session} · {base_url}",
        "identity_error": "身份查询失败：{detail}",
    },
}


def tr(locale: str, key: str, **fmt: object) -> str:
    """Return the localized string for `key`, falling back to English."""
    table = STRINGS.get(locale, STRINGS["en"])
    template = table.get(key, STRINGS["en"].get(key, key))
    return template.format(**fmt) if fmt else template


def get_locale() -> str:
    """Resolve the UI locale from LANG_HARNESS_LOCALE (default "en")."""
    locale = os.environ.get("LANG_HARNESS_LOCALE", "en").lower()
    return locale if locale in LOCALES else "en"
