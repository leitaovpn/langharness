"""Tests for the code workspace reading tools."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.plugins.tools.code_workspace import CodeWorkspaceToolsPlugin
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin


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
    result = {item.name: item for item in instance.get_tools()}["list_directory"].invoke(
        {}
    )
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
    result = tools(tmp_path)["read_file"].invoke(
        {"path": "a.py", "offset": 3, "limit": 2}
    )
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
