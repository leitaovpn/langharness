"""Ranking engine for the interactive slash-command palette.

The assertions here target ordering relationships rather than absolute
scores: the score is an implementation detail, the order is the contract.
"""

from __future__ import annotations

from langharness_cli.common.palette import Candidate, match, rank


def names(
    query: str, candidates: list[Candidate], *, limit: int | None = None
) -> list[str]:
    return [scored.candidate.name for scored in rank(query, candidates, limit=limit)]


def candidate(name: str) -> Candidate:
    return Candidate(name=name, description=f"help for {name}")


def test_empty_query_lists_every_candidate_in_name_order() -> None:
    candidates = [candidate("/scope"), candidate("/help"), candidate("/model")]

    assert names("", candidates) == ["/help", "/model", "/scope"]


def test_non_matching_candidate_is_dropped() -> None:
    candidates = [candidate("/help"), candidate("/scope")]

    assert names("/zz", candidates) == []


def test_match_is_case_insensitive() -> None:
    assert names("/MOD", [candidate("/model")]) == ["/model"]


def test_subsequence_matches_scattered_characters() -> None:
    candidates = [candidate("/scroll-speed"), candidate("/help")]

    assert names("/ss", candidates) == ["/scroll-speed"]


def test_consecutive_run_outranks_scattered_match() -> None:
    candidates = [candidate("/s-e-s-t"), candidate("/session")]

    assert names("/ses", candidates) == ["/session", "/s-e-s-t"]


def test_hits_report_matched_indices_for_highlighting() -> None:
    scored = rank("/ss", [candidate("/session")])

    assert scored[0].hits == (0, 1, 3)


def test_equal_scores_break_on_name_so_order_is_deterministic() -> None:
    candidates = [candidate("/ab-ef"), candidate("/ab-cd")]

    assert names("/ab", candidates) == ["/ab-cd", "/ab-ef"]


def test_match_of_an_empty_query_yields_no_hits() -> None:
    assert match("", "/help") == ()


def test_match_returns_none_when_the_query_is_not_a_subsequence() -> None:
    assert match("/zz", "/help") is None


def test_limit_truncates_after_ranking() -> None:
    candidates = [candidate("/one"), candidate("/two"), candidate("/three")]

    assert names("", candidates, limit=2) == ["/one", "/three"]
