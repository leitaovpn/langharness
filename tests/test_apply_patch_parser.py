
"""Tests for the apply_patch parser and text applier."""

from __future__ import annotations

import pytest

from langharness_core.plugins.tools.patch import (
    FilePatch,
    Hunk,
    HunkLine,
    PatchError,
    PatchPlan,
    apply_to_text,
    parse_patch,
)


def update(*hunks: Hunk) -> FilePatch:
    return FilePatch(
        kind="update", path="a.py", move_to=None, hunks=hunks, lines=()
    )


def test_parses_an_update_with_context_and_changes() -> None:
    plan = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@ def target(\n"
        " unchanged\n"
        "-old line\n"
        "+new line\n"
        "*** End Patch\n"
    )
    assert len(plan.files) == 1
    file_patch = plan.files[0]
    assert file_patch.kind == "update"
    assert file_patch.path == "a.py"
    assert [line.kind for line in file_patch.hunks[0].lines] == [
        "context",
        "remove",
        "add",
    ]
    assert file_patch.hunks[0].lines[1].text == "old line"
    assert file_patch.hunks[0].anchor == "def target("


def test_parses_an_add_file() -> None:
    plan = parse_patch(
        "*** Begin Patch\n*** Add File: b.py\n+first\n+second\n*** End Patch\n"
    )
    assert plan.files == (
        FilePatch(
            kind="add", path="b.py", move_to=None, hunks=(), lines=("first", "second")
        ),
    )


def test_parses_a_delete_file() -> None:
    plan = parse_patch("*** Begin Patch\n*** Delete File: c.py\n*** End Patch\n")
    assert plan.files[0].kind == "delete"
    assert plan.files[0].path == "c.py"


def test_parses_a_move_inside_an_update() -> None:
    plan = parse_patch(
        "*** Begin Patch\n*** Update File: a.py\n*** Move to: d.py\n"
        "@@\n-a\n+b\n*** End Patch\n"
    )
    assert plan.files[0].move_to == "d.py"


def test_missing_begin_marker_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Update File: a.py\n-a\n+b\n*** End Patch\n")
    assert caught.value.code == "patch_invalid"
    assert "Begin Patch" in caught.value.message


def test_missing_end_marker_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** Delete File: a.py\n")
    assert caught.value.code == "patch_invalid"
    assert "End Patch" in caught.value.message


def test_empty_patch_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** End Patch\n")
    assert "no file sections" in caught.value.message


def test_unknown_marker_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** Rename File: a.py\n*** End Patch\n")
    assert "unrecognised" in caught.value.message


def test_an_update_with_no_hunks_is_rejected() -> None:
    with pytest.raises(PatchError) as caught:
        parse_patch("*** Begin Patch\n*** Update File: a.py\n*** End Patch\n")
    assert "no hunks" in caught.value.message


def test_apply_replaces_a_matched_block() -> None:
    file_patch = update(
        Hunk(
            (
                HunkLine("context", "keep"),
                HunkLine("remove", "old"),
                HunkLine("add", "new"),
            )
        )
    )
    assert apply_to_text("keep\nold\n", file_patch) == "keep\nnew\n"


def test_apply_picks_the_occurrence_after_the_anchor() -> None:
    # Both blocks match the hunk; the anchor decides which one is edited.
    original = "def target(\n    old\n\ndef other(\n    old\n"
    file_patch = update(
        Hunk(
            (HunkLine("remove", "    old"), HunkLine("add", "    new")),
            anchor="def other(",
        )
    )
    assert apply_to_text(original, file_patch) == (
        "def target(\n    old\n\ndef other(\n    new\n"
    )


def test_context_lines_survive_on_both_sides_of_a_change() -> None:
    file_patch = update(
        Hunk(
            (
                HunkLine("context", "before"),
                HunkLine("remove", "old"),
                HunkLine("add", "new"),
                HunkLine("context", "after"),
            )
        )
    )
    assert apply_to_text("before\nold\nafter\n", file_patch) == (
        "before\nnew\nafter\n"
    )


def test_an_insertion_with_an_anchor_lands_after_it() -> None:
    file_patch = update(
        Hunk((HunkLine("add", "inserted"),), anchor="def target(")
    )
    assert apply_to_text("def target(\nbody\n", file_patch) == (
        "def target(\ninserted\nbody\n"
    )


def test_an_insertion_without_an_anchor_appends() -> None:
    file_patch = update(Hunk((HunkLine("add", "appended"),)))
    assert apply_to_text("body\n", file_patch) == "body\nappended\n"


def test_apply_reports_a_conflict_when_context_is_absent() -> None:
    file_patch = update(Hunk((HunkLine("remove", "missing"),)))
    with pytest.raises(PatchError) as caught:
        apply_to_text("present\n", file_patch)
    assert caught.value.code == "patch_conflict"
    assert "missing" in caught.value.message


def test_apply_reports_a_missing_anchor() -> None:
    file_patch = update(
        Hunk((HunkLine("add", "inserted"),), anchor="nowhere(")
    )
    with pytest.raises(PatchError) as caught:
        apply_to_text("body\n", file_patch)
    assert caught.value.code == "patch_conflict"
    assert "nowhere(" in caught.value.message


def test_apply_preserves_a_missing_trailing_newline() -> None:
    file_patch = update(Hunk((HunkLine("remove", "old"), HunkLine("add", "new"))))
    assert apply_to_text("old", file_patch) == "new"


def test_apply_preserves_a_trailing_newline() -> None:
    file_patch = update(Hunk((HunkLine("remove", "old"), HunkLine("add", "new"))))
    assert apply_to_text("old\n", file_patch) == "new\n"


def test_apply_add_file_returns_its_lines() -> None:
    file_patch = FilePatch(
        kind="add", path="b.py", move_to=None, hunks=(), lines=("x", "y")
    )
    assert apply_to_text("", file_patch) == "x\ny\n"


def test_apply_delete_file_returns_empty_text() -> None:
    file_patch = FilePatch(kind="delete", path="c.py", move_to=None, hunks=(), lines=())
    assert apply_to_text("anything\n", file_patch) == ""


def test_plan_round_trips_through_the_markers() -> None:
    plan = parse_patch(
        "*** Begin Patch\n*** Add File: b.py\n+only\n*** End Patch\n"
    )
    assert isinstance(plan, PatchPlan)
    assert plan.files[0].lines == ("only",)
