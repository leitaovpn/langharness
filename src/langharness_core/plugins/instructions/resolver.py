"""Discovery, reading and budgeting of repository instruction files."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from langharness_core.common.text import digest, truncate_bytes

DEFAULT_BUDGET_BYTES = 65_536
DEFAULT_NAMES: tuple[str, ...] = ("AGENTS.md",)
# Bounds one read so a pathological instruction file cannot exhaust memory.
MAX_INSTRUCTION_BYTES = 262_144
# The root instruction may use at most this fraction of the budget, leaving
# room for the deeper files that describe the subdirectory being edited.
ROOT_BUDGET_SHARE = 2


@dataclass(frozen=True, slots=True)
class InstructionFile:
    """One instruction file selected for the prompt."""

    path: Path
    relative: str
    depth: int
    text: str
    sha256: str
    size: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class InstructionSelection:
    """The instruction files that fit, plus the ones that did not."""

    files: tuple[InstructionFile, ...]
    omitted: tuple[str, ...]
    budget: int


@dataclass(slots=True)
class _CacheEntry:
    mtime_ns: int
    size: int
    sha256: str
    text: str


class InstructionResolver:
    """Resolves the instruction files applying to a set of touched paths.

    Deeper files describe narrower subtrees, so a directory's instructions are
    only loaded once the conversation has touched something inside it.
    """

    def __init__(
        self,
        root: Path,
        *,
        names: Sequence[str] = DEFAULT_NAMES,
        budget: int = DEFAULT_BUDGET_BYTES,
    ) -> None:
        self._root = Path(root)
        self._names = tuple(names)
        self._budget = budget
        self._cache: dict[Path, _CacheEntry] = {}

    @property
    def root(self) -> Path:
        return self._root

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    @property
    def budget(self) -> int:
        return self._budget

    def directories(
        self, touched_paths: Iterable[str] | None
    ) -> list[tuple[Path, int]]:
        """Return (directory, depth) pairs for the root and touched directories."""
        found: dict[Path, int] = {self._root: 0}
        for raw in touched_paths or ():
            directory = self._directory_for(raw)
            if directory is None:
                continue
            for depth, item in self._chain(directory):
                found[item] = min(depth, found.get(item, depth))
        return sorted(found.items(), key=lambda item: (item[1], item[0].as_posix()))

    def resolve(self, touched_paths: Iterable[str] | None) -> InstructionSelection:
        """Select the instruction files for one prompt context."""
        candidates: list[InstructionFile] = []
        for directory, depth in self.directories(touched_paths):
            for name in self._names:
                found = self._read(directory / name, depth)
                if found is not None:
                    candidates.append(found)
        return self._apply_budget(candidates)

    def _chain(self, directory: Path) -> list[tuple[int, Path]]:
        """(depth, directory) from the workspace root down to ``directory``.

        ``directory`` always comes from :meth:`_directory_for`, which only
        returns paths inside the root, so the walk needs no containment check.
        """
        chain: list[tuple[int, Path]] = [(0, self._root)]
        parts = directory.relative_to(self._root).parts
        current = self._root
        for depth, part in enumerate(parts, start=1):
            current = current / part
            chain.append((depth, current))
        return chain

    def _directory_for(self, raw: str) -> Path | None:
        """Resolve one touched path to a directory inside the workspace."""
        if not raw:
            return None
        candidate = Path(raw)
        absolute = candidate if candidate.is_absolute() else self._root / candidate
        try:
            resolved = absolute.resolve()
            if resolved != self._root:
                resolved.relative_to(self._root)
        except (OSError, ValueError):
            return None
        if resolved.is_dir():
            return resolved
        return resolved.parent

    def _read(self, path: Path, depth: int) -> InstructionFile | None:
        """Read one instruction file, honouring the per-depth byte limit.

        One ``except OSError`` covers stat and read alike: an instruction file
        that cannot be inspected is simply not part of the prompt.
        """
        try:
            if not path.is_file():
                return None
            stat = path.stat()
            cached = self._cache.get(path)
            if (
                cached is not None
                and cached.mtime_ns == stat.st_mtime_ns
                and cached.size == stat.st_size
            ):
                text, sha256 = cached.text, cached.sha256
            else:
                limit = min(self._limit(depth), MAX_INSTRUCTION_BYTES)
                raw = path.open("rb").read(limit)
                text = raw.decode("utf-8", errors="replace")
                sha256 = digest(text)
                self._cache[path] = _CacheEntry(
                    stat.st_mtime_ns, stat.st_size, sha256, text
                )
        except OSError:
            return None
        truncated = stat.st_size > len(text.encode("utf-8"))
        if truncated:
            text, _ = truncate_bytes(text, self._limit(depth))
        relative = path.relative_to(self._root).as_posix()
        return InstructionFile(
            path=path,
            relative=relative,
            depth=depth,
            text=text,
            sha256=sha256,
            size=len(text.encode("utf-8")),
            truncated=truncated,
        )

    def _limit(self, depth: int) -> int:
        if depth == 0:
            return max(self._budget // ROOT_BUDGET_SHARE, 1)
        return self._budget

    def _apply_budget(self, candidates: list[InstructionFile]) -> InstructionSelection:
        """Keep the root instruction and then the nearest-to-target ones."""
        kept: list[InstructionFile] = []
        omitted: list[str] = []
        used = 0
        for item in sorted(candidates, key=lambda entry: (entry.depth, entry.relative)):
            if item.depth == 0:
                kept.append(item)
                used += item.size
        deeper = sorted(
            (item for item in candidates if item.depth > 0),
            key=lambda entry: (-entry.depth, entry.relative),
        )
        for item in deeper:
            if used + item.size > self._budget:
                omitted.append(item.relative)
                continue
            kept.append(item)
            used += item.size
        kept.sort(key=lambda entry: (entry.depth, entry.relative))
        return InstructionSelection(
            files=tuple(kept), omitted=tuple(omitted), budget=self._budget
        )
