# pyright: reportPrivateUsage=false
# The animation state (_frames, _interval_ms, _current_frame, _timer) has no
# public surface by design; asserting on it is the point of this file.
"""Loader behaviour a rendered frame cannot show.

The frames themselves — indicator glyph, colour wrapping, wrapping of long
messages — are pinned by the `component/loader-*` scenarios in
`packages/tui/testkit`, which diff whole screens against the real TypeScript.
What is left is the animation timer, the abort signal, and the mutators.
"""

from __future__ import annotations

import asyncio

from cortex.tui.components import (
    CancellableLoader,
    Loader,
    LoaderIndicatorOptions,
)
from cortex.tui.components.loader import DEFAULT_FRAMES, DEFAULT_INTERVAL_MS


class RenderCounter:
    """Stands in for the TUI; the loader only ever calls `request_render`."""

    def __init__(self) -> None:
        self.requests = 0

    def request_render(self, force: bool = False) -> None:
        self.requests += 1


def make_loader(**kwargs: object) -> tuple[Loader, RenderCounter]:
    ui = RenderCounter()
    loader = Loader(
        ui,
        lambda text: f"<s>{text}</s>",
        lambda text: f"<m>{text}</m>",
        kwargs.get("message", "Loading..."),  # pyright: ignore[reportArgumentType]
        kwargs.get("indicator"),  # pyright: ignore[reportArgumentType]
    )
    return loader, ui


class TestRendering:
    def test_leading_blank_line_precedes_the_text(self) -> None:
        loader, _ = make_loader()
        loader.stop()
        lines = loader.render(30)
        assert lines[0] == ""
        assert len(lines) == 2

    def test_default_indicator_is_colour_wrapped(self) -> None:
        loader, _ = make_loader()
        loader.stop()
        assert f"<s>{DEFAULT_FRAMES[0]}</s>" in loader.render(40)[1]
        assert "<m>Loading...</m>" in loader.render(40)[1]

    def test_an_explicit_indicator_is_rendered_verbatim(self) -> None:
        # Supplying an indicator opts out of the spinner colour function.
        loader, _ = make_loader(indicator=LoaderIndicatorOptions(frames=["*"]))
        loader.stop()
        line = loader.render(40)[1]
        assert " * " in line
        assert "<s>" not in line

    def test_no_frames_means_no_indicator_and_no_space(self) -> None:
        loader, _ = make_loader(indicator=LoaderIndicatorOptions(frames=[]))
        loader.stop()
        assert loader.render(40)[1].startswith(" <m>")

    def test_set_message_updates_the_frame(self) -> None:
        loader, ui = make_loader()
        loader.stop()
        before = ui.requests
        loader.set_message("Almost done")
        assert "<m>Almost done</m>" in loader.render(40)[1]
        assert ui.requests == before + 1, "a message change must request a frame"


class TestIndicatorOptions:
    def test_defaults_when_no_indicator_is_given(self) -> None:
        loader, _ = make_loader()
        loader.stop()
        assert loader._frames == DEFAULT_FRAMES
        assert loader._interval_ms == DEFAULT_INTERVAL_MS

    def test_a_non_positive_interval_falls_back_to_the_default(self) -> None:
        for interval in (0, -5):
            loader, _ = make_loader(
                indicator=LoaderIndicatorOptions(frames=["a", "b"], interval_ms=interval)
            )
            loader.stop()
            assert loader._interval_ms == DEFAULT_INTERVAL_MS

    def test_frames_are_copied_not_aliased(self) -> None:
        frames = ["a", "b"]
        loader, _ = make_loader(indicator=LoaderIndicatorOptions(frames=frames))
        loader.stop()
        frames.append("c")
        assert loader._frames == ["a", "b"]

    def test_changing_the_indicator_resets_to_the_first_frame(self) -> None:
        loader, _ = make_loader()
        loader.stop()
        loader._current_frame = 4
        loader.set_indicator(LoaderIndicatorOptions(frames=["x", "y"]))
        loader.stop()
        assert loader._current_frame == 0


