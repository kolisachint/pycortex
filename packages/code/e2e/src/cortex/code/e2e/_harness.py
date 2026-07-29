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

**The turn is asynchronous, and the scenario should not have to know that.** From
7.4 the app answers a submission by running an agent turn on the event loop, so
"press Enter and look at the screen" spans work no keystroke can drive on its
own. The harness therefore owns a loop and, after every input, *pumps* it: run
ready callbacks, repeatedly, until the app reports itself idle (`attach(busy=…)`)
or the pass budget runs out. Pumping never advances the clock — a turn that is
waiting on a real timer, or on a scenario-held gate, stays in flight and is
observable mid-way, which is what `chat/abort-turn` needs. `settle()` is the
explicit form for anything that must wait on wall-clock time.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine, Iterable
from typing import Any

from cortex.code.e2e._keys import resolve_key
from cortex.code.e2e._terminal import HarnessTerminal
from cortex.tui.render import TUI
from cortex.tui.testkit import Surface, snapshot

__all__ = ["AppHarness"]

# A root builder gets the live TUI and attaches whatever the app's root is.
RootBuilder = Callable[[TUI], None]

#: How many passes of ready callbacks one `pump()` will run before giving up on
#: the app going idle. A faux turn settles in a few dozen; a turn blocked on a
#: gate never will, and spending the budget on it costs microseconds.
PUMP_PASSES = 500


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
        # The app schedules work with `asyncio.get_event_loop()` and the TUI
        # coalesces frames onto a loop; give both one that this harness drives,
        # rather than whatever loop policy the test process happens to have.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._busy: Callable[[], bool] | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        #: Whatever `attach` adopted, for the rare assertion that is about the
        #: app's state rather than the screen (that a turn was aborted, say).
        self.app: Any = None
        self.tui = TUI(self.terminal)
        build_root(self.tui)
        self.tui.start()
        self.render()

    # ---- lifecycle --------------------------------------------------------

    def attach(
        self,
        *,
        app: Any = None,
        busy: Callable[[], bool] | None = None,
        run: Coroutine[Any, Any, Any] | None = None,
    ) -> None:
        """Adopt an app's background loop and its notion of "still working".

        `busy` is what `pump()` waits to go false; without it a pump is a single
        pass of ready callbacks. `run` is scheduled on the harness's loop — the
        app's own message loop, which is what turns a submission into a turn.
        """
        if app is not None:
            self.app = app
        if busy is not None:
            self._busy = busy
        if run is not None:
            self.spawn(run)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Schedule a coroutine on the harness's loop."""
        task = self.loop.create_task(coro)
        self._tasks.append(task)
        return task

    def stop(self) -> None:
        self.tui.stop()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            # Let the cancellations actually land. Closing the loop with tasks
            # still pending is what turns a tidy teardown into a page of
            # "Task was destroyed but it is pending!" on stderr.
            self.loop.run_until_complete(asyncio.gather(*self._tasks, return_exceptions=True))
        self._tasks = []
        self.loop.close()
        asyncio.set_event_loop(None)

    def __enter__(self) -> AppHarness:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ---- driving ----------------------------------------------------------

    def render(self) -> None:
        """Flush a frame now, bypassing the event-loop coalescing."""
        self.tui.render_now()

    def _pass(self) -> None:
        """Run one pass of whatever the loop already has ready."""
        self.loop.run_until_complete(asyncio.sleep(0))

    def pump(self, *, passes: int = PUMP_PASSES) -> None:
        """Let the app act on what just happened, then render.

        Bounded by ready work, not by time: a pass runs the callbacks already
        queued, so work waiting on a timer or a gate is left in flight.
        """
        self._pass()
        if self._busy is not None:
            for _ in range(passes):
                if not self._busy():
                    break
                self._pass()
        self.render()

    def settle(self, *, timeout: float = 2.0) -> None:
        """Pump until the app is idle, letting real timers fire. Then render.

        `pump()` is enough for anything driven by ready callbacks; this is for a
        turn that sleeps — a throttled provider, a retry delay.
        """
        deadline = time.monotonic() + timeout
        while self._busy is not None and self._busy() and time.monotonic() < deadline:
            self.loop.run_until_complete(asyncio.sleep(0.001))
        self.render()

    def wait_for(self, predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
        """Pump until `predicate` holds, rendering a frame between passes.

        `pump()` and `settle()` both key off the app's own "am I busy" flag, and
        some work the screen depends on is invisible to it. Autocomplete is the
        case that needs this: the editor debounces the request, runs it as a
        task and may shell out to `fd`, none of which makes the *app* busy — a
        turn is not in flight, so `settle()` returns at once and `pump()` runs a
        single pass. Waiting on the screen instead is the honest form: the
        scenario says what it expects to see and this gets it there or fails.

        Raises `TimeoutError` rather than returning quietly, so a scenario that
        would otherwise assert against a stale frame reports the wait.
        """
        deadline = time.monotonic() + timeout
        while True:
            self.render()
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"condition not met within {timeout}s\n\n{self.snapshot()}")
            self.loop.run_until_complete(asyncio.sleep(0.001))

    def _deliver(self, data: str) -> None:
        """Hand `data` to the app's input listeners **from inside the loop**.

        A real tty delivers keystrokes through a reader on the event loop, so
        every handler runs with a loop already running. Several of them care:
        the editor's autocomplete resolves `asyncio.get_running_loop()` to
        debounce and to schedule the provider call, and quietly does nothing
        when there is none. Calling `send_input` straight from the scenario
        thread would put the app in that state permanently and make `/` and `@`
        look inert here while working in the terminal.
        """

        async def deliver() -> None:
            self.terminal.send_input(data)

        self.loop.run_until_complete(deliver())

    def send(self, data: str) -> None:
        """Feed raw bytes as if the terminal produced them, then render."""
        self._deliver(data)
        self.pump()

    def type(self, text: str) -> None:
        """Type literal text. One `send` per character, as a real tty delivers."""
        for char in text:
            self._deliver(char)
        self.pump()

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
        self.pump()

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
