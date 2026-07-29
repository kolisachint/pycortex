"""A horizontal rule that re-measures itself. Port of ``dynamic-border.ts``.

The width arrives at render time rather than at construction, which is the whole
point: ``/changelog`` and ``/hotkeys`` frame their output between two of these,
and a rule sized once would be the wrong length the moment the terminal is
resized.
"""

from __future__ import annotations

from collections.abc import Callable

from cortex.code.interactive.theme import get_theme

__all__ = ["DynamicBorder"]


class DynamicBorder:
    """A single line of ``─``, as wide as the viewport.

    The colour is a parameter for the reason the TS gives: an extension loaded
    into its own module cache may not see the global theme, so anything exported
    for extension use passes an explicit colour function. Resolved per render
    here rather than captured at construction, so a theme change repaints.
    """

    def __init__(self, color: Callable[[str], str] | None = None) -> None:
        self._color = color

    def invalidate(self) -> None:
        """Nothing is cached, so there is nothing to drop."""

    def render(self, width: int) -> list[str]:
        color = self._color if self._color is not None else _border_color
        return [color("─" * max(1, width))]


def _border_color(text: str) -> str:
    return get_theme().fg("border", text)
