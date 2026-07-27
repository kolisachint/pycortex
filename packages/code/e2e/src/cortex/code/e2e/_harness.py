"""Boot the app's TUI headlessly, press keys, look at the screen.

This is the app-level counterpart of `tui/testkit`, and it exists for the same
reason: at the app layer "the Python reads like the TS" stops being evidence.
Interactive mode is a state machine over containers, overlays and focus, and a
plausible-looking port can produce a screen the user would not recognise. The
contract is the grid of cells, so the harness renders through the **real** `TUI`
onto the testkit `Surface` and lets scenarios assert against that.

Nothing here is app-specific: the harness takes a callable that populates the
TUI. Step 7.2 supplies the real interactive-mode root; until then the same
harness drives synthetic roots, which is what proves the harness itself works.

Rendering is synchronous on purpose. `TUI.request_render` coalesces frames onto
the event loop, which is right in production and useless in a test — a scenario
that has to sleep to see its own keystroke is a flaky scenario. Every driving
method ends in `render()`, which calls `TUI.render_now()`, so the surface is
always the frame that follows the input just sent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from cortex.code.e2e._keys import resolve_key
from cortex.code.e2e._terminal import HarnessTerminal
from cortex.tui.render import TUI
from cortex.tui.testkit import Surface, snapshot

__all__ = ["AppHarness"]

# A root builder gets the live TUI and attaches whatever the app's root is.
RootBuilder = Callable[[TUI], None]


class AppHarness:
    """A booted app TUI plus the means to drive it and read the screen."""

    def __init__(
        self,
        build_root: RootBuilder,
        *,
        columns: int = 80,
        rows: int = 24,
    ) -> None:
        self.terminal = HarnessTerminal(columns=columns, rows=rows)
        self.tui = TUI(self.terminal)
        build_root(self.tui)
        self.tui.start()
        self.render()

    # ---- lifecycle --------------------------------------------------------

    def stop(self) -> None:
        self.tui.stop()

    def __enter__(self) -> AppHarness:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ---- driving ----------------------------------------------------------

    def render(self) -> None:
        """Flush a frame now, bypassing the event-loop coalescing."""
        self.tui.render_now()

    def send(self, data: str) -> None:
        """Feed raw bytes as if the terminal produced them, then render."""
        self.terminal.send_input(data)
        self.render()

    def type(self, text: str) -> None:
        """Type literal text. One `send` per character, as a real tty delivers."""
        for char in text:
            self.terminal.send_input(char)
        self.render()

    def key(self, name: str) -> None:
        """Press a named key (see `_keys.KEY_SEQUENCES`)."""
        self.send(resolve_key(name))

    def keys(self, names: Iterable[str]) -> None:
        for name in names:
            self.key(name)

    def resize(self, columns: int, rows: int) -> None:
        self.terminal.resize(columns, rows)
        # A resize invalidates every cached line; force the full repaint the
        # real app gets via the renderer's width-changed path.
        self.tui.request_render(force=True)
        self.render()

    # ---- reading the screen ----------------------------------------------

    def surface(self) -> Surface:
        """The cell grid currently on screen."""
        return self.terminal.surface()

    def screen(self) -> str:
        """The visible grid as plain text, one line per row."""
        return "\n".join(self.surface().lines()).rstrip("\n")

    def scrollback(self) -> str:
        """Lines that have scrolled off the top of the viewport."""
        surface = self.surface()
        rows = ["".join(cell.char for cell in row).rstrip() for row in surface.scrollback]
        return "\n".join(rows)

    def transcript(self) -> str:
        """Scrollback followed by the visible grid — the whole session so far.

        Chat is append-only and the viewport is 24 rows, so by the third turn the
        first turn is off-screen. A scenario asking "did my message render" means
        the transcript, not the viewport.
        """
        back = self.scrollback()
        return f"{back}\n{self.screen()}" if back else self.screen()

    def snapshot(self, *, styles: bool = True, cursor: bool = True) -> str:
        """The testkit's bordered snapshot — what to paste into a failure report."""
        return snapshot(self.surface(), styles=styles, cursor=cursor)

    # ---- assertions -------------------------------------------------------

    def contains(self, text: str, *, scrollback: bool = True) -> bool:
        """Whether `text` appears on any single line of the session.

        Line-scoped, not whole-text: the screen is a grid, and a match that
        straddles a line break is an accident of joining, not something the user
        can read. Set `scrollback=False` to require it be currently visible.
        """
        haystack = self.transcript() if scrollback else self.screen()
        return any(text in line for line in haystack.splitlines())

    def assert_shows(self, *texts: str, scrollback: bool = True) -> None:
        """Assert every string appears; on failure, show the screen."""
        missing = [t for t in texts if not self.contains(t, scrollback=scrollback)]
        if missing:
            shown = ", ".join(repr(t) for t in missing)
            raise AssertionError(f"expected on screen but missing: {shown}\n\n{self.snapshot()}")

    def assert_hides(self, *texts: str, scrollback: bool = True) -> None:
        """Assert none of the strings appear."""
        present = [t for t in texts if self.contains(t, scrollback=scrollback)]
        if present:
            shown = ", ".join(repr(t) for t in present)
            raise AssertionError(
                f"expected gone from screen but present: {shown}\n\n{self.snapshot()}"
            )

    # ---- app-level terminal effects --------------------------------------

    @property
    def titles(self) -> list[str]:
        return self.terminal.titles

    @property
    def progress_states(self) -> list[bool]:
        return self.terminal.progress_states

    def state(self) -> dict[str, Any]:
        """Everything a scenario might assert on, in one dict (for reports)."""
        return {
            "screen": self.screen(),
            "transcript": self.transcript(),
            "titles": list(self.titles),
            "progress": list(self.progress_states),
            "cursor_hidden": self.terminal.cursor_hidden,
        }
