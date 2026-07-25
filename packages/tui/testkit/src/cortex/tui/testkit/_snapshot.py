"""Human-readable snapshots and diffs of a `Surface`.

A cell-grid mismatch is useless as a raw dataclass repr, so failures render the
two screens side by side with the differing cells called out. This is the part
that makes "cross-check against the surface" a workflow rather than a slogan.
"""

from __future__ import annotations

from dataclasses import dataclass

from cortex.tui.testkit._surface import DEFAULT_STYLE, Color, Style, Surface
from cortex.tui.util import visible_width

__all__ = ["CellDiff", "SurfaceDiff", "diff_surfaces", "snapshot"]


def snapshot(surface: Surface, *, styles: bool = True, cursor: bool = True) -> str:
    """Deterministic text rendering of a surface, safe to store as a golden."""
    gutter = len(str(max(surface.rows - 1, 0)))
    out: list[str] = [f"{'':>{gutter}} ┌{'─' * surface.cols}┐"]
    for row in range(surface.rows):
        line = surface.line(row, rstrip=False)
        # A wide cell contributes one character but two columns, so pad by the
        # visible width rather than the string length to keep the box square.
        line += " " * max(0, surface.cols - visible_width(line))
        out.append(f"{row:>{gutter}} │{line}│")
    out.append(f"{'':>{gutter}} └{'─' * surface.cols}┘")

    if cursor:
        visible = "visible" if surface.cursor_visible else "hidden"
        pending = " wrap-pending" if surface.cursor_x != surface.cursor_col else ""
        out.append(f"cursor: row={surface.cursor_row} col={surface.cursor_x} {visible}{pending}")
    if styles:
        rendered = _style_lines(surface)
        out.append("styles: " + ("none" if not rendered else ""))
        out.extend(rendered)
    if surface.scrollback:
        out.append(f"scrollback: {len(surface.scrollback)} line(s)")
    if surface.unhandled:
        out.append("unhandled: " + ", ".join(sorted(set(surface.unhandled))))
    return "\n".join(out)


def _style_lines(surface: Surface) -> list[str]:
    lines: list[str] = []
    for row in range(surface.rows):
        for start, end, style in surface.styled_runs(row):
            span = f"{start}" if start == end else f"{start}-{end}"
            lines.append(f"  [{row}] {span}: {style.describe()}")
    return lines


@dataclass(frozen=True)
class CellDiff:
    row: int
    col: int
    expected_char: str
    actual_char: str
    expected_style: Style
    actual_style: Style

    def describe(self) -> str:
        bits: list[str] = []
        if self.expected_char != self.actual_char:
            bits.append(f"char {self.expected_char!r} != {self.actual_char!r}")
        if self.expected_style != self.actual_style:
            exp = self.expected_style.describe() or "plain"
            act = self.actual_style.describe() or "plain"
            bits.append(f"style {exp!r} != {act!r}")
        return f"({self.row},{self.col}) " + "; ".join(bits)


@dataclass(frozen=True)
class SurfaceDiff:
    cells: tuple[CellDiff, ...]
    cursor: tuple[tuple[int, int], tuple[int, int]] | None
    size: tuple[tuple[int, int], tuple[int, int]] | None

    def __bool__(self) -> bool:
        return bool(self.cells or self.cursor or self.size)

    def report(
        self,
        expected: Surface,
        actual: Surface,
        *,
        expected_label: str = "expected (hoocode TS)",
        actual_label: str = "actual (pycortex)",
        max_cells: int = 20,
    ) -> str:
        out: list[str] = []
        if self.size is not None:
            out.append(f"size: {self.size[0]} != {self.size[1]}")
        if self.cursor is not None:
            out.append(f"cursor: {self.cursor[0]} != {self.cursor[1]}")
        if self.cells:
            rows = sorted({c.row for c in self.cells})
            out.append(f"{len(self.cells)} differing cell(s) on row(s) {rows}")
            for cell in self.cells[:max_cells]:
                out.append(f"  {cell.describe()}")
            if len(self.cells) > max_cells:
                out.append(f"  … {len(self.cells) - max_cells} more")
        out.append(f"\n--- {expected_label} ---\n{snapshot(expected)}")
        out.append(f"\n--- {actual_label} ---\n{snapshot(actual)}")
        return "\n".join(out)


def diff_surfaces(
    expected: Surface,
    actual: Surface,
    *,
    compare_styles: bool = True,
    compare_cursor: bool = True,
) -> SurfaceDiff:
    """Structural diff. `compare_styles=False` compares only the characters."""
    if (expected.rows, expected.cols) != (actual.rows, actual.cols):
        return SurfaceDiff(
            cells=(),
            cursor=None,
            size=((expected.rows, expected.cols), (actual.rows, actual.cols)),
        )

    cells: list[CellDiff] = []
    for row in range(expected.rows):
        for col in range(expected.cols):
            exp = expected.grid[row][col]
            act = actual.grid[row][col]
            char_differs = exp.char != act.char or exp.wide_continuation != act.wide_continuation
            style_differs = compare_styles and exp.style != act.style
            # Most of a blank cell's style is unobservable — a bold, italic or
            # red space looks like any other space. Three attributes do paint
            # one: a background, an underline or strike rule drawn through it,
            # and inverse (which swaps the invisible fg onto the visible bg).
            # Ignoring those would hide exactly the bug hoocode's own
            # "underline leaks into the padding" test exists to catch.
            if style_differs and _blank(exp) and _blank(act):
                style_differs = _blank_paint(exp.style) != _blank_paint(act.style)
            if char_differs or style_differs:
                cells.append(
                    CellDiff(
                        row=row,
                        col=col,
                        expected_char=exp.char,
                        actual_char=act.char,
                        expected_style=exp.style if compare_styles else DEFAULT_STYLE,
                        actual_style=act.style if compare_styles else DEFAULT_STYLE,
                    )
                )

    cursor: tuple[tuple[int, int], tuple[int, int]] | None = None
    if compare_cursor:
        exp_pos = (expected.cursor_row, expected.cursor_x)
        act_pos = (actual.cursor_row, actual.cursor_x)
        if exp_pos != act_pos:
            cursor = (exp_pos, act_pos)

    return SurfaceDiff(cells=tuple(cells), cursor=cursor, size=None)


def _blank(cell: object) -> bool:
    char = getattr(cell, "char", " ")
    return char in ("", " ")


def _blank_paint(style: Style) -> tuple[Color, bool, bool, bool]:
    """The part of a style a blank cell still shows on screen."""
    return (style.bg, style.underline, style.strike, style.inverse)
