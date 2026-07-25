"""Unit tests for `Surface` behaviour the xterm corpus does not pin down.

The xterm cross-check covers ANSI interpretation. These cover the surface's own
API — scrollback, APC capture, diffing, snapshots — which have no counterpart in
a terminal emulator's public surface but which the parity tests depend on.
"""

from __future__ import annotations

from cortex.tui.testkit import (
    CaptureTerminal,
    Style,
    Surface,
    diff_surfaces,
    grapheme_clusters,
    snapshot,
    surface_from_lines,
)


def test_blank_surface_is_all_spaces() -> None:
    surface = Surface(cols=4, rows=2)
    assert surface.lines(rstrip=False) == ["    ", "    "]
    assert surface.text() == "\n"


def test_scrolled_rows_go_to_scrollback() -> None:
    surface = Surface.render("a\r\nb\r\nc\r\nd", cols=4, rows=2)
    assert surface.lines() == ["c", "d"]
    assert len(surface.scrollback) == 2
    assert "".join(cell.char for cell in surface.scrollback[0]).strip() == "a"


def test_wide_character_occupies_two_cells() -> None:
    surface = Surface.render("你x", cols=4, rows=1)
    assert surface.grid[0][0].char == "你"
    assert surface.grid[0][1].wide_continuation is True
    assert surface.grid[0][1].char == ""
    assert surface.grid[0][2].char == "x"


def test_combining_mark_attaches_to_previous_cell() -> None:
    surface = Surface.render("é", cols=4, rows=1)
    assert surface.grid[0][0].char == "é"
    assert surface.grid[0][1].char == " "


def test_hyperlink_is_carried_on_the_cell() -> None:
    stream = "\x1b]8;;https://example.com\x07link\x1b]8;;\x07 plain"
    surface = Surface.render(stream, cols=20, rows=1)
    assert surface.grid[0][0].style.hyperlink == "https://example.com"
    assert surface.grid[0][5].style.hyperlink is None


def test_apc_sequences_are_captured_not_printed() -> None:
    # The TUI's cursor marker is an APC sequence; it must never reach the grid.
    surface = Surface.render("ab\x1b_pi:c\x07cd", cols=8, rows=1)
    assert surface.line(0) == "abcd"
    assert surface.apc == ["pi:c"]


def test_unhandled_sequences_are_recorded() -> None:
    surface = Surface.render("\x1b[99Zx", cols=8, rows=1)
    assert surface.unhandled
    assert surface.line(0) == "x"


def test_erase_paints_current_background_only() -> None:
    surface = Surface.render("\x1b[1;44mtext\x1b[2K", cols=6, rows=1)
    erased = surface.grid[0][0]
    assert erased.char == " "
    assert erased.style.bg == 4
    assert erased.style.bold is False, "erase must not carry text attributes"


def test_styled_runs_groups_adjacent_cells() -> None:
    surface = Surface.render("\x1b[31mred\x1b[39m x", cols=10, rows=1)
    assert surface.styled_runs(0) == [(0, 2, Style(fg=1))]


def test_diff_reports_the_differing_cell() -> None:
    left = Surface.render("abc", cols=4, rows=1)
    right = Surface.render("abd", cols=4, rows=1)
    diff = diff_surfaces(left, right)
    assert diff
    assert [(c.row, c.col) for c in diff.cells] == [(0, 2)]
    assert "char 'c' != 'd'" in diff.cells[0].describe()


def test_identical_surfaces_do_not_diff() -> None:
    assert not diff_surfaces(Surface.render("hi", cols=4, rows=1), Surface.render("hi", 4, 1))


def test_diff_ignores_style_of_blank_cells_without_background() -> None:
    plain = Surface.render("a", cols=4, rows=1)
    styled = Surface.render("a\x1b[1m", cols=4, rows=1)
    assert not diff_surfaces(plain, styled)


def test_diff_notices_background_on_blank_cells() -> None:
    plain = Surface.render("a", cols=4, rows=1)
    filled = Surface.render("a\x1b[44m\x1b[K", cols=4, rows=1)
    assert diff_surfaces(plain, filled)


def test_snapshot_frames_the_grid() -> None:
    text = snapshot(Surface.render("hi", cols=4, rows=1))
    assert "│hi  │" in text
    assert "cursor: row=0 col=2 visible" in text


def test_surface_from_lines_lays_out_one_line_per_row() -> None:
    surface = surface_from_lines(["ab", "cd"], width=4)
    assert surface.rows == 2
    assert surface.lines() == ["ab", "cd"]


def test_capture_terminal_records_and_replays() -> None:
    terminal = CaptureTerminal(columns=6, rows=2)
    terminal.write("ab")
    terminal.write("\r\ncd")
    assert terminal.stream == "ab\r\ncd"
    assert terminal.surface().lines() == ["ab", "cd"]


def test_grapheme_clusters_keeps_marks_with_their_base() -> None:
    assert grapheme_clusters("áb") == ["á", "b"]
