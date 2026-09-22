"""Tests for workspace text search."""

from __future__ import annotations

import re
import shutil
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


def test_find_ripgrep_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the stdlib module itself: ``search`` holds the same object, so
    # this affects its lookup without reaching into a private attribute.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert search.find_ripgrep() is None


def test_find_ripgrep_returns_the_resolved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/opt/bin/{name}")
    assert search.find_ripgrep() == "/opt/bin/rg"


def test_regex_metacharacters_are_honoured(tmp_path: Path) -> None:
    write(tmp_path / "a.py", "def foo():\ndef bar():\n")
    outcome = search.search_workspace(tmp_path, r"^def \w+\(")
    assert len(outcome.matches) == 2
    assert re.match(r"^def ", outcome.matches[0].text)


def test_an_unreadable_file_is_skipped(tmp_path: Path) -> None:
    target = tmp_path / "secret.py"
    write(target, "hit\n")
    target.chmod(0o000)
    try:
        assert search.search_workspace(tmp_path, "hit").matches == []
    finally:
        target.chmod(0o600)


def test_ripgrep_path_prefixes_are_normalised(tmp_path: Path) -> None:
    # rg emits "./a.py"; the reported path must be workspace-relative.
    binary = fake_ripgrep(tmp_path, "printf './deep/a.py:3:2:hit\\n'")
    outcome = search.search_workspace(tmp_path, "hit", ripgrep=binary)
    assert outcome.matches == [
        search.SearchMatch(path="deep/a.py", line=3, column=2, text="hit")
    ]


def test_malformed_ripgrep_lines_are_ignored(tmp_path: Path) -> None:
    binary = fake_ripgrep(
        tmp_path,
        "printf 'garbage\\na.py:notanumber:1:hit\\na.py:7:4:real\\n'",
    )
    outcome = search.search_workspace(tmp_path, "hit", ripgrep=binary)
    assert outcome.matches == [
        search.SearchMatch(path="a.py", line=7, column=4, text="real")
    ]


def test_ripgrep_with_a_glob_passes_it_through(tmp_path: Path) -> None:
    # The fake rg echoes its own argv so the filter is observable.
    binary = fake_ripgrep(tmp_path, 'printf "./a.py:1:1:%s\\n" "$*"')
    outcome = search.search_workspace(tmp_path, "hit", glob="*.py", ripgrep=binary)
    assert "--glob *.py" in outcome.matches[0].text
