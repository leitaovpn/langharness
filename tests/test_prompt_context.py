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


def call(name: str, **args: object) -> AIMessage:
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
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
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
    assert touched_paths_from_messages(
        [call("list_directory", dir_path=".")], str(tmp_path)
    ) == ()


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
