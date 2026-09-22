"""Tests for repository instruction discovery and budgeting."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from langharness_core.plugins.instructions.resolver import (
    DEFAULT_BUDGET_BYTES,
    InstructionResolver,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_root_instructions_are_found_without_touched_paths(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root rules")
    selection = InstructionResolver(tmp_path).resolve(())
    assert [item.relative for item in selection.files] == ["AGENTS.md"]
    assert selection.files[0].text == "root rules"
    assert selection.files[0].depth == 0


def test_deeper_instructions_cover_their_own_subtree(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "src" / "AGENTS.md", "src")
    write(tmp_path / "src" / "deep" / "AGENTS.md", "deep")
    write(tmp_path / "other" / "AGENTS.md", "other")
    selection = InstructionResolver(tmp_path).resolve(("src/deep/a.py",))
    assert [item.relative for item in selection.files] == [
        "AGENTS.md",
        "src/AGENTS.md",
        "src/deep/AGENTS.md",
    ]


def test_a_sibling_subtree_is_not_loaded(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "other" / "AGENTS.md", "other")
    selection = InstructionResolver(tmp_path).resolve(("src/a.py",))
    assert [item.relative for item in selection.files] == ["AGENTS.md"]


def test_touched_directory_paths_are_followed(tmp_path: Path) -> None:
    write(tmp_path / "src" / "AGENTS.md", "src")
    selection = InstructionResolver(tmp_path).resolve(("src",))
    assert [item.relative for item in selection.files] == ["src/AGENTS.md"]


def test_paths_outside_the_workspace_are_ignored(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    selection = InstructionResolver(tmp_path).resolve(("../../etc/passwd",))
    assert [item.relative for item in selection.files] == ["AGENTS.md"]


def test_symlink_escape_is_ignored(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-escape"
    write(outside / "AGENTS.md", "secret")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    selection = InstructionResolver(tmp_path).resolve(("link/a.py",))
    assert [item.relative for item in selection.files] == []


def test_extra_names_are_loaded_only_when_requested(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "root")
    write(tmp_path / "CLAUDE.md", "compat")
    default = InstructionResolver(tmp_path).resolve(())
    assert [item.relative for item in default.files] == ["AGENTS.md"]
    extended = InstructionResolver(tmp_path, names=("AGENTS.md", "CLAUDE.md")).resolve(
        ()
    )
    assert [item.relative for item in extended.files] == ["AGENTS.md", "CLAUDE.md"]


def test_budget_keeps_the_root_and_the_nearest_instruction(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 100)
    write(tmp_path / "src" / "AGENTS.md", "m" * 100)
    write(tmp_path / "src" / "deep" / "AGENTS.md", "n" * 100)
    selection = InstructionResolver(
        tmp_path, budget=250, names=("AGENTS.md",)
    ).resolve(("src/deep/a.py",))
    assert [item.relative for item in selection.files] == [
        "AGENTS.md",
        "src/deep/AGENTS.md",
    ]
    assert selection.omitted == ("src/AGENTS.md",)


def test_oversized_root_instruction_is_truncated_not_dropped(tmp_path: Path) -> None:
    write(tmp_path / "AGENTS.md", "r" * 1000)
    selection = InstructionResolver(tmp_path, budget=200).resolve(())
    assert len(selection.files) == 1
    assert selection.files[0].truncated is True
    assert len(selection.files[0].text.encode("utf-8")) <= 100
    assert selection.files[0].sha256.startswith("sha256:")


def test_cache_reuses_text_until_the_file_changes(tmp_path: Path) -> None:
    target = write(tmp_path / "AGENTS.md", "first")
    resolver = InstructionResolver(tmp_path)
    assert resolver.resolve(()).files[0].text == "first"
    target.write_text("second version", encoding="utf-8")
    os.utime(target, (1, 1))
    assert resolver.resolve(()).files[0].text == "second version"


def test_hash_is_stable_when_only_the_timestamp_changes(tmp_path: Path) -> None:
    target = write(tmp_path / "AGENTS.md", "stable")
    resolver = InstructionResolver(tmp_path)
    before = resolver.resolve(()).files[0].sha256
    os.utime(target, (1, 1))
    assert resolver.resolve(()).files[0].sha256 == before


def test_a_directory_named_like_an_instruction_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").mkdir()
    assert InstructionResolver(tmp_path).resolve(()).files == ()


def test_a_file_that_cannot_be_inspected_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / "AGENTS.md", "root")

    def boom(self: Path, **kwargs: object) -> os.stat_result:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "stat", boom)
    assert InstructionResolver(tmp_path).resolve(()).files == ()


def test_directory_chain_is_shallowest_first(tmp_path: Path) -> None:
    resolver = InstructionResolver(tmp_path)
    chain = resolver.directories(("src/deep/a.py",))
    assert [(path.name, depth) for path, depth in chain] == [
        (tmp_path.name, 0),
        ("src", 1),
        ("deep", 2),
    ]


def test_default_budget_is_64_kib() -> None:
    assert DEFAULT_BUDGET_BYTES == 65_536
