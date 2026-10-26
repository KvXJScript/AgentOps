from __future__ import annotations

import pytest

from agentops.domain.ranking import (
    MAX_QUERY_IDENTIFIERS,
    RankingPolicy,
    contains_identifier,
    identifier_pattern,
    query_identifiers,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What does alert PAGER-7731 mean?", ("PAGER-7731",)),
        ("is abc-123 the same as ABC-123 or INC-2024-001?", ("ABC-123", "INC-2024-001")),
        ("error ERR_42 again", ("ERR_42",)),
        ("(PAGER-7731).", ("PAGER-7731",)),
        # Not identifiers: no separator, no digit, no leading letter, too short, glued to other token characters.
        ("the current runbook v3 for SEV1", ()),
        ("e-mail and follow-up in 2018", ()),
        ("call 555-1234", ()),
        ("A-1", ()),
        ("PAGER-7731-", ()),
        ("Ile dni urlopu dostaję z okazji ślubu?", ()),
    ],
)
def test_query_identifiers(query: str, expected: tuple[str, ...]) -> None:
    assert query_identifiers(query) == expected


def test_query_identifiers_are_capped() -> None:
    query = " ".join(f"ID-{n}" for n in range(10, 20))
    assert len(query_identifiers(query)) == MAX_QUERY_IDENTIFIERS


@pytest.mark.parametrize(
    ("text", "matches"),
    [
        ("Alert PAGER-7731 means replication lag.", True),
        ("alert pager-7731: lag", True),
        ("PAGER-7731", True),
        ("Alert PAGER-77310 is different.", False),
        ("XPAGER-7731 is different.", False),
        ("PAGER-7731-B is different.", False),
        ("PAGER 7731", False),
    ],
)
def test_contains_identifier_matches_whole_tokens_case_insensitively(text: str, matches: bool) -> None:
    assert contains_identifier(text, identifier_pattern(("PAGER-7731",))) is matches


def test_identifier_pattern_needs_identifiers() -> None:
    with pytest.raises(ValueError, match="at least one"):
        identifier_pattern(())


def test_rank_score_adjusts_order_only() -> None:
    policy = RankingPolicy(identifier_boost=0.1, deprecated_penalty=0.2)

    assert policy.rank_score(0.5, deprecated=False, identifier_match=False) == 0.5
    assert policy.rank_score(0.5, deprecated=False, identifier_match=True) == pytest.approx(0.6)
    assert policy.rank_score(0.5, deprecated=True, identifier_match=False) == pytest.approx(0.3)
    assert policy.rank_score(0.5, deprecated=True, identifier_match=True) == pytest.approx(0.4)
    assert RankingPolicy().rank_score(0.5, deprecated=True, identifier_match=True) == 0.5


def test_negative_adjustments_are_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        RankingPolicy(identifier_boost=-0.1)
