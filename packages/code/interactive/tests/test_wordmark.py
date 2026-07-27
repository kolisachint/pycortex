"""Tests for the startup wordmark."""

from __future__ import annotations

from cortex.code.interactive.wordmark import (
    WORDMARK,
    WORDMARK_COMPACT,
    WORDMARK_GLYPH,
    CompactWordmarkOptions,
    build_compact_wordmark,
)
from cortex.tui.util import visible_width

PLAIN = CompactWordmarkOptions(
    app_name="hoocode",
    version="0.1.0",
    cwd="~/project",
    accent=lambda text: text,
    dim=lambda text: text,
    muted=lambda text: text,
)


def _options(**overrides: object) -> CompactWordmarkOptions:
    merged: dict[str, object] = dict(vars(PLAIN))
    merged.update(overrides)
    return CompactWordmarkOptions(**merged)  # pyright: ignore[reportArgumentType]


# Distinguishable markers for the three colour roles: with the identity
# functions of `PLAIN` there is nothing to tell "accented" from "not".
def angle(text: str) -> str:
    return f"<{text}>"


def bracket(text: str) -> str:
    return f"[{text}]"


def paren(text: str) -> str:
    return f"({text})"


def test_glyph_is_three_lines_of_equal_width():
    assert len(WORDMARK_GLYPH) == 3
    assert {visible_width(line) for line in WORDMARK_GLYPH} == {7}


def test_ascii_wordmark_and_compact_form():
    assert WORDMARK.splitlines()[0].endswith("__  __            ______          __")
    assert WORDMARK_COMPACT == "hoo — deterministic terminal coding agent"


def test_builds_three_rows_glyph_beside_text():
    lines = build_compact_wordmark(PLAIN).splitlines()
    assert len(lines) == 3
    for glyph_line, rendered in zip(WORDMARK_GLYPH, lines, strict=True):
        assert rendered.startswith(f"   {glyph_line}  ")
    assert lines[0].endswith("hoo│code")
    assert lines[1].endswith("agentic coding agent · v0.1.0")
    assert lines[2].endswith("~/project")


def test_hoo_prefix_is_split_out_for_the_accent():
    options = _options(accent=angle, muted=bracket)
    first = build_compact_wordmark(options).splitlines()[0]
    assert first.endswith("<hoo>[│]code")


def test_a_name_without_the_prefix_is_accented_whole():
    options = _options(app_name="pycortex", accent=angle)
    first = build_compact_wordmark(options).splitlines()[0]
    assert first.endswith("<pycortex>")
    assert "│" not in first


def test_cursor_is_appended_only_when_supplied():
    assert build_compact_wordmark(PLAIN).splitlines()[0].endswith("hoo│code")
    with_cursor = _options(cursor=paren)
    assert build_compact_wordmark(with_cursor).splitlines()[0].endswith("hoo│code(_)")


def test_note_is_appended_to_the_cwd_line_only_when_supplied():
    with_note = _options(note=lambda: "  ctrl+o more")
    assert build_compact_wordmark(with_note).splitlines()[2].endswith("~/project  ctrl+o more")


def test_tagline_defaults_and_overrides():
    assert "agentic coding agent" in build_compact_wordmark(PLAIN)
    custom = _options(tagline="deterministic terminal coding agent")
    assert "deterministic terminal coding agent · v0.1.0" in build_compact_wordmark(custom)
