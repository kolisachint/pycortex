"""Truncate text to a number of *visual* lines.

Port of ``components/visual-truncate.ts``. "Show the last twenty lines" is a
statement about what the user sees, and a logical line wider than the terminal
occupies several rows — so the count has to be taken after wrapping, which means
rendering. A throwaway :class:`~cortex.tui.components.Text` is the renderer, as
in the TS: it is the component that owns the wrapping rules, and reimplementing
them here is how the preview and the real output start disagreeing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cortex.tui.components import Text

__all__ = ["VisualTruncateResult", "truncate_to_visual_lines"]


@dataclass
class VisualTruncateResult:
    #: The visual lines to display.
    visual_lines: list[str] = field(default_factory=list)
    #: How many visual lines were dropped off the top.
    skipped_count: int = 0


def truncate_to_visual_lines(
    text: str,
    max_visual_lines: int,
    width: int,
    padding_x: int = 0,
) -> VisualTruncateResult:
    """Keep the last *max_visual_lines* rows of *text* rendered at *width*.

    ``padding_x`` is the padding the caller will place the result under: 0 when
    it goes inside a :class:`~cortex.tui.components.Box` (which adds its own), 1
    when it goes into a plain :class:`~cortex.tui.render.Container`.
    """
    if not text:
        return VisualTruncateResult()

    all_visual_lines = Text(text, padding_x, 0).render(width)

    if len(all_visual_lines) <= max_visual_lines:
        return VisualTruncateResult(visual_lines=all_visual_lines, skipped_count=0)

    return VisualTruncateResult(
        visual_lines=all_visual_lines[-max_visual_lines:],
        skipped_count=len(all_visual_lines) - max_visual_lines,
    )
