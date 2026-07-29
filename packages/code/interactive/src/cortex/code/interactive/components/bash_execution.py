"""A `!command` the user ran, and its output as it arrives.

Port of ``components/bash-execution.ts``. This is the *prompt mode* bash block —
the one the user starts by typing ``!ls`` — not the block for a bash tool call
the model made; that is a
:class:`~cortex.code.interactive.components.tool_execution.ToolExecutionComponent`
like any other tool.

It is a live component: :meth:`BashExecutionComponent.append_output` is called
with every chunk the shell writes, a spinner runs underneath until
:meth:`BashExecutionComponent.set_complete`, and the status line that replaces
the spinner says what happened — the exit code if it failed, "(cancelled)" if it
was interrupted, and where the full output went if it was truncated.

Truncation happens twice, for two different reasons, and the difference is the
reason the numbers do not match. The *context* truncation is the bash tool's
(``DEFAULT_MAX_LINES``/``DEFAULT_MAX_BYTES``): the same ceiling the model's copy
gets. The *preview* truncation on top of it is only about screen space —
:data:`PREVIEW_LINES` visual rows unless the block is expanded.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from cortex.code.interactive.components.keybinding_hints import key_hint
from cortex.code.interactive.components.visual_truncate import truncate_to_visual_lines
from cortex.code.interactive.theme import get_theme
from cortex.code.tools import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, TruncationOptions, truncate_tail
from cortex.tui.components import Loader, Text
from cortex.tui.render import Container

__all__ = ["PREVIEW_LINES", "BashExecutionComponent"]

#: Preview line limit when not expanded (matches the tool-execution behaviour).
PREVIEW_LINES = 20

# OSC first: its introducer (`ESC ]`) also matches the two-character escape
# alternative, and alternation is ordered.
_ANSI_ESCAPE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]"
)

BashStatus = Literal["running", "complete", "cancelled", "error"]


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


class _CachedPreview:
    """The collapsed preview, wrapped once per width.

    The TS inlines an object literal with `render`/`invalidate`; this is that
    object. Re-wrapping the tail on every frame is what makes a long-running
    command's output cost O(lines × frames).
    """

    def __init__(self, text: str) -> None:
        self._text = text
        self._cached_width: int | None = None
        self._cached_lines: list[str] | None = None

    def invalidate(self) -> None:
        self._cached_width = None
        self._cached_lines = None

    def render(self, width: int) -> list[str]:
        if self._cached_lines is None or self._cached_width != width:
            self._cached_lines = truncate_to_visual_lines(
                self._text, PREVIEW_LINES, width, 1
            ).visual_lines
            self._cached_width = width
        return self._cached_lines


class BashExecutionComponent(Container):
    """The chat-log block for one `!command`."""

    def __init__(self, command: str, ui: Any, exclude_from_context: bool = False) -> None:
        super().__init__()
        self.command = command
        self.output_lines: list[str] = []
        self.status: BashStatus = "running"
        self.exit_code: int | None = None
        self.truncation_result: Any = None
        self.full_output_path: str | None = None
        self.expanded = False

        theme = get_theme()
        # A `!!` command is excluded from the model's context; the dim colour is
        # what says so.
        color_key = "dim" if exclude_from_context else "bashMode"
        # Boxless: a status dot and the command, no borders.
        self.content_container = Container()
        self.add_child(self.content_container)

        dot = theme.fg("warning", "● ")
        self.content_container.add_child(
            Text(dot + theme.fg(color_key, theme.bold(f"$ {command}")), 1, 0)
        )

        self.loader = Loader(
            ui,
            lambda spinner: get_theme().fg(color_key, spinner),
            lambda text: get_theme().fg("muted", text),
            "",  # spinner only
        )
        self.content_container.add_child(self.loader)

    def set_expanded(self, expanded: bool) -> None:
        """Show the whole output rather than the last :data:`PREVIEW_LINES` rows."""
        self.expanded = expanded
        self.update_display()

    def invalidate(self) -> None:
        super().invalidate()
        self.update_display()

    def append_output(self, chunk: str) -> None:
        """Add a chunk of shell output.

        The first line of a chunk continues the last line already held: a shell
        writes when it writes, not on line boundaries, so treating every chunk as
        whole lines would break a progress bar into a paragraph.
        """
        clean = _strip_ansi(chunk).replace("\r\n", "\n").replace("\r", "\n")
        new_lines = clean.split("\n")
        if self.output_lines and new_lines:
            self.output_lines[-1] += new_lines[0]
            self.output_lines.extend(new_lines[1:])
        else:
            self.output_lines.extend(new_lines)
        self.update_display()

    def set_complete(
        self,
        exit_code: int | None,
        cancelled: bool,
        truncation_result: Any = None,
        full_output_path: str | None = None,
    ) -> None:
        self.exit_code = exit_code
        if cancelled:
            self.status = "cancelled"
        elif exit_code is not None and exit_code != 0:
            self.status = "error"
        else:
            self.status = "complete"
        self.truncation_result = truncation_result
        self.full_output_path = full_output_path
        self.loader.stop()
        self.update_display()

    def update_display(self) -> None:
        theme = get_theme()
        # The model's ceiling first: what is on screen is a view of what the
        # model would have been given, not of the raw stream.
        full_output = "\n".join(self.output_lines)
        context_truncation = truncate_tail(
            full_output,
            TruncationOptions(max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES),
        )
        available_lines = (
            context_truncation.content.split("\n") if context_truncation.content else []
        )

        preview_logical_lines = available_lines[-PREVIEW_LINES:]
        hidden_line_count = len(available_lines) - len(preview_logical_lines)

        self.content_container.clear()

        dot_color = (
            "error"
            if self.status == "error"
            else "warning"
            if self.status == "running"
            else "success"
        )
        dot = theme.fg(dot_color, "● ")
        self.content_container.add_child(
            Text(dot + theme.fg("bashMode", theme.bold(f"$ {self.command}")), 1, 0)
        )

        if available_lines:
            if self.expanded:
                display_text = "\n".join(theme.fg("muted", line) for line in available_lines)
                self.content_container.add_child(Text(f"\n{display_text}", 1, 0))
            else:
                styled_output = "\n".join(theme.fg("muted", line) for line in preview_logical_lines)
                self.content_container.add_child(_CachedPreview(f"\n{styled_output}"))

        if self.status == "running":
            self.content_container.add_child(self.loader)
            return

        status_parts: list[str] = []

        if hidden_line_count > 0:
            if self.expanded:
                status_parts.append(f"({key_hint('app.tools.expand', 'to collapse')})")
            else:
                status_parts.append(
                    f"{theme.fg('muted', f'... {hidden_line_count} more lines')} "
                    f"({key_hint('app.tools.expand', 'to expand')})"
                )

        if self.status == "cancelled":
            status_parts.append(theme.fg("warning", "(cancelled)"))
        elif self.status == "error":
            status_parts.append(theme.fg("error", f"(exit {self.exit_code})"))

        # The *context* truncation, not the preview one: this line is about what
        # did not fit in the transcript, and points at where the rest was saved.
        was_truncated = (
            getattr(self.truncation_result, "truncated", False) or context_truncation.truncated
        )
        if was_truncated and self.full_output_path:
            status_parts.append(
                theme.fg("warning", f"Output truncated. Full output: {self.full_output_path}")
            )

        if status_parts:
            self.content_container.add_child(Text("\n" + "\n".join(status_parts), 1, 0))

    def get_output(self) -> str:
        """The raw output, for recording the command in the session."""
        return "\n".join(self.output_lines)

    def get_command(self) -> str:
        return self.command
