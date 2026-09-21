"""Candidate ranking for the interactive slash-command palette.

Deliberately free of ``prompt_toolkit`` and ``rich`` imports: the scoring
rules are the only nontrivial logic in the palette, and keeping them pure
is what makes them testable without a terminal.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Characters that start a new "word" inside a candidate name. A hit right
# after one of these is a stronger signal than a hit mid-word.
BOUNDARY_CHARS = frozenset("/-_.")

# A hit that continues an unbroken run is worth far more than an isolated
# hit, which is what separates "/ses" -> "/session" from "/ses" -> "/s-e-s-t".
CONSECUTIVE_BONUS = 10
BOUNDARY_BONUS = 5
# Keep-name-the-same pressure: prefer short names and early hits.
START_PENALTY = 2


@dataclass(frozen=True)
class Candidate:
    """One selectable entry offered by the palette."""

    name: str
    description: str


@dataclass(frozen=True)
class Scored:
    """A candidate plus why it ranked where it did."""

    candidate: Candidate
    score: int
    hits: tuple[int, ...]


def match(query: str, target: str) -> tuple[int, ...] | None:
    """Greedily match ``query`` as a case-insensitive subsequence of ``target``.

    Returns the matched indices in increasing order, or ``None`` when the
    query does not match. An empty query matches at no indices.
    """
    if not query:
        return ()
    lowered = target.lower()
    wanted = query.lower()
    hits: list[int] = []
    index = 0
    for position, character in enumerate(lowered):
        if character == wanted[index]:
            hits.append(position)
            index += 1
            if index == len(wanted):
                return tuple(hits)
    return None


def _score(hits: Sequence[int], target: str) -> int:
    score = 0
    for order, position in enumerate(hits):
        if position == 0 or target[position - 1] in BOUNDARY_CHARS:
            score += BOUNDARY_BONUS
        if order and hits[order - 1] == position - 1:
            score += CONSECUTIVE_BONUS
    score -= hits[0] * START_PENALTY
    return score - len(target)


def rank(
    query: str,
    candidates: Iterable[Candidate],
    *,
    limit: int | None = None,
) -> list[Scored]:
    """Filter and order ``candidates`` against ``query``.

    An empty query lists everything in name order. Otherwise the order is by
    descending score, with ``len(name)`` then ``name`` as tie-breakers so the
    result never depends on the order candidates were supplied in.
    """
    if not query:
        ordered = sorted(candidates, key=lambda item: item.name)
        return [Scored(item, 0, ()) for item in ordered[:limit]]

    scored: list[Scored] = []
    for candidate in candidates:
        hits = match(query, candidate.name)
        if hits is None:
            continue
        scored.append(Scored(candidate, _score(hits, candidate.name), hits))

    scored.sort(key=lambda item: (-item.score, len(item.candidate.name), item.candidate.name))
    return scored[:limit]
