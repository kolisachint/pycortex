"""Tests for the diff colouring.

The input is the shape `generate_diff_string` emits, so these fix the *parse*
(what counts as a removal, an addition, a context line, and a line that is none
of those) and the *intra-line* rule, which is the only thing here that computes
rather than paints.
"""

from __future__ import annotations

from cortex.code.interactive.components.diff import (
    parse_diff_line,
    render_diff,
    render_intra_line_diff,
)
from cortex.tui.util import visible_width


def plain(text: str) -> str:
    """The text with its styling stripped — what the user would read off it."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestParseDiffLine:
    def test_reads_a_removal(self):
        parsed = parse_diff_line("-12 old line")
        assert parsed is not None
        assert (parsed.prefix, parsed.line_num, parsed.content) == ("-", "12", "old line")

    def test_reads_an_addition(self):
        parsed = parse_diff_line("+ 7 new line")
        assert parsed is not None
        assert (parsed.prefix, parsed.line_num, parsed.content) == ("+", " 7", "new line")

    def test_reads_a_context_line(self):
        parsed = parse_diff_line("  3 unchanged")
        assert parsed is not None
        assert (parsed.prefix, parsed.line_num, parsed.content) == (" ", " 3", "unchanged")

    def test_rejects_a_line_that_is_not_a_diff_line(self):
        assert parse_diff_line("[diff truncated for brevity]") is None

    def test_keeps_content_that_starts_with_a_marker(self):
        # `-1 -x = 1` is a removed line whose *content* also starts with a dash;
        # a greedy parse would eat it.
        parsed = parse_diff_line("-1 -x = 1")
        assert parsed is not None
        assert parsed.content == "-x = 1"


class TestRenderDiff:
    def test_marks_every_line_with_its_prefix(self):
        out = plain(render_diff("  1 kept\n-2 gone\n+2 added"))
        # The context line keeps the space its own line number was aligned with,
        # so all three markers sit in the same column.
        assert out.splitlines() == ["  1 kept", "-2 gone", "+2 added"]

    def test_colours_removals_additions_and_context_differently(self):
        lines = render_diff("  1 kept\n-2 gone\n+2 added").splitlines()
        codes = [line[: line.index("m") + 1] for line in lines]
        assert len(set(codes)) == 3, f"colours are not distinct: {codes}"

    def test_a_line_that_does_not_parse_survives_as_context(self):
        out = plain(render_diff("[diff truncated for brevity]"))
        assert out == "[diff truncated for brevity]"

    def test_tabs_become_spaces(self):
        out = plain(render_diff("+1 \tindented"))
        assert out == "+1    indented"

    def test_a_one_for_one_hunk_highlights_the_changed_words(self):
        rendered = render_diff('-2     print("hello")\n+2     print("goodbye")')
        assert "\x1b[7m" in rendered, "no inverse on a single-line modification"

    def test_a_wider_hunk_shows_the_lines_as_they_are(self):
        # Two out, two in: word-diffing across a block reads as noise, so the TS
        # does not, and neither does this.
        rendered = render_diff("-1 a\n-2 b\n+1 c\n+2 d")
        assert "\x1b[7m" not in rendered
        assert [line[:2] for line in plain(rendered).splitlines()] == ["-1", "-2", "+1", "+2"]

    def test_removals_come_out_before_additions(self):
        out = plain(render_diff("-1 a\n-2 b\n+1 c\n+2 d")).splitlines()
        assert [line[0] for line in out] == ["-", "-", "+", "+"]


class TestRenderIntraLineDiff:
    def test_only_the_changed_word_is_inverted(self):
        removed, added = render_intra_line_diff("value = old_name", "value = new_name")
        assert plain(removed) == "value = old_name"
        assert plain(added) == "value = new_name"
        # The common prefix is outside the inverse run in both lines.
        assert removed.startswith("value = \x1b[7m")
        assert added.startswith("value = \x1b[7m")

    def test_leading_indentation_is_never_inverted(self):
        removed, added = render_intra_line_diff("    one", "    two")
        assert removed.startswith("    \x1b[7m")
        assert added.startswith("    \x1b[7m")

    def test_indentation_that_was_itself_removed_is_not_inverted(self):
        # The one shape where the strip does work: the old line was indented and
        # the new one is not, so the first *changed* part is the whitespace. An
        # inverted run of blanks is a block of background over an edit nobody
        # made.
        removed, added = render_intra_line_diff("    one", "one")
        assert "\x1b[7m" not in removed, f"the indentation was highlighted: {removed!r}"
        assert plain(removed) == "    one"
        assert plain(added) == "one"

    def test_a_pure_whitespace_change_highlights_nothing(self):
        # `diffWords` ignores whitespace differences; `diffWordsWithSpace` does
        # not, and the TS picked the first. Re-indenting a line must not light
        # the whole line up.
        removed, added = render_intra_line_diff("a  b", "a b")
        assert "\x1b[7m" not in removed
        assert "\x1b[7m" not in added

    def test_identical_lines_produce_no_highlight(self):
        removed, added = render_intra_line_diff("same", "same")
        assert removed == "same"
        assert added == "same"

    def test_the_visible_text_is_unchanged_by_highlighting(self):
        removed, added = render_intra_line_diff("a b c", "a x c")
        assert visible_width(removed) == len("a b c")
        assert visible_width(added) == len("a x c")
