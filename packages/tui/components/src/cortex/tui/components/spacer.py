"""Spacer component that renders empty lines."""

from __future__ import annotations


class Spacer:
    """Spacer component that renders empty lines."""

    def __init__(self, lines: int = 1) -> None:
        self._lines = lines
        self._cached: list[str] | None = None

    def set_lines(self, lines: int) -> None:
        """Set the number of lines to render."""
        self._lines = lines
        self._cached = None

    def invalidate(self) -> None:
        """Invalidate cached rendering state."""
        # Output depends only on `lines`; keep the cache.

    def render(self, width: int) -> list[str]:
        """Render spacer as empty lines."""
        if self._cached is None or len(self._cached) != self._lines:
            self._cached = [""] * self._lines
        return self._cached
