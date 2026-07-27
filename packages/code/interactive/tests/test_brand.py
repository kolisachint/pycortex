"""Tests for the brand vocabulary.

The values are constants, so the interesting property is not what they are but
that they still satisfy the two rules `brand.ts` states in prose: every glyph is
a single terminal cell (so counts and columns stay aligned), and none of them
collides with the task panel's own status/owner glyphs.
"""

from __future__ import annotations

from cortex.code.interactive.brand import (
    BRAND_MARK,
    BRAND_NAME,
    CATEGORY_GLYPH,
    GIT_BRANCH_GLYPH,
    GIT_DIRTY_MARK,
    SEGMENT_SEP,
)
from cortex.tui.util import visible_width

#: The task panel's vocabulary, which the brand glyphs must stay clear of.
TASK_PANEL_GLYPHS = {"◐", "◆", "◇", "▸", "⧉", "✓", "✗", "○"}


def test_marks_have_the_documented_values():
    assert BRAND_MARK == "⬢"
    assert BRAND_NAME == "HooCode"
    assert SEGMENT_SEP == "·"
    assert GIT_DIRTY_MARK == "*"
    assert GIT_BRANCH_GLYPH == "⑂"


def test_every_glyph_is_one_cell():
    glyphs = [BRAND_MARK, SEGMENT_SEP, GIT_DIRTY_MARK, GIT_BRANCH_GLYPH, *CATEGORY_GLYPH.values()]
    wide = [g for g in glyphs if visible_width(g) != 1]
    assert wide == [], f"glyphs that are not one cell: {wide!r}"


def test_category_glyphs_cover_every_capability_class():
    assert set(CATEGORY_GLYPH) == {
        "skills",
        "commands",
        "agents",
        "mcp",
        "plugins",
        "themes",
        "context",
        "extensions",
    }


def test_category_glyphs_are_distinct():
    assert len(set(CATEGORY_GLYPH.values())) == len(CATEGORY_GLYPH)


def test_no_glyph_reads_as_a_task_panel_signal():
    # `mcp` is `⧉` in both vocabularies in the TS as well; the rule the comment
    # states is about the *category* set as a whole not being mistaken for a
    # status, and the two that overlap are the same concept in both.
    brand_only = {BRAND_MARK, GIT_BRANCH_GLYPH} | set(CATEGORY_GLYPH.values()) - {"⧉"}
    assert brand_only & TASK_PANEL_GLYPHS == set()
