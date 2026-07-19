"""Fuzzy matching utilities.

Mechanical port of hoocode's `packages/tui/src/fuzzy.ts`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar


@dataclass(frozen=True)
class FuzzyMatch:
    matches: bool
    score: float


T = TypeVar("T")


def _match_query(normalized_query: str, text_lower: str) -> FuzzyMatch:
    if not normalized_query:
        return FuzzyMatch(matches=True, score=0)

    if len(normalized_query) > len(text_lower):
        return FuzzyMatch(matches=False, score=0)

    query_index = 0
    score = 0.0
    last_match_index = -1
    consecutive_matches = 0

    for i in range(len(text_lower)):
        if query_index >= len(normalized_query):
            break
        if text_lower[i] == normalized_query[query_index]:
            is_word_boundary = i == 0 or text_lower[i - 1] in " \\-_.:/:"

            if last_match_index == i - 1:
                consecutive_matches += 1
                score -= consecutive_matches * 5
            else:
                consecutive_matches = 0
                if last_match_index >= 0:
                    score += (i - last_match_index - 1) * 2

            if is_word_boundary:
                score -= 10

            score += i * 0.1

            last_match_index = i
            query_index += 1

    if query_index < len(normalized_query):
        return FuzzyMatch(matches=False, score=0)

    if normalized_query == text_lower:
        score -= 100

    return FuzzyMatch(matches=True, score=score)


def fuzzy_match(query: str, text: str) -> FuzzyMatch:
    """Return whether query matches text and the match quality score."""
    query_lower = query.lower()
    text_lower = text.lower()

    primary = _match_query(query_lower, text_lower)
    if primary.matches:
        return primary

    # Support letter+digit or digit+letter swaps (e.g. "abc123" <=> "123abc")
    import re

    alpha_numeric = re.match(r"^(?P<letters>[a-z]+)(?P<digits>[0-9]+)$", query_lower)
    numeric_alpha = re.match(r"^(?P<digits>[0-9]+)(?P<letters>[a-z]+)$", query_lower)
    if alpha_numeric:
        swapped = alpha_numeric.group("digits") + alpha_numeric.group("letters")
    elif numeric_alpha:
        swapped = numeric_alpha.group("letters") + numeric_alpha.group("digits")
    else:
        return primary

    swapped_match = _match_query(swapped, text_lower)
    if not swapped_match.matches:
        return primary
    return FuzzyMatch(matches=True, score=swapped_match.score + 5)


def fuzzy_filter(items: list[T], query: str, get_text: Callable[[T], str]) -> list[T]:
    """Filter and sort items by fuzzy match quality (best matches first)."""
    trimmed = query.strip()
    if not trimmed:
        return items

    tokens = [t for t in trimmed.split() if t]
    if not tokens:
        return items

    results: list[tuple[T, float]] = []
    for item in items:
        text = get_text(item)
        total_score = 0.0
        all_match = True
        for token in tokens:
            match = fuzzy_match(token, text)
            if match.matches:
                total_score += match.score
            else:
                all_match = False
                break
        if all_match:
            results.append((item, total_score))

    results.sort(key=lambda x: x[1])
    return [item for item, _ in results]
