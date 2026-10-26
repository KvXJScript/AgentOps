"""Deterministic grounding check for a model-drafted issue body (advisory; see ADR 0007).

`check_proposal_grounding(body, evidence)` is pure. It does not rewrite what the model said: it only
removes `[Sn]` markers that don't resolve to evidence the model was shown, and reports, claim by claim,
what it could not tie to that evidence. The human reviewer decides.

Claim rule, applied line by line:

- Not claims: blank lines, Markdown headings, horizontal rules, fenced code (fences and the lines in
  between), checkbox items (`- [ ] ...`), every line of an *action section* (a heading starting with
  Next step(s), Action(s)/Action items, To do, Tasks, Proposed actions, Questions/Open questions,
  Sources, References or Related issues; it lasts until the next heading), and lines or sentences that
  start with an action prefix (`Next:`, `Next step:`, `Action:`, `TODO:`, `Owner:`, `Question:`,
  `Related:`, `See also:`, `Sources:`, `References:`; bold around the prefix is allowed).
- List bullets, numbering and `>` quote marks are removed, then the line is split into sentences after
  `.`, `!` or `?` followed by whitespace and an upper-case letter, a digit or a marker (not after common
  abbreviations such as "e.g."). Markers that open a sentence belong to the previous one ("... week. [S1]").
- A sentence ending in `?` or with fewer than 3 words is not a claim. Everything else is a claim.

Per claim, in this order (one issue per claim at most):

1. `unsupported`: no marker resolves to an included evidence label.
2. `unverified_number`: a quantity in the claim (any digit number, or a number word followed by a unit,
   e.g. "five business days") has no equal value in the excerpts the claim cites. Number words and digits
   are normalised to one value ("one hour" == "1 hour", "1,200" == "1200"); when both sides carry a unit
   (minute, hour, day, week, month, year, second, percent) the units must match too. Identifiers such as
   SEV2, LT-3 or PAGER-7731 are not quantities.
3. `low_overlap`: the claim has at least `OVERLAP_MIN_TERMS` content words and fewer than
   `OVERLAP_MIN_SHARE` of them occur in the cited excerpts (crude stemming: first 5 letters).

Known blind spots: a claim with a real marker whose excerpt shares its words but says something else
passes; a paraphrase with different words or units ("60 minutes" for "1 hour") is flagged; facts written
as imperatives or inside an action section are not checked.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from agentops.application.grounding import normalize_markers
from agentops.domain.proposals import GroundingIssue, GroundingReason, ProposalGrounding
from agentops.domain.retrieval import Citation

CLAIM_MAX_CHARS = 300
MIN_CLAIM_WORDS = 3
OVERLAP_MIN_TERMS = 3
OVERLAP_MIN_SHARE = 0.5

_MARKER = re.compile(r"\[(S\d{1,3})\]")
_MARKER_WITH_SPACE = re.compile(r"[ \t]*\[(S\d{1,3})\]")
_MARKER_GROUP = re.compile(r"\[\s*(S\d{1,3}(?:\s*[,;]\s*S\d{1,3})+)\s*\]")
_LEADING_MARKERS = re.compile(r"^(?:\s*\[S\d{1,3}\])+")

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")
_BULLET = re.compile(r"^\s*(?:>\s*)*(?:(?:[-*+]|\d{1,3}[.)])\s+)?")
_CHECKBOX = re.compile(r"^\[[ xX]\]\s+")
_ACTION_HEADING = re.compile(
    r"^[*_]*(?:next\s+steps?|action\s+items?|actions?|to\s*-?\s*dos?|tasks?|proposed\s+(?:actions?|next\s+steps?)"
    r"|open\s+questions?|questions?|sources|references|related(?:\s+issues?)?)\b",
    re.IGNORECASE,
)
_ACTION_PREFIX = re.compile(
    r"^[*_]*(?:next(?:\s+steps?)?|actions?(?:\s+items?)?|to\s*-?\s*do|owner|questions?|related(?:\s+issues?)?"
    r"|see\s+also|sources?|references?)[*_\s]*:",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'*_])")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "approx.", "incl.", "no.", "cf.", "dr.", "mr.", "ms.")
_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")

# --- quantities -----------------------------------------------------------------------------------
_UNIT_FORMS: dict[str, tuple[str, ...]] = {
    "second": ("seconds", "second", "secs", "sec"),
    "minute": ("minutes", "minute", "mins", "min"),
    "hour": ("hours", "hour", "hrs", "hr", "h"),
    "day": ("days", "day", "d"),
    "week": ("weeks", "week", "wks", "wk"),
    "month": ("months", "month"),
    "year": ("years", "year", "yrs", "yr"),
    "percent": ("percent", "per cent", "%"),
}
_UNIT_OF = {form: unit for unit, forms in _UNIT_FORMS.items() for form in forms}
_UNIT_ALT = "|".join(re.escape(f) for f in sorted(_UNIT_OF, key=len, reverse=True))
_UNIT_TAIL = rf"(?:[\s-]*(?:(?:business|working|calendar)[\s-]+)?(?P<unit>{_UNIT_ALT})(?![^\W\d_]))?"

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_DIGIT_QTY = re.compile(
    rf"(?<![\w.,])(?P<num>\d+(?:[.,]\d+)*(?::\d{{2}})?)(?P<suffix>st|nd|rd|th|am|pm)?{_UNIT_TAIL}",
    re.IGNORECASE,
)
_WORD_QTY = re.compile(
    rf"\b(?P<word>(?:{'|'.join(_TENS)})(?:[\s-](?:{'|'.join(k for k, v in _ONES.items() if 0 < v < 10)}))?"
    rf"|{'|'.join(sorted(_ONES, key=len, reverse=True))})\b{_UNIT_TAIL}",
    re.IGNORECASE,
)

# Common words that say nothing about the evidence (only words of >= 4 letters matter).
_STOPWORDS = frozenset({
    "about", "above", "after", "again", "against", "also", "although", "among", "another", "because", "been",
    "before", "being", "below", "between", "both", "cannot", "could", "does", "doing", "done", "down", "during",
    "each", "either", "even", "every", "from", "further", "have", "having", "here", "into", "just", "less", "many",
    "more", "most", "much", "must", "need", "needs", "only", "other", "otherwise", "over", "same", "should",
    "since", "some", "such", "than", "that", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "under", "until", "upon", "very", "were", "what", "when", "where", "whether", "which", "while",
    "will", "with", "within", "without", "would", "your", "yours", "report", "reports", "found", "states", "stated",
    "says", "according", "document", "documents", "evidence", "source", "sources", "issue", "issues", "team",
    "currently",
})  # fmt: skip


@dataclass(frozen=True, slots=True)
class Quantity:
    value: str
    unit: str | None
    text: str


@dataclass(frozen=True, slots=True)
class GroundingResult:
    body: str  # the body with group markers expanded and invalid markers removed
    grounding: ProposalGrounding
    cited: tuple[str, ...]  # valid labels cited anywhere in the body, first mention first
    invalid_markers: int


def check_proposal_grounding(body: str, evidence: Sequence[Citation]) -> GroundingResult:
    by_label = {item.marker: item for item in evidence}
    cleaned, invalid = _strip_invalid(_expand_groups(normalize_markers(body)), set(by_label))
    issues: list[GroundingIssue] = []
    claims = split_claims(cleaned)
    for claim in claims:
        issue = _check_claim(claim, by_label)
        if issue is not None:
            issues.append(issue)
    cited = tuple(dict.fromkeys(label for label in _MARKER.findall(cleaned) if label in by_label))
    grounding = ProposalGrounding(checked_claims=len(claims), supported=len(claims) - len(issues), issues=tuple(issues))
    return GroundingResult(body=cleaned, grounding=grounding, cited=cited, invalid_markers=invalid)


def split_claims(body: str) -> list[str]:
    """The claims of a body, each with its markers still attached (see the module docstring)."""
    claims: list[str] = []
    in_fence = False
    in_action_section = False
    for raw in body.splitlines():
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or not raw.strip() or _RULE.match(raw):
            continue
        heading = _HEADING.match(raw)
        if heading:
            in_action_section = bool(_ACTION_HEADING.match(heading.group(1)))
            continue
        if in_action_section:
            continue
        line = _BULLET.sub("", raw, count=1).strip()
        if _CHECKBOX.match(line) or _ACTION_PREFIX.match(line):
            continue
        claims.extend(s for s in _sentences(line) if _is_claim(s))
    return claims


def _sentences(line: str) -> list[str]:
    out: list[str] = []
    for raw in _SENTENCE_END.split(line):
        piece = raw
        leading = _LEADING_MARKERS.match(piece)
        if leading and out:
            out[-1] = f"{out[-1]} {leading.group(0).strip()}"
            piece = piece[leading.end() :].strip()
        if not piece:
            continue
        if out and out[-1].lower().endswith(_ABBREVIATIONS):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _is_claim(sentence: str) -> bool:
    text = _without_markers(sentence)
    if _ACTION_PREFIX.match(text) or text.rstrip("*_ ").endswith("?"):
        return False
    return len(_WORD.findall(text)) >= MIN_CLAIM_WORDS


def _check_claim(claim: str, by_label: dict[str, Citation]) -> GroundingIssue | None:
    markers = tuple(dict.fromkeys(label for label in _MARKER.findall(claim) if label in by_label))
    text = _display(claim)
    if not markers:
        return GroundingIssue(claim=text, reason=GroundingReason.UNSUPPORTED, markers=())
    excerpts = " ".join(by_label[m].excerpt for m in markers)
    plain = _without_markers(claim)
    missing = [q.text for q in quantities(plain, require_unit_for_words=True) if not _found(q, excerpts)]
    if missing:
        return GroundingIssue(
            claim=text,
            reason=GroundingReason.UNVERIFIED_NUMBER,
            markers=markers,
            numbers=tuple(dict.fromkeys(missing)),
        )
    terms = content_terms(plain)
    if len(terms) >= OVERLAP_MIN_TERMS:
        available = {_stem(w) for w in _WORD.findall(excerpts)}
        if len(terms & available) / len(terms) < OVERLAP_MIN_SHARE:
            return GroundingIssue(claim=text, reason=GroundingReason.LOW_OVERLAP, markers=markers)
    return None


def quantities(text: str, *, require_unit_for_words: bool = False) -> list[Quantity]:
    """Numbers in `text`, normalised. Number words count only with a unit when `require_unit_for_words`."""
    found: list[Quantity] = []
    for m in _DIGIT_QTY.finditer(text):
        start, end = m.span()
        if start >= 2 and text[start - 1] == "-" and text[start - 2].isalpha():
            continue  # LT-3, PAGER-7731: an identifier, not a quantity
        if end < len(text) and text[end].isalpha() and m.group("unit") is None:
            continue  # 2FA, 4xx
        value = _digit_value(m.group("num"))
        if value is not None:
            found.append(Quantity(value, _unit(m.group("unit")), m.group(0).strip()))
    for m in _WORD_QTY.finditer(text):
        if require_unit_for_words and m.group("unit") is None:
            continue  # "one of the teams" is not a quantity
        found.append(Quantity(str(_word_value(m.group("word"))), _unit(m.group("unit")), m.group(0).strip()))
    return found


def content_terms(text: str) -> set[str]:
    words = (w.lower() for w in _WORD.findall(text))
    return {_stem(w) for w in words if len(w) >= 4 and not w.isdigit() and w not in _STOPWORDS}


def _found(q: Quantity, excerpts: str) -> bool:
    return any(
        e.value == q.value and (q.unit is None or e.unit is None or e.unit == q.unit) for e in quantities(excerpts)
    )


def _digit_value(raw: str) -> str | None:
    if ":" in raw:
        hours, minutes = raw.split(":", 1)
        return f"{int(hours)}:{minutes}"
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        raw = raw.replace(",", "")
    elif raw.count(",") == 1 and "." not in raw:
        raw = raw.replace(",", ".")  # decimal comma
    try:
        return format(Decimal(raw).normalize(), "f")
    except InvalidOperation:
        return None


def _word_value(word: str) -> int:
    parts = re.split(r"[\s-]", word.lower())
    return sum(_TENS.get(p, _ONES.get(p, 0)) for p in parts)


def _unit(form: str | None) -> str | None:
    return _UNIT_OF.get(re.sub(r"\s+", " ", form.lower())) if form else None


def _stem(word: str) -> str:
    word = word.lower().removesuffix("'s")
    return word[:5] if len(word) > 5 else word.rstrip("s")


def _expand_groups(text: str) -> str:
    """`[S1, S2]` → `[S1][S2]`, so every marker has the one form that is resolved and stripped."""
    return _MARKER_GROUP.sub(lambda m: "".join(f"[{x}]" for x in re.split(r"\s*[,;]\s*", m.group(1))), text)


def _strip_invalid(text: str, valid: set[str]) -> tuple[str, int]:
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        if match.group(1) in valid:
            return match.group(0)
        removed += 1
        return ""

    return _MARKER_WITH_SPACE.sub(replace, text), removed


def _without_markers(text: str) -> str:
    return _MARKER_WITH_SPACE.sub("", text).strip()


def _display(claim: str) -> str:
    text = " ".join(_without_markers(claim).split())
    return text if len(text) <= CLAIM_MAX_CHARS else text[: CLAIM_MAX_CHARS - 1].rstrip() + "…"
