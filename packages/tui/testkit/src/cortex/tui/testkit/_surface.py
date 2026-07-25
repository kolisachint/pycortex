"""The authoritative rendering surface: what a terminal actually shows.

TUI code is the one place in this port where "the Python reads like the TS" is
not evidence of correctness. `tui.ts` emits a stream of cursor moves, erases and
text; two very different implementations can emit very different streams and
still land on the same screen — or emit near-identical streams and land on
different ones. The only stable contract is the grid of cells the user ends up
looking at.

`Surface` is that grid, plus enough of an ANSI interpreter to get a write stream
into it. It is deliberately *not* modelled on `tui.ts`: it is modelled on
terminal behaviour, and cross-validated against `@xterm/headless` (see
`reference/xterm_dump.ts` and the `xterm/*` goldens) so that it stays a real
terminal model rather than a convenient one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace

from cortex.tui.util import visible_width

__all__ = [
    "Cell",
    "Style",
    "Surface",
    "grapheme_clusters",
]

# Default colours are `None`; anything else is either a palette index (int) or an
# (r, g, b) triple from a 24-bit SGR.
Color = int | tuple[int, int, int] | None

_ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Mc", "Me"})
_CSI_RE = re.compile(r"\x1b\[([\x30-\x3f]*)([\x20-\x2f]*)([\x40-\x7e])")


def grapheme_clusters(text: str) -> list[str]:
    """Split text into grapheme clusters, matching `cortex.tui.util`'s width model.

    Kept in lockstep with the port's own segmentation on purpose: if the surface
    counted columns differently from the code under test, every wide-character
    parity failure would be the harness's fault rather than the port's.
    """
    clusters: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.category(text[j]) in _ZERO_WIDTH_CATEGORIES:
            j += 1
        clusters.append(text[i:j])
        i = j
    return clusters


@dataclass(frozen=True)
class Style:
    """SGR state carried by a cell."""

    fg: Color = None
    bg: Color = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    strike: bool = False
    hyperlink: str | None = None

    def describe(self) -> str:
        """Compact human-readable form, used in snapshot diffs. Empty when plain."""
        parts: list[str] = []
        for flag in ("bold", "dim", "italic", "underline", "inverse", "strike"):
            if getattr(self, flag):
                parts.append(flag)
        if self.fg is not None:
            parts.append(f"fg={_color_name(self.fg)}")
        if self.bg is not None:
            parts.append(f"bg={_color_name(self.bg)}")
        if self.hyperlink is not None:
            parts.append(f"link={self.hyperlink}")
        return " ".join(parts)


DEFAULT_STYLE = Style()


def _color_name(color: Color) -> str:
    if color is None:
        return "default"
    if isinstance(color, tuple):
        return f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
    return str(color)


@dataclass
class Cell:
    """One terminal cell.

    `char` is a grapheme cluster, or `""` for the trailing half of a wide
    character (the cell is occupied but prints nothing of its own).
    """

    char: str = " "
    style: Style = DEFAULT_STYLE
    wide_continuation: bool = False

    def key(self) -> tuple[str, Style, bool]:
        return (self.char, self.style, self.wide_continuation)


@dataclass
class Surface:
    """A fixed-size cell grid driven by an ANSI write stream.

    Only the sequences the TUI actually emits are interpreted; anything else is
    consumed and recorded in `unhandled` so an unimplemented escape shows up as a
    reported gap instead of silently corrupting the grid.
    """

    cols: int = 80
    rows: int = 24
    cursor_row: int = 0
    cursor_col: int = 0
    cursor_visible: bool = True
    grid: list[list[Cell]] = field(default_factory=lambda: [])
    scrollback: list[list[Cell]] = field(default_factory=lambda: [])
    unhandled: list[str] = field(default_factory=lambda: [])
    apc: list[str] = field(default_factory=lambda: [])
    _style: Style = DEFAULT_STYLE
    _pending_wrap: bool = False

    def __post_init__(self) -> None:
        if not self.grid:
            self.grid = [self._blank_row() for _ in range(self.rows)]

    # ---- construction -----------------------------------------------------

    @classmethod
    def render(cls, data: str, cols: int = 80, rows: int = 24) -> Surface:
        """Convenience: a fresh surface with `data` already written to it."""
        surface = cls(cols=cols, rows=rows)
        surface.feed(data)
        return surface

    def _blank_row(self) -> list[Cell]:
        return [Cell() for _ in range(self.cols)]

    # ---- reading ----------------------------------------------------------

    @property
    def cursor_x(self) -> int:
        """Reported cursor column, which may be one past the last one.

        After a glyph lands in the final column the cursor does not move to the
        next row yet — it sits in a "wrap pending" state that real terminals
        report as column `cols`. That distinction is load-bearing: `ESC [ K` from
        there erases nothing, whereas erasing from `cols - 1` would wipe the last
        character. Verified against @xterm/headless in `ansi/erase-at-pending-wrap`.
        """
        return self.cursor_col + (1 if self._pending_wrap else 0)

    def line(self, row: int, *, rstrip: bool = True) -> str:
        """Row `row` as plain text, wide continuations skipped."""
        text = "".join(cell.char for cell in self.grid[row])
        return text.rstrip() if rstrip else text

    def lines(self, *, rstrip: bool = True) -> list[str]:
        return [self.line(r, rstrip=rstrip) for r in range(self.rows)]

    def text(self) -> str:
        return "\n".join(self.lines())

    def cells(self) -> list[list[tuple[str, Style, bool]]]:
        return [[cell.key() for cell in row] for row in self.grid]

    def styled_runs(self, row: int) -> list[tuple[int, int, Style]]:
        """Maximal runs of identical non-default style on `row`, as (start, end, style)."""
        runs: list[tuple[int, int, Style]] = []
        start: int | None = None
        current = DEFAULT_STYLE
        for col, cell in enumerate(self.grid[row]):
            if cell.style != current:
                if start is not None and current != DEFAULT_STYLE:
                    runs.append((start, col - 1, current))
                start, current = col, cell.style
        if start is not None and current != DEFAULT_STYLE:
            runs.append((start, self.cols - 1, current))
        return runs

    # ---- writing ----------------------------------------------------------

    def feed(self, data: str) -> Surface:
        """Apply a write stream. Returns self so calls can be chained."""
        i = 0
        n = len(data)
        text_start = 0
        while i < n:
            ch = data[i]
            if ch == "\x1b" or (ch < " " and ch != "\t"):
                if text_start < i:
                    self._put_text(data[text_start:i])
                if ch == "\x1b":
                    i = self._escape(data, i)
                else:
                    i = self._control(ch, i)
                text_start = i
                continue
            i += 1
        if text_start < n:
            self._put_text(data[text_start:n])
        return self

    def _control(self, ch: str, i: int) -> int:
        if ch == "\r":
            self.cursor_col = 0
            self._pending_wrap = False
        elif ch == "\n":
            self._index()
        elif ch == "\b":
            self.cursor_col = max(0, self.cursor_col - 1)
            self._pending_wrap = False
        elif ch == "\x07":
            pass
        else:
            self.unhandled.append(f"C0 {ord(ch):#04x}")
        return i + 1

    def _escape(self, data: str, i: int) -> int:
        nxt = data[i + 1] if i + 1 < len(data) else ""
        if nxt == "[":
            match = _CSI_RE.match(data, i)
            if match is None:
                self.unhandled.append(f"malformed CSI at {i}")
                return i + 2
            self._csi(match.group(1), match.group(3))
            return match.end()
        if nxt == "]":
            body, end = _read_string_sequence(data, i + 2)
            self._osc(body)
            return end
        if nxt == "_":
            body, end = _read_string_sequence(data, i + 2)
            self.apc.append(body)
            return end
        if nxt in ("P", "^"):  # DCS / PM — consumed, never rendered
            _, end = _read_string_sequence(data, i + 2)
            return end
        if nxt == "M":  # reverse index
            self._reverse_index()
            return i + 2
        if nxt == "":
            return i + 1
        self.unhandled.append(f"ESC {nxt}")
        return i + 2

    def _csi(self, params_raw: str, final: str) -> None:
        private = params_raw.startswith("?")
        body = params_raw[1:] if private else params_raw
        params = [int(p) if p else 0 for p in body.split(";")] if body else []

        def arg(index: int, default: int = 1) -> int:
            if index >= len(params) or params[index] == 0:
                return default
            return params[index]

        if private:
            self._csi_private(params, final)
            return

        if final == "A":
            self.cursor_row = max(0, self.cursor_row - arg(0))
        elif final == "B":
            self.cursor_row = min(self.rows - 1, self.cursor_row + arg(0))
        elif final == "C":
            self.cursor_col = min(self.cols - 1, self.cursor_col + arg(0))
        elif final == "D":
            self.cursor_col = max(0, self.cursor_col - arg(0))
        elif final == "G":
            self.cursor_col = _clamp(arg(0) - 1, 0, self.cols - 1)
        elif final == "d":
            self.cursor_row = _clamp(arg(0) - 1, 0, self.rows - 1)
        elif final in ("H", "f"):
            self.cursor_row = _clamp(arg(0) - 1, 0, self.rows - 1)
            self.cursor_col = _clamp(arg(1) - 1, 0, self.cols - 1)
        elif final == "J":
            self._erase_display(params[0] if params else 0)
        elif final == "K":
            self._erase_line(params[0] if params else 0)
        elif final == "S":
            for _ in range(arg(0)):
                self._scroll_up()
        elif final == "T":
            for _ in range(arg(0)):
                self._scroll_down()
        elif final == "m":
            self._sgr(params or [0])
        elif final in ("t", "n", "c", "r", "h", "l"):
            pass  # window ops / reports / margins — no effect on the grid
        else:
            self.unhandled.append(f"CSI {params_raw}{final}")
            return
        # Only an explicit cursor move clears the wrap-pending state. Erases do
        # not: `ESC [ K` while pending must erase from column `cols`, i.e. erase
        # nothing, leaving the last glyph intact (see `cursor_x`).
        if final in ("A", "B", "C", "D", "G", "d", "H", "f"):
            self._pending_wrap = False

    def _csi_private(self, params: list[int], final: str) -> None:
        if final not in ("h", "l"):
            self.unhandled.append(f"CSI ?{params}{final}")
            return
        enable = final == "h"
        for param in params:
            if param == 25:
                self.cursor_visible = enable
            elif param in (2026, 1049, 1004, 2004, 1000, 1002, 1003, 1006, 7, 12):
                # Synchronized output, alt screen, focus/mouse/bracketed-paste,
                # autowrap and cursor blink: no effect on a captured frame.
                pass
            else:
                self.unhandled.append(f"CSI ?{param}{final}")

    def _osc(self, body: str) -> None:
        if body.startswith("8;"):
            parts = body.split(";", 2)
            uri = parts[2] if len(parts) > 2 else ""
            self._style = replace(self._style, hyperlink=uri or None)
        # Every other OSC (title, progress, clipboard) leaves the grid alone.

    def _sgr(self, params: list[int]) -> None:
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self._style = replace(DEFAULT_STYLE, hyperlink=self._style.hyperlink)
            elif p == 1:
                self._style = replace(self._style, bold=True)
            elif p == 2:
                self._style = replace(self._style, dim=True)
            elif p == 3:
                self._style = replace(self._style, italic=True)
            elif p == 4:
                self._style = replace(self._style, underline=True)
            elif p == 7:
                self._style = replace(self._style, inverse=True)
            elif p == 9:
                self._style = replace(self._style, strike=True)
            elif p == 22:
                self._style = replace(self._style, bold=False, dim=False)
            elif p == 23:
                self._style = replace(self._style, italic=False)
            elif p == 24:
                self._style = replace(self._style, underline=False)
            elif p == 27:
                self._style = replace(self._style, inverse=False)
            elif p == 29:
                self._style = replace(self._style, strike=False)
            elif 30 <= p <= 37:
                self._style = replace(self._style, fg=p - 30)
            elif 90 <= p <= 97:
                self._style = replace(self._style, fg=p - 90 + 8)
            elif 40 <= p <= 47:
                self._style = replace(self._style, bg=p - 40)
            elif 100 <= p <= 107:
                self._style = replace(self._style, bg=p - 100 + 8)
            elif p == 39:
                self._style = replace(self._style, fg=None)
            elif p == 49:
                self._style = replace(self._style, bg=None)
            elif p in (38, 48):
                color, consumed = _extended_color(params, i)
                if consumed == 0:
                    self.unhandled.append(f"SGR {params[i:]}")
                    return
                self._style = (
                    replace(self._style, fg=color) if p == 38 else replace(self._style, bg=color)
                )
                i += consumed
            else:
                self.unhandled.append(f"SGR {p}")
            i += 1

    # ---- grid primitives --------------------------------------------------

    def _put_text(self, text: str) -> None:
        for cluster in grapheme_clusters(text):
            if cluster == "\t":
                self.cursor_col = min(self.cols - 1, (self.cursor_col // 8 + 1) * 8)
                continue
            width = visible_width(cluster)
            if width == 0:
                # Combining mark with no base on this cell: attach to the left.
                col = self.cursor_col - 1
                if 0 <= col < self.cols:
                    self.grid[self.cursor_row][col].char += cluster
                continue
            if self._pending_wrap or self.cursor_col + width > self.cols:
                self._wrap()
            row = self.grid[self.cursor_row]
            row[self.cursor_col] = Cell(char=cluster, style=self._style)
            for offset in range(1, width):
                col = self.cursor_col + offset
                if col < self.cols:
                    row[col] = Cell(char="", style=self._style, wide_continuation=True)
            self.cursor_col += width
            if self.cursor_col >= self.cols:
                self.cursor_col = self.cols - 1
                self._pending_wrap = True

    def _wrap(self) -> None:
        self.cursor_col = 0
        self._pending_wrap = False
        self._index()

    def _index(self) -> None:
        """Line feed: down one row, scrolling the region when already at the bottom."""
        self._pending_wrap = False
        if self.cursor_row >= self.rows - 1:
            self._scroll_up()
        else:
            self.cursor_row += 1

    def _reverse_index(self) -> None:
        self._pending_wrap = False
        if self.cursor_row == 0:
            self._scroll_down()
        else:
            self.cursor_row -= 1

    def _scroll_up(self) -> None:
        self.scrollback.append(self.grid.pop(0))
        self.grid.append(self._blank_row())

    def _scroll_down(self) -> None:
        self.grid.pop()
        self.grid.insert(0, self._blank_row())

    def _erase_line(self, mode: int) -> None:
        row = self.grid[self.cursor_row]
        start = self.cursor_x
        span: range
        if mode == 0:
            span = range(min(start, self.cols), self.cols)
        elif mode == 1:
            span = range(0, min(start + 1, self.cols))
        elif mode == 2:
            span = range(0, self.cols)
        else:
            self.unhandled.append(f"EL {mode}")
            return
        for col in span:
            row[col] = Cell(style=self._erase_style())

    def _erase_display(self, mode: int) -> None:
        if mode == 0:
            self._erase_line(0)
            for r in range(self.cursor_row + 1, self.rows):
                self.grid[r] = [Cell(style=self._erase_style()) for _ in range(self.cols)]
        elif mode == 1:
            self._erase_line(1)
            for r in range(0, self.cursor_row):
                self.grid[r] = [Cell(style=self._erase_style()) for _ in range(self.cols)]
        elif mode == 2:
            self.grid = [
                [Cell(style=self._erase_style()) for _ in range(self.cols)]
                for _ in range(self.rows)
            ]
        elif mode == 3:
            self.scrollback.clear()
        else:
            self.unhandled.append(f"ED {mode}")

    def _erase_style(self) -> Style:
        """Erases paint the current background, but never text attributes."""
        return Style(bg=self._style.bg)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _extended_color(params: list[int], i: int) -> tuple[Color, int]:
    """Parse `38;5;n` / `38;2;r;g;b`. Returns (color, params consumed after the 38)."""
    if i + 1 >= len(params):
        return None, 0
    mode = params[i + 1]
    if mode == 5 and i + 2 < len(params):
        return params[i + 2], 2
    if mode == 2 and i + 4 < len(params):
        return (params[i + 2], params[i + 3], params[i + 4]), 4
    return None, 0


def _read_string_sequence(data: str, start: int) -> tuple[str, int]:
    """Read an OSC/APC/DCS body terminated by BEL or ST. Returns (body, index after)."""
    bel = data.find("\x07", start)
    st = data.find("\x1b\\", start)
    if bel == -1 and st == -1:
        return data[start:], len(data)
    if st == -1 or (bel != -1 and bel < st):
        return data[start:bel], bel + 1
    return data[start:st], st + 2
