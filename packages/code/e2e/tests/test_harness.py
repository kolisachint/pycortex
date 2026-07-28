"""The harness must be trustworthy before the corpus it runs means anything."""

from __future__ import annotations

import pytest
from cortex.code.e2e import AppHarness, HarnessTerminal
from cortex.tui.components import Text
from cortex.tui.render import TUI


def _label(text: str):
    def build(tui: TUI) -> None:
        tui.add_child(Text(text))

    return build


def test_boot_renders_without_an_event_loop() -> None:
    """`request_render` coalesces onto the loop; the harness must not need one."""
    with AppHarness(_label("hello")) as h:
        assert h.contains("hello")


def test_screen_is_the_terminal_size() -> None:
    with AppHarness(_label("x"), columns=40, rows=10) as h:
        surface = h.surface()
        assert (surface.cols, surface.rows) == (40, 10)


def test_input_reaches_listeners_byte_for_byte() -> None:
    seen: list[str] = []
    with AppHarness(_label("x")) as h:
        h.tui.add_input_listener(lambda data: seen.append(data) or None)
        h.type("ab")
        h.key("ctrl+c")
    assert seen == ["a", "b", "\x03"]


def test_contains_is_line_scoped_not_whole_text() -> None:
    """A match straddling a line break is a joining artefact, not something read."""

    def build(tui: TUI) -> None:
        tui.add_child(Text("foo"))
        tui.add_child(Text("bar"))

    with AppHarness(build) as h:
        assert h.contains("foo")
        assert h.contains("bar")
        assert not h.contains("foobar")


def test_assert_shows_reports_the_screen_on_failure() -> None:
    with AppHarness(_label("present")) as h:
        with pytest.raises(AssertionError) as excinfo:
            h.assert_shows("absent")
    message = str(excinfo.value)
    assert "'absent'" in message
    # The failure must carry the picture, not just the claim.
    assert "present" in message and "┌" in message


def test_resize_adopts_the_new_width() -> None:
    with AppHarness(_label("resize me"), columns=60, rows=12) as h:
        h.resize(30, 8)
        assert (h.surface().cols, h.surface().rows) == (30, 8)
        assert h.contains("resize me")


def test_stop_restores_the_cursor_and_stops_the_terminal() -> None:
    h = AppHarness(_label("bye"))
    assert h.terminal.cursor_hidden is True
    h.stop()
    assert h.terminal.cursor_hidden is False
    assert h.terminal.stopped is True


def test_terminal_records_app_level_effects() -> None:
    term = HarnessTerminal()
    term.set_title("pycortex")
    term.set_progress(True)
    term.set_progress(False)
    term.drain_input()
    assert term.titles == ["pycortex"]
    assert term.progress_states == [True, False]
    assert term.drained == 1


def test_cursor_motion_goes_through_the_write_stream() -> None:
    """Motion must land in the stream the Surface interprets, or the grid drifts."""
    term = HarnessTerminal()
    term.move_by(-2)
    term.move_by(3)
    assert term.stream == "\x1b[2A\x1b[3B"


# ---------------------------------------------------------------------------
# The event loop (step 7.4)
# ---------------------------------------------------------------------------


def test_the_harness_owns_a_loop_app_code_can_find() -> None:
    """The app schedules with `get_event_loop()`; it must land on this loop."""
    import asyncio

    with AppHarness(_label("x")) as h:
        assert asyncio.get_event_loop() is h.loop


def test_pump_runs_work_the_input_kicked_off() -> None:
    import asyncio

    ran: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0)
        ran.append("done")

    with AppHarness(_label("x")) as h:
        h.spawn(work())
        assert ran == [], "the task ran before the loop was pumped"
        h.pump()
        assert ran == ["done"]


def test_pump_waits_for_busy_to_clear() -> None:
    import asyncio

    state = {"busy": True}

    async def work() -> None:
        for _ in range(20):
            await asyncio.sleep(0)
        state["busy"] = False

    with AppHarness(_label("x")) as h:
        h.attach(busy=lambda: state["busy"], run=work())
        h.pump()
        assert state["busy"] is False, "pump gave up while the app was still working"


def test_pump_gives_up_on_work_that_never_finishes() -> None:
    """A turn held open must stay open — that is how an abort is observable."""
    import asyncio

    gate = asyncio.Event()
    state = {"busy": True}

    async def work() -> None:
        await gate.wait()
        state["busy"] = False

    with AppHarness(_label("x")) as h:
        h.attach(busy=lambda: state["busy"], run=work())
        h.pump(passes=10)
        assert state["busy"] is True
        gate.set()
        h.pump()
        assert state["busy"] is False


def test_settle_lets_real_timers_fire() -> None:
    """`pump` never advances the clock; `settle` is the form that waits."""
    import asyncio

    state = {"busy": True}

    async def work() -> None:
        await asyncio.sleep(0.01)
        state["busy"] = False

    with AppHarness(_label("x")) as h:
        h.attach(busy=lambda: state["busy"], run=work())
        h.pump(passes=5)
        assert state["busy"] is True, "a sleeping task finished without the clock moving"
        h.settle()
        assert state["busy"] is False


def test_attach_records_the_app() -> None:
    marker = object()
    with AppHarness(_label("x")) as h:
        assert h.app is None
        h.attach(app=marker)
        assert h.app is marker