class TestAnimation:
    async def test_frames_advance_and_wrap(self) -> None:
        loader, ui = make_loader(
            indicator=LoaderIndicatorOptions(frames=["1", "2", "3"], interval_ms=10)
        )
        try:
            # Sample rather than checking the index at one instant: with three
            # frames on a 10ms tick, "advanced" and "back at 0" look identical.
            seen: set[int] = set()
            for _ in range(12):
                seen.add(loader._current_frame)
                await asyncio.sleep(0.008)
            assert seen == {0, 1, 2}, f"expected the full cycle, saw {sorted(seen)}"
            assert ui.requests > 1, "each frame requests a render"
        finally:
            loader.stop()

    async def test_a_single_frame_indicator_does_not_animate(self) -> None:
        loader, ui = make_loader(indicator=LoaderIndicatorOptions(frames=["."], interval_ms=10))
        try:
            requests_after_setup = ui.requests
            await asyncio.sleep(0.05)
            assert ui.requests == requests_after_setup, "a static indicator must not tick"
        finally:
            loader.stop()

    async def test_stop_halts_the_animation(self) -> None:
        loader, ui = make_loader(
            indicator=LoaderIndicatorOptions(frames=["1", "2"], interval_ms=10)
        )
        await asyncio.sleep(0.025)
        loader.stop()
        settled = ui.requests
        await asyncio.sleep(0.05)
        assert ui.requests == settled

    async def test_restarting_does_not_leave_two_timers_running(self) -> None:
        loader, ui = make_loader(
            indicator=LoaderIndicatorOptions(frames=["1", "2"], interval_ms=10)
        )
        try:
            loader.start()
            loader.start()
            await asyncio.sleep(0.055)
            # ~5 ticks at 10ms, plus the three start()/set_indicator refreshes.
            # Two live timers would roughly double it.
            assert ui.requests < 12, f"looks like overlapping timers: {ui.requests} renders"
        finally:
            loader.stop()

    def test_no_event_loop_means_no_animation_but_still_renders(self) -> None:
        # A spinner is only meaningful inside a loop; the frame must still draw.
        loader, _ = make_loader(indicator=LoaderIndicatorOptions(frames=["1", "2"], interval_ms=10))
        assert loader._timer is None
        assert loader.render(20)[1].strip() != ""


class TestCancellableLoader:
    def test_starts_unaborted(self) -> None:
        loader = CancellableLoader(None, str, str, "Working")
        loader.stop()
        assert loader.aborted is False
        assert loader.signal.aborted is False

    def test_escape_aborts_and_fires_the_callback(self) -> None:
        loader = CancellableLoader(None, str, str, "Working")
        loader.stop()
        calls: list[int] = []
        loader.on_abort = lambda: calls.append(1)

        loader.handle_input("\x1b")

        assert loader.aborted is True
        assert loader.signal.aborted is True
        assert calls == [1]

    def test_ctrl_c_also_cancels(self) -> None:
        loader = CancellableLoader(None, str, str, "Working")
        loader.stop()
        loader.handle_input("\x03")
        assert loader.aborted is True

    def test_other_keys_do_nothing(self) -> None:
        loader = CancellableLoader(None, str, str, "Working")
        loader.stop()
        loader.handle_input("a")
        assert loader.aborted is False

    def test_abort_without_a_callback_is_safe(self) -> None:
        loader = CancellableLoader(None, str, str, "Working")
        loader.stop()
        loader.handle_input("\x1b")
        assert loader.aborted is True

    async def test_dispose_stops_the_animation(self) -> None:
        ui = RenderCounter()
        loader = CancellableLoader(
            ui, str, str, "Working", LoaderIndicatorOptions(frames=["1", "2"], interval_ms=10)
        )
        await asyncio.sleep(0.025)
        loader.dispose()
        settled = ui.requests
        await asyncio.sleep(0.05)
        assert ui.requests == settled
