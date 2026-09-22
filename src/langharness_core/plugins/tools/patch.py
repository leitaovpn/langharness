
"""Parsing and atomic application of the code agent's patch format.

The format is frozen in docs/plans/2026-09-22-code-agent-workspace-tools-plan.md
and is already understood by ``touched_paths_from_messages`` in
``plugins/prompts/context.py``.  Parsing never touches the filesystem and
application is a pure text function, so a caller can compute every file's new
content before writing any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BEGIN_MARKER = "*** Begin Patch"
END_MARKER = "*** End Patch"
UPDATE_MARKER = "*** Update File: "
ADD_MARKER = "*** Add File: "
DELETE_MARKER = "*** Delete File: "
MOVE_MARKER = "*** Move to: "
ANCHOR_MARKER = "@@"
CONTEXT_PREFIX = " "
REMOVE_PREFIX = "-"
ADD_PREFIX = "+"

PatchErrorCode = Literal["patch_invalid", "patch_conflict"]


class PatchError(Exception):
    """A patch that cannot be parsed, or cannot be applied to this content."""

    def __init__(self, code: PatchErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HunkLine:
    kind: Literal["context", "remove", "add"]
    text: str


@dataclass(frozen=True, slots=True)
class Hunk:
    """One contiguous edit. ``anchor`` is the text after the ``@@`` marker."""

    lines: tuple[HunkLine, ...]
    anchor: str | None = None


@dataclass(frozen=True, slots=True)
class FilePatch:
    kind: Literal["update", "add", "delete"]
    path: str
    move_to: str | None
    hunks: tuple[Hunk, ...]
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatchPlan:
    files: tuple[FilePatch, ...]


def parse_patch(text: str) -> PatchPlan:
    """Parse a patch document into a plan, rejecting anything malformed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != BEGIN_MARKER:
        raise PatchError("patch_invalid", f"patch must start with {BEGIN_MARKER!r}")
    if lines[-1].strip() != END_MARKER:
        raise PatchError("patch_invalid", f"patch must end with {END_MARKER!r}")
    body = lines[1:-1]
    files: list[FilePatch] = []
    index = 0
    while index < len(body):
        line = body[index]
        if line.startswith(UPDATE_MARKER):
            path = line[len(UPDATE_MARKER):].strip()
            index += 1
            move_to = None
            if index < len(body) and body[index].startswith(MOVE_MARKER):
                move_to = body[index][len(MOVE_MARKER):].strip()
                index += 1
            hunks: list[Hunk] = []
            current: list[HunkLine] = []
            anchor: str | None = None
            while index < len(body) and not _is_marker(body[index]):
                raw = body[index]
                if raw.startswith(ANCHOR_MARKER):
                    if current:
                        hunks.append(Hunk(tuple(current), anchor))
                        current = []
                    anchor = raw[len(ANCHOR_MARKER):].strip() or None
                    index += 1
                    continue
                if raw.startswith(ADD_PREFIX):
                    current.append(HunkLine("add", raw[1:]))
                elif raw.startswith(REMOVE_PREFIX):
                    current.append(HunkLine("remove", raw[1:]))
                elif raw.startswith(CONTEXT_PREFIX):
                    current.append(HunkLine("context", raw[1:]))
                elif raw == "":
                    current.append(HunkLine("context", ""))
                else:
                    raise PatchError(
                        "patch_invalid", f"unrecognised patch line: {raw!r}"
                    )
                index += 1
            if current:
                hunks.append(Hunk(tuple(current), anchor))
            if not hunks:
                raise PatchError("patch_invalid", f"{path!r} has no hunks")
            files.append(
                FilePatch(
                    kind="update",
                    path=path,
                    move_to=move_to,
                    hunks=tuple(hunks),
                    lines=(),
                )
            )
            continue
        if line.startswith(ADD_MARKER):
            path = line[len(ADD_MARKER):].strip()
            index += 1
            added: list[str] = []
            while index < len(body) and not _is_marker(body[index]):
                raw = body[index]
                if not raw.startswith(ADD_PREFIX):
                    raise PatchError(
                        "patch_invalid",
                        f"added file {path!r} may only contain '+' lines: {raw!r}",
                    )
                added.append(raw[1:])
                index += 1
            files.append(
                FilePatch(
                    kind="add",
                    path=path,
                    move_to=None,
                    hunks=(),
                    lines=tuple(added),
                )
            )
            continue
        if line.startswith(DELETE_MARKER):
            files.append(
                FilePatch(
                    kind="delete",
                    path=line[len(DELETE_MARKER):].strip(),
                    move_to=None,
                    hunks=(),
                    lines=(),
                )
            )
            index += 1
            continue
        raise PatchError("patch_invalid", f"unrecognised patch line: {line!r}")
    if not files:
        raise PatchError("patch_invalid", "patch contains no file sections")
    return PatchPlan(files=tuple(files))


def _is_marker(line: str) -> bool:
    return line.startswith(
        (UPDATE_MARKER, ADD_MARKER, DELETE_MARKER, MOVE_MARKER, END_MARKER)
    )


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _apply_hunk(lines: list[str], hunk: Hunk, index: int) -> list[str]:
    old = [item.text for item in hunk.lines if item.kind in {"context", "remove"}]
    new = [item.text for item in hunk.lines if item.kind in {"context", "add"}]
    start = 0
    if hunk.anchor:
        start = next(
            (offset for offset, line in enumerate(lines) if hunk.anchor in line),
            -1,
        )
        if start < 0:
            raise PatchError(
                "patch_conflict",
                f"hunk {index} anchor {hunk.anchor!r} not found",
            )
        start += 1
    if not old:
        # Nothing to match on: land right after the anchor, or at the end.
        return lines[:start] + new + lines[start:] if hunk.anchor else lines + new
    for offset in range(start, len(lines) - len(old) + 1):
        if lines[offset : offset + len(old)] == old:
            return lines[:offset] + new + lines[offset + len(old) :]
    raise PatchError(
        "patch_conflict",
        f"hunk {index} context not found; expected {old!r}",
    )


def apply_to_text(original: str, file_patch: FilePatch) -> str:
    """Return the new content for one file, or raise before changing anything."""
    if file_patch.kind == "delete":
        return ""
    if file_patch.kind == "add":
        return "".join(f"{line}\n" for line in file_patch.lines)
    newline = _newline_of(original)
    had_trailing = original.endswith(newline) or original == ""
    lines = original.split(newline)
    if had_trailing and lines and lines[-1] == "":
        lines.pop()
    for index, hunk in enumerate(file_patch.hunks):
        lines = _apply_hunk(lines, hunk, index)
    if not lines:
        return ""
    return newline.join(lines) + (newline if had_trailing else "")
