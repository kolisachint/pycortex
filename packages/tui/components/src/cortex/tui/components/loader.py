"""Loader component with an optional spinner animation.

Mechanical port of hoocode's ``packages/tui/src/components/loader.ts``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from cortex.tui.components.text import Text

__all__ = ["DEFAULT_FRAMES", "DEFAULT_INTERVAL_MS", "Loader", "LoaderIndicatorOptions"]

DEFAULT_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
# Each frame requests a full TUI re-render, so the cadence is a direct tax on
# long transcripts; 120ms stays visually smooth at two-thirds the render load.
DEFAULT_INTERVAL_MS = 120


@dataclass
class LoaderIndicatorOptions:
    """Animation configuration. An empty ``frames`` list hides the indicator."""

    frames: Sequence[str] | None = None
    #: Frame interval in milliseconds for animated indicators.
    interval_ms: int | None = None


class Loader(Text):
    """A message with an optional spinner, re-rendering on each frame."""

    def __init__(
        self,
        ui: Any,
        spinner_color_fn: Callable[[str], str],
        message_color_fn: Callable[[str], str],
        message: str = "Loading...",
        indicator: LoaderIndicatorOptions | None = None,
    ) -> None:
        super().__init__("", 1, 0)
        self._ui = ui
        self._spinner_color_fn = spinner_color_fn
        self._message_color_fn = message_color_fn
        self._message = message
        self._frames = list(DEFAULT_FRAMES)
        self._interval_ms = DEFAULT_INTERVAL_MS
        self._current_frame = 0
        self._timer: asyncio.TimerHandle | None = None
        # Bumped by stop()/reschedule so an already-queued tick from a previous
        # animation cannot resurrect itself and run two spinners at once.
        self._generation = 0
        self._render_indicator_verbatim = False
        self.set_indicator(indicator)

    def render(self, width: int) -> list[str]:
        return ["", *super().render(width)]

    def start(self) -> None:
        self._update_display()
        self._restart_animation()

    def stop(self) -> None:
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def set_message(self, message: str) -> None:
        self._message = message
        self._update_display()

    def set_indicator(self, indicator: LoaderIndicatorOptions | None = None) -> None:
        # A caller-supplied indicator is rendered as given; the default spinner
        # goes through the colour function.
        self._render_indicator_verbatim = indicator is not None
        frames = indicator.frames if indicator is not None else None
        self._frames = list(frames) if frames is not None else list(DEFAULT_FRAMES)
        interval = indicator.interval_ms if indicator is not None else None
        self._interval_ms = (
            interval if interval is not None and interval > 0 else DEFAULT_INTERVAL_MS
        )
        self._current_frame = 0
        self.start()

    def _restart_animation(self) -> None:
        self.stop()
        if len(self._frames) <= 1:
            return
        self._schedule_tick()

    def _schedule_tick(self) -> None:
        """The TS `setInterval`. Without a running loop there is no animation.

        A spinner is only meaningful inside an event loop; the frame still
        renders, it just does not advance.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        generation = self._generation

        def tick() -> None:
            if generation != self._generation:
                return
            self._current_frame = (self._current_frame + 1) % len(self._frames)
            self._update_display()
            if generation == self._generation:
                self._schedule_tick()

        self._timer = loop.call_later(self._interval_ms / 1000, tick)

    def _update_display(self) -> None:
        frame = self._frames[self._current_frame] if self._frames else ""
        rendered_frame = frame if self._render_indicator_verbatim else self._spinner_color_fn(frame)
        indicator = f"{rendered_frame} " if len(frame) > 0 else ""
        self.set_text(f"{indicator}{self._message_color_fn(self._message)}")
        if self._ui is not None:
            self._ui.request_render()
