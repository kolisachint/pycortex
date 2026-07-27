"""A terminal the app can be booted against without owning a real tty.

``tui/testkit``'s :class:`CaptureTerminal` already models the renderer's slice of
the terminal — writes in, cell grid out. It stops there on purpose: it is the
thing that gets pointed at both the TS and the Python renderer, so it implements
only what *rendering* needs.

The app layer needs more. Interactive mode sets the window title, raises the
progress indicator while a turn is in flight, and drains stdin on the way out.
Those are app behaviours, so they are modelled here rather than in the tui leaf,
and they are *recorded* rather than ignored — "did the turn set the progress
indicator" is an assertion an end-to-end scenario is entitled to make.
"""

from __future__ import annotations

from collections.abc import Callable

from cortex.tui.terminal import Terminal
from cortex.tui.testkit import CaptureTerminal, Surface

__all__ = ["HarnessTerminal"]


class HarnessTerminal(Terminal):
    """Full ``Terminal`` ABC over a :class:`CaptureTerminal`.

    Delegates everything the renderer touches to the capture terminal (so the
    cell grid stays the testkit's model, not a second one that could drift), and
    records the app-level calls the renderer never makes.
    """

    def __init__(self, columns: int = 80, rows: int = 24) -> None:
        self._capture = CaptureTerminal(columns=columns, rows=rows)
        self.titles: list[str] = []
        self.progress_states: list[bool] = []
        self.drained = 0
        self.stopped = False

    # ---- delegated to the capture terminal --------------------------------

    @property
    def columns(self) -> int:
        return self._capture.columns

    @property
    def rows(self) -> int:
        return self._capture.rows

    @property
    def kitty_protocol_active(self) -> bool:
        return False

    def write(self, data: str) -> None:
        self._capture.write(data)

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None:
        self._capture.start(on_input, on_resize)

    def stop(self) -> None:
        self.stopped = True
        self._capture.stop()

    def hide_cursor(self) -> None:
        self._capture.hide_cursor()

    def show_cursor(self) -> None:
        self._capture.show_cursor()

    # ---- app-level surface, recorded --------------------------------------

    def drain_input(self, max_ms: float = 1000, idle_ms: float = 50) -> None:
        self.drained += 1

    def set_title(self, title: str) -> None:
        self.titles.append(title)

    def set_progress(self, active: bool) -> None:
        self.progress_states.append(active)

    # ---- cursor / clearing ------------------------------------------------
    #
    # These go through `write` so they land in the same stream the Surface
    # interprets. Implementing them any other way would put cursor motion
    # outside the model and quietly desynchronise the grid from the app.

    def move_by(self, lines: int) -> None:
        if lines < 0:
            self.write(f"\x1b[{-lines}A")
        elif lines > 0:
            self.write(f"\x1b[{lines}B")

    def clear_line(self) -> None:
        self.write("\x1b[2K")

    def clear_from_cursor(self) -> None:
        self.write("\x1b[0J")

    def clear_screen(self) -> None:
        self.write("\x1b[2J\x1b[H")

    # ---- driving ----------------------------------------------------------

    def send_input(self, data: str) -> None:
        self._capture.send_input(data)

    def resize(self, columns: int, rows: int) -> None:
        self._capture.resize(columns, rows)

    @property
    def stream(self) -> str:
        return self._capture.stream

    def surface(self) -> Surface:
        return self._capture.surface()

    @property
    def cursor_hidden(self) -> bool | None:
        return self._capture.cursor_hidden
