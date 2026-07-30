"""Searching and sorting the session list. Port of ``session-selector-search.ts``.

Split out of the selector for the reason the TS splits it: the query language is
the part with rules — ``re:`` for a regular expression, quotes for an exact
phrase, bare words fuzzy-matched — and it is worth testing without a component
around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from cortex.tui.fuzzy import fuzzy_match

__all__ = [
    "MatchResult",
    "NameFilter",
    "ParsedSearchQuery",
    "SortMode",
    "filter_and_sort_sessions",
    "has_session_name",
    "match_session",
    "parse_search_query",
]

#: How the list is ordered. ``threaded`` draws the parent/child tree, ``recent``
#: keeps the incoming (most-recent-first) order, ``relevance`` sorts by score.
SortMode = Literal["threaded", "recent", "relevance"]

#: Whether the list is restricted to sessions someone has named.
NameFilter = Literal["all", "named"]


@dataclass(frozen=True)
class SearchToken:
    """One term of a token-mode query."""

    kind: Literal["fuzzy", "phrase"]
    value: str


@dataclass
class ParsedSearchQuery:
    """A query as the matcher wants it."""

    mode: Literal["tokens", "regex"] = "tokens"
    tokens: list[SearchToken] = field(default_factory=list)
    regex: re.Pattern[str] | None = None
    #: Set when parsing failed; the query then matches nothing rather than
    #: everything, because a typo'd regex silently listing every session is
    #: worse than one listing none.
    error: str | None = None


@dataclass(frozen=True)
class MatchResult:
    """Whether a session matched, and how well. Lower scores are better."""

    matches: bool
    score: float = 0.0


def _normalize_whitespace_lower(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _session_search_text(session: Any) -> str:
    """Everything about a session a query is matched against."""
    return f"{session.id} {session.name or ''} {session.all_messages_text} {session.cwd}"


def has_session_name(session: Any) -> bool:
    """Whether someone gave this session a name."""
    return bool((session.name or "").strip())


def parse_search_query(query: str) -> ParsedSearchQuery:
    """Turn what was typed into a matcher.

    Three forms: ``re:<pattern>``, ``"an exact phrase"``, and bare words. An
    unbalanced quote falls back to plain whitespace tokens, so the query keeps
    working while the closing quote is still being typed.
    """
    trimmed = query.strip()
    if not trimmed:
        return ParsedSearchQuery()

    if trimmed.startswith("re:"):
        pattern = trimmed[3:].strip()
        if not pattern:
            return ParsedSearchQuery(mode="regex", error="Empty regex")
        try:
            return ParsedSearchQuery(mode="regex", regex=re.compile(pattern, re.IGNORECASE))
        except re.error as error:
            return ParsedSearchQuery(mode="regex", error=str(error))

    tokens: list[SearchToken] = []
    buffer = ""
    in_quote = False
    had_unclosed_quote = False

    def flush(kind: Literal["fuzzy", "phrase"]) -> None:
        nonlocal buffer
        value = buffer.strip()
        buffer = ""
        if value:
            tokens.append(SearchToken(kind=kind, value=value))

    for char in trimmed:
        if char == '"':
            if in_quote:
                flush("phrase")
                in_quote = False
            else:
                flush("fuzzy")
                in_quote = True
            continue

        if not in_quote and char.isspace():
            flush("fuzzy")
            continue

        buffer += char

    if in_quote:
        had_unclosed_quote = True

    if had_unclosed_quote:
        return ParsedSearchQuery(
            tokens=[SearchToken(kind="fuzzy", value=part) for part in trimmed.split() if part]
        )

    flush("phrase" if in_quote else "fuzzy")
    return ParsedSearchQuery(tokens=tokens)


def match_session(session: Any, parsed: ParsedSearchQuery) -> MatchResult:
    """Match one session, scoring it by how early the query landed."""
    text = _session_search_text(session)

    if parsed.mode == "regex":
        if parsed.regex is None:
            return MatchResult(matches=False)
        found = parsed.regex.search(text)
        if found is None:
            return MatchResult(matches=False)
        return MatchResult(matches=True, score=found.start() * 0.1)

    if not parsed.tokens:
        return MatchResult(matches=True)

    total_score = 0.0
    normalized_text: str | None = None

    for token in parsed.tokens:
        if token.kind == "phrase":
            if normalized_text is None:
                normalized_text = _normalize_whitespace_lower(text)
            phrase = _normalize_whitespace_lower(token.value)
            if not phrase:
                continue
            index = normalized_text.find(phrase)
            if index < 0:
                return MatchResult(matches=False)
            total_score += index * 0.1
            continue

        match = fuzzy_match(token.value, text)
        if not match.matches:
            return MatchResult(matches=False)
        total_score += match.score

    return MatchResult(matches=True, score=total_score)


def _modified_timestamp(session: Any) -> float:
    return session.modified.timestamp() if session.modified is not None else 0.0


def filter_and_sort_sessions(
    sessions: list[Any],
    query: str,
    sort_mode: SortMode,
    name_filter: NameFilter = "all",
) -> list[Any]:
    """Apply the name filter and the query, in the order the sort mode wants.

    ``recent`` filters without reordering — the list arrives most-recent-first
    and typing must not shuffle it — while ``relevance`` sorts by score with the
    modified date breaking ties.
    """
    name_filtered = (
        sessions if name_filter == "all" else [s for s in sessions if has_session_name(s)]
    )
    if not query.strip():
        return name_filtered

    parsed = parse_search_query(query)
    if parsed.error:
        return []

    if sort_mode == "recent":
        return [s for s in name_filtered if match_session(s, parsed).matches]

    scored: list[tuple[float, Any]] = []
    for session in name_filtered:
        result = match_session(session, parsed)
        if result.matches:
            scored.append((result.score, session))

    scored.sort(key=lambda pair: (pair[0], -_modified_timestamp(pair[1])))
    return [session for _, session in scored]
