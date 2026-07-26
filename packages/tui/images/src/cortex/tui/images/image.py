"""The ``Image`` component.

Mechanical port of hoocode's ``packages/tui/src/components/image.ts`` (112
lines). It lives in the images leaf rather than the components one because it is
nothing but a `Component`-shaped wrapper around ``terminal_image``: keeping it
here leaves this leaf dependency-free, which is what lets `cortex.tui.render`
depend on it without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cortex.tui.images.terminal_image import (
    ImageDimensions,
    ImageRenderOptions,
    allocate_image_id,
    get_capabilities,
    get_image_dimensions,
    image_fallback,
    render_image,
)

__all__ = ["Image", "ImageOptions", "ImageTheme"]


@dataclass
class ImageTheme:
    fallback_color: Callable[[str], str]


@dataclass
class ImageOptions:
    max_width_cells: int | None = None
    max_height_cells: int | None = None
    filename: str | None = None
    #: Kitty image ID. If provided, reuses this ID (for animations/updates).
    image_id: int | None = None


class Image:
    def __init__(
        self,
        base64_data: str,
        mime_type: str,
        theme: ImageTheme,
        options: ImageOptions | None = None,
        dimensions: ImageDimensions | None = None,
    ) -> None:
        self._base64_data = base64_data
        self._mime_type = mime_type
        self._theme = theme
        self._options = options if options is not None else ImageOptions()
        self._dimensions = (
            dimensions
            or get_image_dimensions(base64_data, mime_type)
            or ImageDimensions(width_px=800, height_px=600)
        )
        self._image_id = self._options.image_id

        self._cached_lines: list[str] | None = None
        self._cached_width: int | None = None

    def get_image_id(self) -> int | None:
        """Get the Kitty image ID used by this image (if any)."""
        return self._image_id

    def invalidate(self) -> None:
        self._cached_lines = None
        self._cached_width = None

    def render(self, width: int) -> list[str]:
        # `if (this.cachedLines && ...)` in the TS, where an empty array is
        # truthy — the Python spelling of that is an explicit None check.
        if self._cached_lines is not None and self._cached_width == width:
            return self._cached_lines

        max_width = min(
            width - 2,
            self._options.max_width_cells if self._options.max_width_cells is not None else 60,
        )

        caps = get_capabilities()
        lines: list[str]

        if caps.images:
            if caps.images == "kitty" and self._image_id is None:
                self._image_id = allocate_image_id()
            result = render_image(
                self._base64_data,
                self._dimensions,
                ImageRenderOptions(
                    max_width_cells=max_width,
                    image_id=self._image_id,
                    move_cursor=False,
                ),
            )

            if result:
                # Store the image ID for later cleanup
                if result.image_id:
                    self._image_id = result.image_id

                # Return `rows` lines so TUI accounts for image height.
                # First (rows-1) lines are empty and cleared before the image is drawn.
                # Last line: move cursor back up, draw the image, then move back down
                # for Kitty (this component disables Kitty's terminal-side cursor movement)
                # so TUI cursor accounting stays inside the scroll area.
                lines = []
                for _ in range(result.rows - 1):
                    lines.append("")
                row_offset = result.rows - 1
                move_up = f"\x1b[{row_offset}A" if row_offset > 0 else ""
                move_down = (
                    f"\x1b[{row_offset}B" if caps.images == "kitty" and row_offset > 0 else ""
                )
                lines.append(move_up + result.sequence + move_down)
            else:
                fallback = image_fallback(self._mime_type, self._dimensions, self._options.filename)
                lines = [self._theme.fallback_color(fallback)]
        else:
            fallback = image_fallback(self._mime_type, self._dimensions, self._options.filename)
            lines = [self._theme.fallback_color(fallback)]

        self._cached_lines = lines
        self._cached_width = width

        return lines
