"""Workspace text search.

The pure-Python walk is the main path, not a fallback: a machine without a
real ``rg`` binary is the normal case, so both engines return the same shape
and the same ordering.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".codegraph",
    }
)
DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_FILE_BYTES = 2_097_152
BINARY_SNIFF_BYTES = 8_192


class SearchPatternError(ValueError):
    """Raised when the query is not a usable regular expression."""


@dataclass(frozen=True, slots=True)
class SearchMatch:
    path: str
    line: int
    column: int
    text: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    matches: list[SearchMatch]
    files_scanned: int
    truncated: bool
    engine: str


def find_ripgrep() -> str | None:
    """Return the ripgrep binary path, or None when it is not installed."""
    return shutil.which("rg")


def _candidates(root: Path, glob: str | None) -> list[Path]:
    """Files to search, skipping ignored directories.

    The ignore check runs on workspace-relative parts: an absolute path whose
    own ancestors happen to be called ``build`` or ``dist`` must not silently
    exclude the whole workspace.
    """
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in DEFAULT_IGNORED_DIRECTORIES for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        if glob and not fnmatch.fnmatch(path.name, glob):
            continue
        found.append(path)
    return found


def _matches_in(
    root: Path, path: Path, pattern: re.Pattern[str], max_file_bytes: int
) -> list[SearchMatch]:
    try:
        if path.stat().st_size > max_file_bytes:
            return []
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        return []
    relative = path.relative_to(root).as_posix()
    text = raw.decode("utf-8", errors="replace")
    found: list[SearchMatch] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for hit in pattern.finditer(line):
            found.append(
                SearchMatch(
                    path=relative,
                    line=number,
                    column=hit.start() + 1,
                    text=line,
                )
            )
    return found


def _search_with_ripgrep(
    root: Path,
    query: str,
    glob: str | None,
    binary: str,
    max_file_bytes: int,
) -> tuple[list[SearchMatch], int]:
    command = [
        binary,
        "--line-number",
        "--column",
        "--no-heading",
        "--color=never",
        "--max-filesize",
        str(max_file_bytes),
    ]
    if glob:
        command += ["--glob", glob]
    for ignored in sorted(DEFAULT_IGNORED_DIRECTORIES):
        command += ["--glob", f"!**/{ignored}/**"]
    command += [query, "."]
    completed = subprocess.run(
        command, cwd=root, capture_output=True, text=True, check=False
    )
    matches: list[SearchMatch] = []
    files: set[str] = set()
    for raw_line in completed.stdout.splitlines():
        parts = raw_line.split(":", 3)
        if len(parts) != 4:
            continue
        raw_path, raw_number, raw_column, text = parts
        # rg prefixes paths with "./" when the search root is "."; Path drops
        # it, so the workspace-relative spelling falls out of as_posix().
        relative = Path(raw_path).as_posix()
        files.add(relative)
        try:
            line_number = int(raw_number)
            column = int(raw_column)
        except ValueError:
            continue
        matches.append(
            SearchMatch(path=relative, line=line_number, column=column, text=text)
        )
    return matches, len(files)


def search_workspace(
    root: Path,
    query: str,
    *,
    glob: str | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ripgrep: str | None = None,
) -> SearchOutcome:
    """Search ``root`` for ``query``, returning a capped, ordered result set."""
    try:
        pattern = re.compile(query)
    except re.error as exc:
        raise SearchPatternError(
            f"invalid regular expression {query!r}: {exc}"
        ) from exc
    binary = ripgrep or find_ripgrep()
    if binary:
        try:
            found, scanned = _search_with_ripgrep(
                root, query, glob, binary, max_file_bytes
            )
        except OSError:
            # A binary that cannot be executed is not an error the model can
            # act on; the pure-Python path answers the same question.
            pass
        else:
            found.sort(key=lambda item: (item.path, item.line, item.column))
            return SearchOutcome(
                matches=found[:max_results],
                files_scanned=scanned,
                truncated=len(found) > max_results,
                engine="ripgrep",
            )
    matches: list[SearchMatch] = []
    scanned = 0
    truncated = False
    for path in _candidates(root, glob):
        scanned += 1
        for match in _matches_in(root, path, pattern, max_file_bytes):
            if len(matches) >= max_results:
                truncated = True
                break
            matches.append(match)
        if truncated:
            break
    matches.sort(key=lambda item: (item.path, item.line, item.column))
    return SearchOutcome(
        matches=matches, files_scanned=scanned, truncated=truncated, engine="python"
    )
