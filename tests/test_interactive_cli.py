"""Tests for interactive CLI mode."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false
# pyright: reportArgumentType=false

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import CompleteStyle

import langharness_cli.common.interactive as interactive_module
import langharness_cli.plugins.commands.health as health_module
from langharness_cli.common.interactive import InteractiveCLIRunner
from langharness_cli.common.theme import SELECTED_BACKGROUND
from langharness_cli.contracts import InteractiveCommandSpec
from langharness_cli.plugins.commands.health import HealthCommandPlugin
from langharness_cli.plugins.commands.model import ModelCommandPlugin
from langharness_cli.plugins.commands.shell import ShellCommandPlugin
from langharness_cli.plugins.rich_renderer import RichInteractiveRenderer


def test_interactive_command_spec() -> None:
    spec = InteractiveCommandSpec(
        name="health", help="check", handler=lambda context, line: False
    )
    assert spec.name == "health"
    runner = InteractiveCLIRunner(base_url="http://api", token="secret", commands=[])
    assert spec.handler(runner, "") is False


def test_interactive_runner_dispatches_slash_plugin_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = InteractiveCommandSpec(
        name="greet",
        help="Greet somebody",
        handler=lambda context, line: print(
            f"{context.base_url}:{context.token}:{line}"
        ),
    )
    runner = InteractiveCLIRunner(
        base_url="http://127.0.0.1:8000",
        token="secret",
        commands=[command],
    )
    assert runner.onecmd("/greet Ada") is False
    assert "http://127.0.0.1:8000:secret:Ada" in capsys.readouterr().out


def test_interactive_runner_reports_unknown_slash_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = InteractiveCLIRunner(base_url="http://api", token="secret", commands=[])
    assert runner.onecmd("/missing") is False
    assert "Unknown command: /missing" in capsys.readouterr().out


def test_interactive_runner_stream_request(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LANG_HARNESS_STREAM_USAGE", raising=False)

    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return [
                json.dumps(
                    {"type": "tool_call", "name": "bash", "args": {"commands": "pwd"}}
                ),
                json.dumps(
                    {"type": "tool_output", "name": "bash", "output": "/workspace"}
                ),
                json.dumps({"type": "assistant", "content": "done"}),
            ]

    captured = {}

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return FakeStreamResponse()

    monkeypatch.setattr("langharness_cli.common.interactive.httpx.stream", fake_stream)
    runner = InteractiveCLIRunner(
        base_url="http://127.0.0.1:8000",
        token="secret",
        model="deepseek-v4-flash",
        api_key="model-secret",
        model_base_url="https://models.example/v1/",
        commands=[],
        session_id="cli-session",
    )
    runner.do_stream("hello")
    output = capsys.readouterr().out
    assert "● bash" in output
    assert "⎿ /workspace" in output
    assert "done" in output
    assert captured["json"] == {
        "input": "hello",
        "model": "deepseek-v4-flash",
        "protocol": "chat",
        "api_key": "model-secret",
        "base_url": "https://models.example/v1",
        "session_id": "cli-session",
        "user_id": "local_user",
        "agent_id": "simple_agent",
    }


def stream_response(lines: list[dict]) -> object:
    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return [json.dumps(line) for line in lines]

    return FakeStreamResponse()


def test_interactive_runner_sends_explicit_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return stream_response([])

    monkeypatch.setattr("langharness_cli.common.interactive.httpx.stream", fake_stream)
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        user_id="alice",
        agent_id="researcher",
        session_id="pinned-session",
    )
    runner.do_stream("hi")
    assert captured["json"]["user_id"] == "alice"
    assert captured["json"]["agent_id"] == "researcher"
    assert captured["json"]["session_id"] == "pinned-session"


def test_interactive_runner_omits_session_id_until_server_assigns_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return stream_response([])

    monkeypatch.setattr("langharness_cli.common.interactive.httpx.stream", fake_stream)
    runner = InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[], session_id=None
    )
    runner.do_stream("hi")
    assert "session_id" not in captured["json"]


def test_interactive_runner_adopts_session_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream",
        lambda *a, **k: stream_response(
            [
                {
                    "type": "session",
                    "session_id": "server-session",
                    "agent_id": "researcher",
                    "user_id": "local_user",
                },
                {"type": "assistant", "content": "done"},
            ]
        ),
    )
    renderer = RecordingRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=renderer,
        session_id=None,
    )
    runner.do_stream("hello")

    assert runner.session_id == "server-session"
    assert runner.agent_id == "researcher"
    assert {"type": "assistant", "content": "done"} in renderer.events
    assert all(event.get("type") != "session" for event in renderer.events)


def test_interactive_runner_forwards_identity_to_real_renderer_status() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=RichInteractiveRenderer(),
        user_id="alice",
        agent_id="researcher",
        session_id="abcdef1234567890",
    )
    status = runner.renderer.get_status_text()
    assert "alice@researcher" in status
    assert "abcdef12" in status
    assert "gpt-4o-mini" in status


def test_health_provides_interactive_command() -> None:
    plugin = HealthCommandPlugin()
    commands = plugin.get_interactive_commands()
    assert [command.name for command in commands] == ["health"]


def test_interactive_runner_default_streams_non_slash(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        InteractiveCLIRunner,
        "do_stream",
        lambda self, line: print(f"stream:{line}"),
    )
    runner = InteractiveCLIRunner(
        base_url="http://127.0.0.1:8000",
        token="secret",
        commands=[],
    )
    runner.onecmd("hello agent")
    assert "stream:hello agent" in capsys.readouterr().out


def test_interactive_runner_ignores_empty_default() -> None:
    runner = InteractiveCLIRunner(base_url="http://api", token="secret", commands=[])
    assert runner.default("") is None


def test_interactive_runner_exit_commands() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://127.0.0.1:8000",
        token="secret",
        commands=[],
    )
    assert runner.do_exit("") is True
    assert runner.do_quit("") is True
    assert runner.do_EOF("") is True
    assert runner.onecmd("exit") is True
    assert runner.onecmd("quit") is True
    assert runner.onecmd("EOF") is True


def test_shell_plugin_provides_exit_and_dynamic_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    commands = ShellCommandPlugin().get_interactive_commands()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[
            *commands,
            InteractiveCommandSpec(
                name="health", help="Check health", handler=lambda context, line: False
            ),
        ],
    )

    assert runner.onecmd("/help") is False
    output = capsys.readouterr().out
    assert "/exit" in output
    assert "/help" in output
    assert "/health" in output
    assert runner.onecmd("help") is False
    assert "/health" in capsys.readouterr().out
    assert runner.onecmd("/exit") is True

    assert runner.onecmd("/help health") is False
    assert "/health: Check health" in capsys.readouterr().out
    assert runner.onecmd("/help missing") is False
    assert "Unknown command: /missing" in capsys.readouterr().out

    plugin = ShellCommandPlugin()
    assert plugin.get_commands() == []
    assert plugin.get_plugin_info() == {"name": "shell-command", "version": "1.0.0"}


def test_shell_plugin_localizes_help_in_zh(
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin = ShellCommandPlugin()
    plugin._locale = "zh"
    commands = plugin.get_interactive_commands()
    help_texts = {command.name: command.help for command in commands}
    assert help_texts["exit"] == "退出交互式 shell"
    assert help_texts["help"] == "显示可用的交互命令"

    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[
            *commands,
            InteractiveCommandSpec(
                name="health", help="Check health", handler=lambda context, line: False
            ),
        ],
    )
    assert runner.onecmd("/help missing") is False
    assert "未知命令: /missing" in capsys.readouterr().out


def test_health_interactive_handler(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Response:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    monkeypatch.setattr(health_module.httpx, "get", lambda *a, **k: Response())

    plugin = HealthCommandPlugin()
    runner = InteractiveCLIRunner(
        base_url="http://127.0.0.1:8000", token="secret", commands=[]
    )
    assert plugin._interactive_handler(runner, "") is False
    assert "{'status': 'ok'}" in capsys.readouterr().out


def test_interactive_runner_has_welcome_screen() -> None:
    welcome = InteractiveCLIRunner.intro
    assert welcome is not None
    assert "conversation" in welcome
    assert "/help" in welcome
    assert "/exit" in welcome


def test_prompt_completion_is_built_from_command_plugins() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[
            InteractiveCommandSpec(
                name="health", help="check", handler=lambda context, line: False
            )
        ],
    )
    completions = list(
        runner._command_completer().get_completions(
            Document("/he", cursor_position=3), CompleteEvent()
        )
    )
    assert [completion.text for completion in completions] == ["/health"]
    assert fragment_list_to_text(completions[0].display_meta) == "check"


def test_cmdloop_uses_prompt_session_and_injected_renderer() -> None:
    prompts = []

    class FakeSession:
        def prompt(self, *args, **kwargs):
            prompts.append((args, kwargs))
            return "/exit"

    class FakeRenderer:
        def __init__(self):
            self.welcome = ""

        def show_welcome(self, text):
            self.welcome = text

        def start_response(self):
            return None

        def render_event(self, event):
            return None

        def finish_response(self):
            return None

        def show_error(self, message):
            return None

    renderer = FakeRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=ShellCommandPlugin().get_interactive_commands(),
        renderer=renderer,
        session=FakeSession(),
    )
    runner._interactive_input = True
    runner.cmdloop()

    assert "conversation" in renderer.welcome
    assert len(prompts) == 1
    # Read through the callable: prompt_toolkit re-reads it on every repaint.
    assert prompts[0][1]["bottom_toolbar"]().startswith(" gpt-4o-mini")


class RecordingRenderer:
    def __init__(self):
        self.welcome = ""
        self.errors = []
        self.events = []

    def show_welcome(self, text):
        self.welcome = text

    def start_response(self):
        return None

    def render_event(self, event):
        self.events.append(event)

    def finish_response(self):
        return None

    def show_error(self, message):
        self.errors.append(message)


def test_cmdloop_uses_dynamic_status_toolbar_with_real_renderer() -> None:
    prompts = []

    class FakeSession:
        def prompt(self, *args, **kwargs):
            prompts.append((args, kwargs))
            return "/exit"

    renderer = RichInteractiveRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=ShellCommandPlugin().get_interactive_commands(),
        renderer=renderer,
        session=FakeSession(),
    )
    runner._interactive_input = True
    runner.cmdloop()
    toolbar = prompts[0][1]["bottom_toolbar"]
    assert callable(toolbar)
    assert toolbar().startswith(
        " local_user@simple_agent · new session · environment · gpt-4o-mini · chat"
    )


def test_runner_localizes_welcome_and_unknown_command_in_zh() -> None:
    class FakeSession:
        def prompt(self, *args, **kwargs):
            return "/exit"

    renderer = RecordingRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=ShellCommandPlugin().get_interactive_commands(),
        renderer=renderer,
        locale="zh",
        session=FakeSession(),
    )
    runner._interactive_input = True
    runner.cmdloop()
    assert renderer.welcome == "输入消息开始对话。\n/help 查看命令 · /exit 安全退出"
    runner.onecmd("/missing")
    assert renderer.errors == ["未知命令: /missing。输入 /help 查看可用命令。"]


def test_runner_localizes_cancelled_in_zh(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream", lambda *a, **k: Response()
    )
    renderer = RecordingRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        renderer=renderer,
        locale="zh",
    )
    runner.do_stream("hi")
    assert renderer.errors == ["已取消"]


def test_runner_forwards_usage_events_to_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = {
        "type": "usage",
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }

    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return [json.dumps(usage)]

    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream", lambda *a, **k: FakeStreamResponse()
    )
    renderer = RecordingRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[], renderer=renderer
    )
    runner.do_stream("hi")
    assert usage in renderer.events


def test_runner_sends_stream_usage_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            return []

    captured = {}

    def fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return FakeStreamResponse()

    monkeypatch.setattr("langharness_cli.common.interactive.httpx.stream", fake_stream)
    monkeypatch.setenv("LANG_HARNESS_STREAM_USAGE", "false")
    runner = InteractiveCLIRunner(base_url="http://api", token="secret", commands=[])
    runner.do_stream("hi")
    assert captured["json"]["stream_usage"] is False


APPROVAL_REQUEST = {
    "action_requests": [
        {"name": "write_file", "args": {"file_path": "/tmp/x.txt", "text": "hi"}}
    ],
    "review_configs": [],
}


def approval_fake_stream(
    requests: list, request: dict, lines: list[dict] = None, resume_lines: list[dict] = None
) -> object:
    """Serve the approval event on /stream and capture both HTTP calls."""

    def fake_stream(*args, **kwargs):
        requests.append({"args": args, "kwargs": kwargs})
        if args[1].endswith("/stream"):
            return stream_response(
                [{"type": "approval_required", "request": request}]
                if lines is None
                else lines
            )
        return stream_response([] if resume_lines is None else resume_lines)

    return fake_stream


def approval_runner(monkeypatch, answers: list[str], request: dict, lines: list[dict] = None):
    requests = []
    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream",
        approval_fake_stream(requests, request, lines),
    )
    answers_iter = iter(answers)
    prompts = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers_iter)

    monkeypatch.setattr("builtins.input", fake_input)
    runner = InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[], session_id="s1"
    )
    runner.do_stream("write a file")
    return runner, requests, prompts


def test_approval_approve_sends_structured_decision(monkeypatch) -> None:
    """The resume payload must carry the LangChain HITLResponse shape, not a
    bare 'approve' string."""
    _, requests, _ = approval_runner(monkeypatch, ["y"], APPROVAL_REQUEST)

    assert len(requests) == 2
    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [{"type": "approve"}]
    }


def test_approval_prompt_names_the_tool(monkeypatch) -> None:
    """The approval prompt shows the tool name from the action request."""
    _, requests, prompts = approval_runner(monkeypatch, ["n"], APPROVAL_REQUEST)

    assert "write_file" in prompts[0]
    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [{"type": "reject"}]
    }


def test_approval_edit_sends_edited_action(monkeypatch) -> None:
    _, requests, _ = approval_runner(
        monkeypatch,
        ["e", '{"file_path": "/tmp/y.txt", "text": "hello"}'],
        APPROVAL_REQUEST,
    )

    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [
            {
                "type": "edit",
                "edited_action": {
                    "name": "write_file",
                    "args": {"file_path": "/tmp/y.txt", "text": "hello"},
                },
            }
        ]
    }


def test_approval_decision_count_matches_action_requests(monkeypatch) -> None:
    """The middleware requires one decision per interrupted tool call."""
    request = {
        "action_requests": [
            {"name": "write_file", "args": {"file_path": "/tmp/x.txt", "text": "hi"}},
            {"name": "bash", "args": {"commands": "pwd"}},
        ],
        "review_configs": [],
    }
    _, requests, _ = approval_runner(monkeypatch, ["y"], request)

    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [{"type": "approve"}, {"type": "approve"}]
    }


def test_approval_edit_rejects_invalid_json(monkeypatch) -> None:
    """Invalid edited args JSON degrades to a reject with a reason."""
    _, requests, _ = approval_runner(
        monkeypatch, ["e", "not-json"], APPROVAL_REQUEST
    )

    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [{"type": "reject", "message": "Invalid edited args JSON"}]
    }


def test_approval_edit_empty_input_keeps_original_args(monkeypatch) -> None:
    _, requests, _ = approval_runner(monkeypatch, ["e", ""], APPROVAL_REQUEST)

    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [
            {
                "type": "edit",
                "edited_action": {
                    "name": "write_file",
                    "args": {"file_path": "/tmp/x.txt", "text": "hi"},
                },
            }
        ]
    }


def test_approval_non_dict_request_falls_back_to_tool_label(monkeypatch) -> None:
    """A malformed request payload still prompts with the generic label."""
    _, requests, prompts = approval_runner(monkeypatch, ["y"], "not-a-dict")

    assert "tool" in prompts[0]
    assert requests[1]["kwargs"]["json"]["decision"] == {"decisions": []}


def test_approval_resume_response_events_are_rendered(monkeypatch) -> None:
    """Events streamed by the resume turn (e.g. tool output) reach the renderer."""
    requests = []
    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.stream",
        approval_fake_stream(
            requests,
            APPROVAL_REQUEST,
            resume_lines=[{"type": "assistant", "content": "done"}],
        ),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    renderer = RecordingRenderer()
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[],
        session_id="s1",
        renderer=renderer,
    )
    runner.do_stream("write a file")

    assert requests[1]["kwargs"]["json"]["decision"] == {
        "decisions": [{"type": "approve"}]
    }
    assert {"type": "assistant", "content": "done"} in renderer.events


# --- command palette ------------------------------------------------------


def _command(name: str, help_text: str = "does a thing") -> InteractiveCommandSpec:
    return InteractiveCommandSpec(
        name=name, help=help_text, handler=lambda context, line: False
    )


def test_prompt_completion_ranks_subsequence_matches() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=[_command("session"), _command("scroll-speed"), _command("help")],
    )

    completions = list(
        runner._command_completer().get_completions(
            Document("/ss", cursor_position=3), CompleteEvent()
        )
    )

    assert [completion.text for completion in completions] == [
        "/session",
        "/scroll-speed",
    ]


def test_prompt_completion_leaves_plain_messages_alone() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[_command("help")]
    )

    completions = list(
        runner._command_completer().get_completions(
            Document("write a /he", cursor_position=11), CompleteEvent()
        )
    )

    assert completions == []


def test_model_argument_completes_from_configured_providers() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=ModelCommandPlugin().get_interactive_commands(),
        configs=SimpleNamespace(list_providers=lambda: ["demo", "prod"]),
    )

    completions = list(
        runner._command_completer().get_completions(
            Document("/model ", cursor_position=7), CompleteEvent()
        )
    )

    assert [completion.text for completion in completions] == ["demo", "prod"]


def test_prompt_session_uses_the_shared_palette_style(monkeypatch) -> None:
    captured: dict = {}

    class RecordingSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(interactive_module, "PromptSession", RecordingSession)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[_command("help")]
    )

    attrs = captured["style"].get_attrs_for_style_str(
        "class:completion-menu.completion.current"
    )

    assert attrs.bgcolor == SELECTED_BACKGROUND.removeprefix("bg:#")
    assert attrs.reverse is False


def test_cmdloop_asks_for_a_single_column_menu() -> None:
    prompts = []

    class FakeSession:
        def prompt(self, *args, **kwargs):
            prompts.append((args, kwargs))
            return "/exit"

    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        # cmdloop only stops when a command's handler returns True, so an
        # exit that actually exits is required or this spins forever.
        commands=[
            _command("help"),
            InteractiveCommandSpec(
                name="exit", help="leave", handler=lambda context, line: True
            ),
        ],
        session=FakeSession(),
    )
    runner._interactive_input = True
    runner.cmdloop()

    assert prompts[0][1]["complete_style"] == CompleteStyle.COLUMN


def _completions(runner, text: str) -> list[str]:
    return [
        completion.text
        for completion in runner._command_completer().get_completions(
            Document(text, cursor_position=len(text)), CompleteEvent()
        )
    ]


def test_a_command_can_declare_its_own_argument_completion() -> None:
    def complete(context, words, prefix):
        # Reading the context proves the spec's completer is handed one.
        return ["alpha", "beta"] if context.user_id == "local_user" else []

    command = InteractiveCommandSpec(
        name="demo", help="d", handler=lambda context, line: False, complete=complete
    )
    runner = InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[command]
    )

    assert _completions(runner, "/demo ") == ["alpha", "beta"]


def test_a_declared_completer_sees_the_deeper_slots() -> None:
    seen: list[list[str]] = []

    def complete(context, words, prefix):
        seen.append(list(words))
        return ["set"] if words == ["runtime"] else []

    command = InteractiveCommandSpec(
        name="plugins", help="p", handler=lambda context, line: False, complete=complete
    )
    runner = InteractiveCLIRunner(
        base_url="http://api", token="secret", commands=[command]
    )

    assert _completions(runner, "/plugins runtime s") == ["set"]
    assert seen == [["runtime"]]


def test_model_declares_its_own_completion_from_the_runner_providers() -> None:
    runner = InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=ModelCommandPlugin().get_interactive_commands(),
        configs=SimpleNamespace(list_providers=lambda: ["demo"]),
    )

    assert _completions(runner, "/model de") == ["demo"]


def _catalogue_response(url: str, tools: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"tools": tools}, request=httpx.Request("GET", url)
    )


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
        lambda url, **kwargs: _catalogue_response(
            url, [{"name": "bash", "headline": "Bash({commands})"}]
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
                    "name": "bash",
                    "tool_call_id": "c1",
                    "args": {"commands": "pwd"},
                },
                {
                    "type": "tool_call",
                    "name": "mystery",
                    "tool_call_id": "c2",
                    "args": {"x": 1},
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "langharness_cli.common.interactive.httpx.get",
        lambda url, **kwargs: _catalogue_response(
            url, [{"name": "bash", "headline": "Bash({commands})"}]
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

    calls = {event["name"]: event for event in seen if event["type"] == "tool_call"}
    # The known tool proves the catalogue was consulted, so the absence on
    # the unknown one means "declined", not "never looked".
    assert calls["bash"]["headline"] == "Bash(pwd)"
    assert "headline" not in calls["mystery"]


def test_a_failing_catalogue_leaves_the_transcript_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    class CapturingRenderer(RecordingRenderer):
        def render_event(self, event):
            seen.append(dict(event))

    attempts: list[str] = []

    def refuse(url, **kwargs):
        attempts.append(url)
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

    # Counting the attempt keeps this from passing before the catalogue
    # exists at all: the point is that a failed lookup is survivable.
    assert attempts == ["http://api/agents/simple_agent/tools"]
    assert any(event["type"] == "assistant" for event in seen)


DETAIL = "args: {'commands': 'pwd'}"


class _RendererWithDetail(RichInteractiveRenderer):
    """A renderer whose last response has something to expand."""

    def expansion_text(self) -> str:
        return DETAIL


class _KeyPress:
    """Enough of prompt_toolkit's key event to carry one press."""

    def __init__(self) -> None:
        self.repaints = 0
        self.app = self

    def invalidate(self) -> None:
        self.repaints += 1


