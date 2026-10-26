"""Ranking adjustments on top of vector similarity (ADR 0007).

Two narrow, measured signals reorder candidates without changing their `score`, which stays the cosine
similarity, so `RETRIEVAL_MIN_SCORE` still means the same thing:

- **Identifier boost.** When the query contains identifier-like tokens (`PAGER-7731`, `ABC-123`,
  `INC-2024-001`, `ERR_42`), a chunk whose text contains one of them as a whole token ranks as if its
  similarity were `identifier_boost` higher. Queries without such tokens are ranked by similarity alone.
- **Deprecated penalty.** A chunk of a document marked `status: deprecated` (Markdown front matter) ranks as if
  its similarity were `deprecated_penalty` lower, so a current near-duplicate wins unless the deprecated text
  is clearly closer.

`PgVectorRetriever` and the eval index both rank with `RankingPolicy.rank_score`, so the eval measures exactly
what production does. A policy of zeros is plain vector ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_QUERY_IDENTIFIERS = 5

# ASCII letters/digits joined by `-` or `_`, starting with a letter, not touching another such character.
# A digit is required as well (checked below), so plain hyphenated words ("e-mail") don't count, and tokens
# without a separator ("v3", "SEV1", "2018") don't either: they are too common to be exact evidence.
_WORD_CHARS = "A-Za-z0-9_-"
_IDENTIFIER = re.compile(rf"(?<![{_WORD_CHARS}])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+(?![{_WORD_CHARS}])")
_MIN_IDENTIFIER_CHARS = 4


def query_identifiers(query: str) -> tuple[str, ...]:
    """Identifier-like tokens in the query, upper-cased, de-duplicated, at most `MAX_QUERY_IDENTIFIERS`."""
    found: list[str] = []
    for match in _IDENTIFIER.finditer(query):
        token = match.group(0).upper()
        if len(token) >= _MIN_IDENTIFIER_CHARS and any(c.isdigit() for c in token) and token not in found:
            found.append(token)
    return tuple(found[:MAX_QUERY_IDENTIFIERS])


def identifier_pattern(identifiers: tuple[str, ...]) -> str:
    """A regex matching any of `identifiers` as a whole token, valid both for Python `re` and PostgreSQL `~*`.

    Identifiers come from `query_identifiers`, so they contain only `[A-Za-z0-9_-]` and need no escaping.
    Use case-insensitively.
    """
    if not identifiers:
        raise ValueError("identifier_pattern needs at least one identifier")
    return f"(^|[^{_WORD_CHARS}])({'|'.join(identifiers)})($|[^{_WORD_CHARS}])"


def contains_identifier(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE | re.ASCII) is not None


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    identifier_boost: float = 0.0
    deprecated_penalty: float = 0.0

    def __post_init__(self) -> None:
        if self.identifier_boost < 0 or self.deprecated_penalty < 0:
            raise ValueError("ranking adjustments must be >= 0")

    def rank_score(self, score: float, *, deprecated: bool, identifier_match: bool) -> float:
        """The value candidates are ordered by (descending, ties by chunk id). Never reported as `score`."""
        boost = self.identifier_boost if identifier_match else 0.0
        penalty = self.deprecated_penalty if deprecated else 0.0
        return score + boost - penalty
