"""Capturing terminal: records the write stream a renderer produces.

Deliberately dumb. It stores bytes and reports its size; it does **not** try to
interpret anything. Interpretation is `Surface`'s job, and keeping the two apart
is what lets the same interpreter be applied to the TS reference stream and the
Python stream — a shared bug in the interpreter cannot hide a real divergence
between the two implementations, because it cancels on both sides only when the
streams truly agree.
"""

from __future__ import annotations

from collections.abc import Callable

from cortex.tui.testkit._surface import Surface

__all__ = ["CaptureTerminal"]


class CaptureTerminal:
    """Implements enough of the tui `Terminal` protocol to drive a renderer."""

    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        self._columns = columns
        self._rows = rows
        self.writes: list[str] = []
        self.cursor_hidden: bool | None = None
        self._on_input: Callable[[str], None] | None = None
        self._on_resize: Callable[[], None] | None = None

    # ---- Terminal protocol ------------------------------------------------

    @property
    def columns(self) -> int:
        return self._columns

    @property
    def rows(self) -> int:
        return self._rows

    def write(self, data: str) -> None:
        self.writes.append(data)

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        self._on_input = on_input
        self._on_resize = on_resize

    def stop(self) -> None:
        self._on_input = None
        self._on_resize = None

    def hide_cursor(self) -> None:
        self.cursor_hidden = True

    def show_cursor(self) -> None:
        self.cursor_hidden = False

    # ---- test driving -----------------------------------------------------

    def send_input(self, data: str) -> None:
        if self._on_input is not None:
            self._on_input(data)

    def resize(self, columns: int, rows: int) -> None:
        self._columns = columns
        self._rows = rows
        if self._on_resize is not None:
            self._on_resize()

    @property
    def stream(self) -> str:
        """Everything written so far, concatenated."""
        return "".join(self.writes)

    def surface(self) -> Surface:
        """The screen the write stream produces at the current terminal size."""
        return Surface.render(self.stream, cols=self._columns, rows=self._rows)
