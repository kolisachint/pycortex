"""Tests for `cortex.tui.util` (port of utils.ts tests)."""

from __future__ import annotations

from cortex.tui.util import (
    extract_ansi_code,
    extract_segments,
    is_punctuation_char,
    is_whitespace_char,
    normalize_terminal_output,
    slice_by_column,
    truncate_to_width,
    visible_width,
    wrap_text_with_ansi,
)


def test_visible_width_ascii() -> None:
    assert visible_width("hello") == 5


def test_visible_width_wide_chars() -> None:
    assert visible_width("中") == 2


def test_visible_width_ansi_codes() -> None:
    assert visible_width("\x1b[31mred\x1b[0m") == 3


def test_visible_width_tabs() -> None:
    assert visible_width("a\tb") == 5


def test_visible_width_regional_indicator_partial_flag() -> None:
    # Partial flag grapheme should be measured as full-width (width 2)
    assert visible_width("\U0001f1e8") == 2


def test_wrap_text_with_ansi_empty() -> None:
    assert wrap_text_with_ansi("", 10) == [""]


def test_wrap_text_with_ansi_no_wrap() -> None:
    assert wrap_text_with_ansi("short", 10) == ["short"]


def test_wrap_text_with_ansi_basic() -> None:
    assert wrap_text_with_ansi("hello world", 5) == ["hello", "world"]


def test_wrap_text_with_ansi_preserves_ansi() -> None:
    text = "\x1b[31mhello world\x1b[0m"
    lines = wrap_text_with_ansi(text, 5)
    assert len(lines) == 2
    assert "\x1b[31m" in lines[0]


def test_wrap_text_with_ansi_wide_chars() -> None:
    # Two wide chars (4 cols) should wrap at width 2 per line
    assert wrap_text_with_ansi("中文", 2) == ["中", "文"]


def test_normalize_terminal_output() -> None:
    assert normalize_terminal_output("\u0e33") == "\u0e4d\u0e32"
    assert normalize_terminal_output("\u0eb3") == "\u0ecd\u0eb2"


def test_extract_ansi_code_csi() -> None:
    code = extract_ansi_code("\x1b[31mred", 0)
    assert code is not None
    assert code.code == "\x1b[31m"
    assert code.length == 5


def test_extract_ansi_code_none() -> None:
    assert extract_ansi_code("hello", 0) is None


def test_is_whitespace_char() -> None:
    assert is_whitespace_char(" ")
    assert not is_whitespace_char("x")


def test_is_punctuation_char() -> None:
    assert is_punctuation_char("!")
    assert not is_punctuation_char("x")


def test_truncate_to_width_no_truncate() -> None:
    assert truncate_to_width("hello", 10) == "hello"


def test_truncate_to_width_pad() -> None:
    assert truncate_to_width("hi", 5, pad=True) == "hi   "


def test_slice_by_column() -> None:
    assert slice_by_column("hello world", 0, 5) == "hello"


def test_extract_segments() -> None:
    before, bw, after, aw = extract_segments("hello world", 5, 6, 5)
    assert before == "hello"
    assert bw == 5
    assert after == "world"
    assert aw == 5
