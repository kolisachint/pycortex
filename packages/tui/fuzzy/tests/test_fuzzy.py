"""Tests for `cortex.tui.fuzzy` (port of fuzzy.ts tests)."""

from __future__ import annotations

from cortex.tui.fuzzy import fuzzy_filter, fuzzy_match


def test_fuzzy_match_empty_query() -> None:
    result = fuzzy_match("", "anything")
    assert result.matches
    assert result.score == 0


def test_fuzzy_match_query_longer_than_text() -> None:
    result = fuzzy_match("longquery", "short")
    assert not result.matches
    assert result.score == 0


def test_fuzzy_match_exact_match() -> None:
    result = fuzzy_match("hello", "hello")
    assert result.matches
    assert result.score < 0


def test_fuzzy_match_subsequence() -> None:
    result = fuzzy_match("hl", "hello")
    assert result.matches


def test_fuzzy_match_no_match() -> None:
    result = fuzzy_match("xyz", "hello")
    assert not result.matches


def test_fuzzy_filter() -> None:
    items = ["hello", "world", "help"]
    result = fuzzy_filter(items, "he", lambda x: x)
    assert "hello" in result
    assert "help" in result


def test_fuzzy_filter_empty_query() -> None:
    items = ["hello", "world"]
    assert fuzzy_filter(items, "", lambda x: x) == items


def test_fuzzy_filter_swapped_letters_digits() -> None:
    result = fuzzy_match("abc123", "123abc")
    assert result.matches