def _ctrl_o(runner: InteractiveCLIRunner):
    bindings = runner._key_bindings()
    return next(
        binding for binding in bindings.bindings if binding.keys == (Keys.ControlO,)
    )


def _runner_with_detail(**kwargs) -> InteractiveCLIRunner:
    return InteractiveCLIRunner(
        base_url="http://api",
        token="secret",
        commands=kwargs.pop("commands", []),
        renderer=_RendererWithDetail(),
        **kwargs,
    )


def test_ctrl_o_opens_the_detail_and_the_same_key_closes_it() -> None:
    runner = _runner_with_detail()
    ctrl_o = _ctrl_o(runner)
    event = _KeyPress()

    ctrl_o.handler(event)
    opened = runner._toolbar()

    ctrl_o.handler(event)
    closed = runner._toolbar()

    assert DETAIL in opened
    assert DETAIL not in closed


def test_ctrl_o_asks_prompt_toolkit_to_repaint() -> None:
    """Erasing the detail is prompt_toolkit's job, not a reprint's.

    Without the invalidate the pane would never be drawn or taken back, which
    is the shape of the bug this replaced: a key that only ever adds.
    """
    runner = _runner_with_detail()
    event = _KeyPress()

    _ctrl_o(runner).handler(event)

    assert event.repaints == 1


