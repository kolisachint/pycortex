"""Box component - a container that applies padding and background to all children."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cortex.tui.render import Component
from cortex.tui.util import apply_background_to_line, visible_width


@dataclass
class RenderCache:
    """Cache for rendered output."""

    child_lines: list[str]
    width: int
    bg_sample: str | None
    lines: list[str]


class Box:
    """Box component - a container that applies padding and background to all children."""

    def __init__(
        self,
        padding_x: int = 1,
        padding_y: int = 1,
        bg_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.children: list[Component] = []
        self._padding_x = padding_x
        self._padding_y = padding_y
        self._bg_fn = bg_fn
        self._cache: RenderCache | None = None

    def add_child(self, component: Component) -> None:
        """Add a child component."""
        self.children.append(component)
        self._invalidate_cache()

    def remove_child(self, component: Component) -> None:
        """Remove a child component."""
        try:
            self.children.remove(component)
            self._invalidate_cache()
        except ValueError:
            pass

    def clear(self) -> None:
        """Remove all children."""
        self.children = []
        self._invalidate_cache()

    def set_bg_fn(self, bg_fn: Callable[[str], str] | None) -> None:
        """Set the background function."""
        self._bg_fn = bg_fn
        # Don't invalidate here - we'll detect bg_fn changes by sampling output

    def _invalidate_cache(self) -> None:
        """Invalidate the render cache."""
        self._cache = None

    def _match_cache(
        self,
        width: int,
        child_lines: list[str],
        bg_sample: str | None,
    ) -> bool:
        """Check if cache is valid."""
        cache = self._cache
        return (
            cache is not None
            and cache.width == width
            and cache.bg_sample == bg_sample
            and len(cache.child_lines) == len(child_lines)
            and all(a == b for a, b in zip(cache.child_lines, child_lines, strict=True))
        )

    def invalidate(self) -> None:
        """Invalidate cached rendering state."""
        self._invalidate_cache()
        for child in self.children:
            child.invalidate()

    def render(self, width: int) -> list[str]:
        """Render box with children."""
        if not self.children:
            return []

        content_width = max(1, width - self._padding_x * 2)
        left_pad = " " * self._padding_x

        # Render all children
        child_lines: list[str] = []
        for child in self.children:
            lines = child.render(content_width)
            for line in lines:
                child_lines.append(left_pad + line)

        if not child_lines:
            return []

        # Check if bg_fn output changed by sampling
        bg_sample = self._bg_fn("test") if self._bg_fn else None

        # Check cache validity
        if self._match_cache(width, child_lines, bg_sample):
            assert self._cache is not None
            return self._cache.lines

        # Apply background and padding
        result: list[str] = []

        # Top padding
        for _ in range(self._padding_y):
            result.append(self._apply_bg("", width))

        # Content
        for line in child_lines:
            result.append(self._apply_bg(line, width))

        # Bottom padding
        for _ in range(self._padding_y):
            result.append(self._apply_bg("", width))

        # Update cache
        self._cache = RenderCache(
            child_lines=child_lines,
            width=width,
            bg_sample=bg_sample,
            lines=result,
        )

        return result

    def _apply_bg(self, line: str, width: int) -> str:
        """Apply background to a line."""
        vis_len = visible_width(line)
        pad_needed = max(0, width - vis_len)
        padded = line + " " * pad_needed

        if self._bg_fn:
            return apply_background_to_line(padded, width, self._bg_fn)
        return padded
