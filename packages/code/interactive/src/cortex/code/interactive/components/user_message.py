"""User message component that renders user input in the chat.

Port of ``components/user-message.ts`` — a ``Container`` that wraps a
``Markdown`` component inside a styled box, and wraps the output in
OSC 133 zones for shell integration (prompt start/end markers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex.tui.components import Box, DefaultTextStyle, Markdown
from cortex.tui.render import Container

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cortex.tui.components import MarkdownTheme

from cortex.code.interactive.theme import get_markdown_theme, get_theme

__all__ = ["UserMessageComponent"]

# OSC 133 zone markers for shell integration
OSC133_ZONE_START = "\x1b]133;A\x07"
OSC133_ZONE_END = "\x1b]133;B\x07"
OSC133_ZONE_FINAL = "\x1b]133;C\x07"


class UserMessageComponent(Container):
    """Component that renders a user message.

    The message is wrapped in a ``Box`` with a background colour, and the
    rendered lines are wrapped in OSC 133 zone markers for shell integration.
    """

    def __init__(
        self,
        text: str,
        markdown_theme: MarkdownTheme | None = None,
    ) -> None:
        super().__init__()
        if markdown_theme is None:
            markdown_theme = get_markdown_theme()

        theme = get_theme()
        content_box = Box(
            1,
            1,
            lambda content: theme.bg("userMessageBg", content),
        )
        content_box.add_child(
            Markdown(
                text,
                0,
                0,
                markdown_theme,
                default_text_style=DefaultTextStyle(
                    color=lambda content: theme.fg("userMessageText", content),
                ),
            ),
        )
        self.add_child(content_box)

        # OSC-zone wrap memo: Container.render returns the same array across
        # frames when nothing changed, so it must not be mutated — the wrapped
        # copy is cached keyed on the source array's identity.
        self.zone_src: Sequence[str] | None = None
        self.zone_out: list[str] | None = None

    def render(self, width: int) -> list[str]:
        lines = super().render(width)
        if not lines:
            return lines

        # Return cached result if the source array hasn't changed
        if self.zone_src is lines and self.zone_out is not None:
            return self.zone_out

        out = list(lines)
        out[0] = OSC133_ZONE_START + out[0]
        out[-1] = OSC133_ZONE_END + OSC133_ZONE_FINAL + out[-1]
        self.zone_src = lines
        self.zone_out = out
        return out