def test_the_prompt_gets_a_toolbar_it_reads_when_it_draws() -> None:
    """A callable, not a snapshot: the pane opens while the prompt is running."""
    toolbars: list[object] = []

    class FakeSession:
        def prompt(self, *args, **kwargs):
            toolbar = kwargs["bottom_toolbar"]
            toolbars.append(toolbar)
            # The key is pressed while the prompt is up, then it repaints.
            _ctrl_o(runner).handler(_KeyPress())
            return "/exit"

    runner = _runner_with_detail(
        commands=[
            _command("help"),
            InteractiveCommandSpec(
                name="exit", help="leave", handler=lambda context, line: True
            ),
        ],
        session=FakeSession(),
    )
    runner._interactive_input = True

    runner.cmdloop()

    assert callable(toolbars[0])
    assert DETAIL in toolbars[0]()


def test_submitting_a_line_takes_the_detail_down() -> None:
    """The pane describes the prompt it was opened at, not the next one."""
    seen: list[str] = []

    class FakeSession:
        def __init__(self) -> None:
            self.lines = ["/noop", "/exit"]

        def prompt(self, *args, **kwargs):
            toolbar = kwargs["bottom_toolbar"]
            if not seen:
                # Open the pane the way a person would: at the prompt.
                _ctrl_o(runner).handler(_KeyPress())
            seen.append(toolbar())
            return self.lines.pop(0)

    runner = _runner_with_detail(
        commands=[
            _command("noop"),
            InteractiveCommandSpec(
                name="exit", help="leave", handler=lambda context, line: True
            ),
        ],
        session=FakeSession(),
    )
    runner._interactive_input = True

    runner.cmdloop()

    assert DETAIL in seen[0]
    assert DETAIL not in seen[1]
