"""Extract the workspace paths a conversation has already touched."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from langharness_core.contracts import PromptContext

PATH_ARG_KEYS: tuple[str, ...] = (
    "path",
    "file_path",
    "dir_path",
    "cwd",
    "notebook_path",
)
PATCH_ARG_KEY = "patch"
PATCH_PATH_MARKERS: tuple[str, ...] = (
    "*** Update File:",
    "*** Add File:",
    "*** Delete File:",
    "*** Move to:",
)
MODEL_NAME_ATTRIBUTES: tuple[str, ...] = ("model_name", "model", "model_id")


def _record(seen: dict[str, None], root: Path, value: str) -> None:
    """Keep workspace-relative path ``value``; silently drop everything else."""
    if not value or "\n" in value:
        return
    candidate = Path(value)
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = absolute.resolve()
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        # Either the path cannot be resolved or it leaves the workspace; a
        # traversal or a symlink escape must never widen the instruction scope.
        return
    if not relative.parts:
        return
    seen.setdefault(relative.as_posix(), None)


def touched_paths_from_messages(
    messages: Iterable[Any] | None, workspace: str
) -> tuple[str, ...]:
    """Return workspace-relative paths named by the conversation's tool calls.

    The result keeps first-seen order and never raises: prompt assembly runs on
    the request path, so a malformed message must degrade to "no touched paths"
    rather than fail the model call.
    """
    root = Path(workspace).resolve()
    seen: dict[str, None] = {}
    for message in messages or ():
        for tool_call in getattr(message, "tool_calls", None) or ():
            if not isinstance(tool_call, dict):
                continue
            args = tool_call.get("args")
            if not isinstance(args, dict):
                continue
            for key in PATH_ARG_KEYS:
                value = args.get(key)
                if isinstance(value, str):
                    _record(seen, root, value)
            patch = args.get(PATCH_ARG_KEY)
            if isinstance(patch, str):
                for line in patch.splitlines():
                    for marker in PATCH_PATH_MARKERS:
                        if line.startswith(marker):
                            _record(seen, root, line[len(marker) :].strip())
    return tuple(seen)


def model_display_name(model: Any) -> str | None:
    """Best-effort model identifier for the identity prompt section."""
    for attribute in MODEL_NAME_ATTRIBUTES:
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


def build_prompt_context(
    *,
    workspace: str,
    messages: Iterable[Any] | None,
    model: str | None,
) -> PromptContext:
    """Assemble the context handed to every prompt-section provider."""
    return PromptContext(
        workspace=workspace,
        touched_paths=touched_paths_from_messages(messages, workspace),
        model=model,
    )
