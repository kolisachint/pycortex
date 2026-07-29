"""Slash-command handlers, off the app class. Port of ``command-executor.ts``.

The TS extracted these from ``InteractiveMode`` so a handler reaches its
dependencies through a :class:`CommandContext` rather than through ``this``;
this port keeps that shape, and for the same payoff — a handler is testable
against a context built by hand, with no TUI, no terminal and no session file.

**Six of the thirteen handlers are here, and the other seven are not stranded —
they are waiting on machinery that belongs to a later step.** ``/model`` and its
selector need the model registry (7.11); ``/new``, ``/clone`` and ``/import``
need ``AgentSessionRuntime``'s session-replacement half, which
:mod:`cortex.code.session.runtime` defers to 7.10 and which is also what
``renderCurrentSessionState`` rebuilds from; ``/subagent`` needs the subagent
pool and the agent registry; ``/share`` shells out to ``gh``; and ``/copy``
needs ``utils/clipboard.ts``, whose payload is a native addon plus Wayland/X11
tool probing rather than a clipboard call. Adding a handler that answers
"not available" would put a dead entry in the `/` menu, so instead
:mod:`cortex.code.interactive.interactive_mode` advertises exactly the commands
it dispatches. The context's shape is the TS's regardless: the callbacks a
missing handler would use are the ones missing from it.

``CommandContext`` is a protocol rather than a dataclass because the TS builds
it out of *getters* — ``get session() { return self.session }`` — so a handler
always sees the live session rather than the one that existed when the executor
was constructed. A dataclass of values would snapshot them; the app satisfies
the protocol with properties and gets the TS's behaviour for free.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any, Protocol

from cortex.code.config import get_changelog_path, get_debug_log_path
from cortex.code.interactive.changelog import parse_changelog
from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.keybinding_hints import (
    key_display_text,
)
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Markdown, MarkdownTheme, Spacer, Text
from cortex.tui.render import TUI, Container
from cortex.tui.util import visible_width

__all__ = ["CommandContext", "CommandExecutor"]


class CommandContext(Protocol):
    """What a handler is allowed to reach for.

    Every member is read on use, never captured: see the module docstring.
    """

    @property
    def session(self) -> Any: ...

    @property
    def session_manager(self) -> Any: ...

    @property
    def ui(self) -> TUI: ...

    @property
    def chat_container(self) -> Container: ...

    @property
    def keybindings(self) -> Any: ...

    # Positional-only: the app names these parameters after what they carry
    # (`warning_message`), and a protocol that pinned a name would reject it.
    def show_status(self, message: str, /) -> None: ...

    def show_warning(self, message: str, /) -> None: ...

    def get_markdown_theme_with_settings(self) -> MarkdownTheme: ...


class CommandExecutor:
    """The handlers behind the built-in slash commands."""

    def __init__(self, ctx: CommandContext) -> None:
        self._ctx = ctx

    # =========================================================================
    # Slash command handlers
    # =========================================================================

    def handle_name(self, text: str) -> None:
        """``/name`` — set the session's display name, or print the current one.

        The TS writes both answers straight into the chat container rather than
        through ``showStatus``, which is why this does too: they are transcript
        lines, not a status blip.
        """
        name = text[len("/name") :].strip() if text.startswith("/name") else text.strip()
        if not name:
            current_name = self._ctx.session_manager.get_session_name()
            if current_name:
                self._ctx.chat_container.add_child(Spacer(1))
                self._ctx.chat_container.add_child(
                    Text(get_theme().fg("dim", f"Session name: {current_name}"), 1, 0)
                )
            else:
                self._ctx.show_warning("Usage: /name <name>")
            self._ctx.ui.request_render()
            return

        self._ctx.session.set_session_name(name)
        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(
            Text(get_theme().fg("dim", f"Session name set: {name}"), 1, 0)
        )
        self._ctx.ui.request_render()

    def handle_session(self) -> None:
        """``/session`` — counts, tokens and cost for the session so far."""
        theme = get_theme()
        stats = self._ctx.session.get_session_stats()
        session_name = self._ctx.session_manager.get_session_name()

        info = f"{theme.bold('Session Info')}\n\n"
        if session_name:
            info += f"{theme.fg('dim', 'Name:')} {session_name}\n"
        info += f"{theme.fg('dim', 'File:')} {stats.session_file or 'In-memory'}\n"
        info += f"{theme.fg('dim', 'ID:')} {stats.session_id}\n\n"
        info += f"{theme.bold('Messages')}\n"
        info += f"{theme.fg('dim', 'User:')} {stats.user_messages}\n"
        info += f"{theme.fg('dim', 'Assistant:')} {stats.assistant_messages}\n"
        info += f"{theme.fg('dim', 'Tool Calls:')} {stats.tool_calls}\n"
        info += f"{theme.fg('dim', 'Tool Results:')} {stats.tool_results}\n"
        info += f"{theme.fg('dim', 'Total:')} {stats.total_messages}\n\n"
        info += f"{theme.bold('Tokens')}\n"
        info += f"{theme.fg('dim', 'Input:')} {_locale_string(stats.tokens.input)}\n"
        info += f"{theme.fg('dim', 'Output:')} {_locale_string(stats.tokens.output)}\n"
        if stats.tokens.cache_read > 0:
            info += f"{theme.fg('dim', 'Cache Read:')} {_locale_string(stats.tokens.cache_read)}\n"
        if stats.tokens.cache_write > 0:
            info += (
                f"{theme.fg('dim', 'Cache Write:')} {_locale_string(stats.tokens.cache_write)}\n"
            )
        info += f"{theme.fg('dim', 'Total:')} {_locale_string(stats.tokens.total)}\n"

        if stats.cost > 0:
            info += f"\n{theme.bold('Cost')}\n"
            info += f"{theme.fg('dim', 'Total:')} {stats.cost:.4f}"

        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(Text(info, 1, 0))
        self._ctx.ui.request_render()

    def handle_changelog(self) -> None:
        """``/changelog`` — every entry, newest first, inside a pair of rules."""
        theme = get_theme()
        all_entries = parse_changelog(get_changelog_path())

        if not all_entries:
            self._ctx.chat_container.add_child(Spacer(1))
            self._ctx.chat_container.add_child(
                Text(theme.fg("dim", "No changelog entries found."), 1, 0)
            )
            self._ctx.ui.request_render()
            return

        changelog_markdown = "\n\n".join(entry.content for entry in reversed(all_entries))

        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(DynamicBorder())
        self._ctx.chat_container.add_child(Text(theme.bold(theme.fg("accent", "What's New")), 1, 0))
        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(
            Markdown(changelog_markdown, 1, 1, self._ctx.get_markdown_theme_with_settings())
        )
        self._ctx.chat_container.add_child(DynamicBorder())
        self._ctx.ui.request_render()

    def handle_hotkeys(self) -> None:
        """``/hotkeys`` — the keyboard reference card, as a markdown table.

        Every key is read back through :func:`key_display_text` rather than
        written out, so a user who rebound ``tui.input.submit`` sees what they
        bound. The TS appends a table of extension-registered shortcuts after
        this; the extension runner is not ported, so there is nothing to append
        and the section is absent rather than empty.
        """
        theme = get_theme()

        # Navigation keybindings
        cursor_up = key_display_text("tui.editor.cursorUp")
        cursor_down = key_display_text("tui.editor.cursorDown")
        cursor_left = key_display_text("tui.editor.cursorLeft")
        cursor_right = key_display_text("tui.editor.cursorRight")
        cursor_word_left = key_display_text("tui.editor.cursorWordLeft")
        cursor_word_right = key_display_text("tui.editor.cursorWordRight")
        cursor_line_start = key_display_text("tui.editor.cursorLineStart")
        cursor_line_end = key_display_text("tui.editor.cursorLineEnd")
        jump_forward = key_display_text("tui.editor.jumpForward")
        jump_backward = key_display_text("tui.editor.jumpBackward")
        page_up = key_display_text("tui.editor.pageUp")
        page_down = key_display_text("tui.editor.pageDown")

        # Editing keybindings
        submit = key_display_text("tui.input.submit")
        new_line = key_display_text("tui.input.newLine")
        delete_word_backward = key_display_text("tui.editor.deleteWordBackward")
        delete_word_forward = key_display_text("tui.editor.deleteWordForward")
        delete_to_line_start = key_display_text("tui.editor.deleteToLineStart")
        delete_to_line_end = key_display_text("tui.editor.deleteToLineEnd")
        yank = key_display_text("tui.editor.yank")
        yank_pop = key_display_text("tui.editor.yankPop")
        undo = key_display_text("tui.editor.undo")
        tab = key_display_text("tui.input.tab")

        # App keybindings
        interrupt = key_display_text("app.interrupt")
        clear = key_display_text("app.clear")
        exit_key = key_display_text("app.exit")
        suspend = key_display_text("app.suspend")
        cycle_thinking_level = key_display_text("app.thinking.cycle")
        cycle_model_forward = key_display_text("app.model.cycleForward")
        select_model = key_display_text("app.model.select")
        expand_tools = key_display_text("app.tools.expand")
        toggle_thinking = key_display_text("app.thinking.toggle")
        cycle_task_view = key_display_text("app.tasks.cycleView")
        external_editor = key_display_text("app.editor.external")
        cycle_model_backward = key_display_text("app.model.cycleBackward")
        follow_up = key_display_text("app.message.followUp")
        dequeue = key_display_text("app.message.dequeue")
        paste_image = key_display_text("app.clipboard.pasteImage")

        windows_note = " (Ctrl+Enter on Windows Terminal)" if sys.platform == "win32" else ""
        # The four arrow keys share one row; assembled here only so the row fits
        # on a source line — the rendered table is the TS's, cell for cell.
        cursor_keys = f"`{cursor_up}` / `{cursor_down}` / `{cursor_left}` / `{cursor_right}`"

        hotkeys = f"""
**Navigation**
| Key | Action |
|-----|--------|
| {cursor_keys} | Move cursor / browse history (Up when empty) |
| `{cursor_word_left}` / `{cursor_word_right}` | Move by word |
| `{cursor_line_start}` | Start of line |
| `{cursor_line_end}` | End of line |
| `{jump_forward}` | Jump forward to character |
| `{jump_backward}` | Jump backward to character |
| `{page_up}` / `{page_down}` | Scroll by page |

**Editing**
| Key | Action |
|-----|--------|
| `{submit}` | Send message |
| `{new_line}` | New line{windows_note} |
| `{delete_word_backward}` | Delete word backwards |
| `{delete_word_forward}` | Delete word forwards |
| `{delete_to_line_start}` | Delete to start of line |
| `{delete_to_line_end}` | Delete to end of line |
| `{yank}` | Paste the most-recently-deleted text |
| `{yank_pop}` | Cycle through the deleted text after pasting |
| `{undo}` | Undo |

**Other**
| Key | Action |
|-----|--------|
| `{tab}` | Path completion / accept autocomplete |
| `{interrupt}` | Cancel autocomplete / abort streaming |
| `{clear}` | Clear editor (first) / exit (second) |
| `{exit_key}` | Exit (when editor is empty) |
| `{suspend}` | Suspend to background |
| `{cycle_thinking_level}` | Cycle thinking level |
| `{cycle_model_forward}` / `{cycle_model_backward}` | Cycle models |
| `{select_model}` | Open model selector |
| `{expand_tools}` | Toggle tool output expansion |
| `{toggle_thinking}` | Toggle thinking block visibility |
| `{cycle_task_view}` | Cycle task panel view (tasks → subagents → teams) |
| `{external_editor}` | Edit message in external editor |
| `{follow_up}` | Queue follow-up message |
| `{dequeue}` | Restore queued messages |
| `{paste_image}` | Paste image from clipboard |
| `/` | Slash commands |
| `!` | Run bash command |
| `!!` | Run bash command (excluded from context) |
"""

        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(DynamicBorder())
        self._ctx.chat_container.add_child(
            Text(theme.bold(theme.fg("accent", "Keyboard Shortcuts")), 1, 0)
        )
        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(
            Markdown(hotkeys.strip(), 1, 1, self._ctx.get_markdown_theme_with_settings())
        )
        self._ctx.chat_container.add_child(DynamicBorder())
        self._ctx.ui.request_render()

    def handle_debug(self) -> None:
        """``/debug`` — dump the rendered frame and the message log to a file.

        The widths are the point: every line is recorded with the width the
        renderer measured it at, which is how a "rendered line exceeds terminal
        width" crash gets diagnosed after the fact.
        """
        theme = get_theme()
        width = self._ctx.ui.terminal.columns
        height = self._ctx.ui.terminal.rows
        all_lines = self._ctx.ui.render(width)

        debug_log_path = get_debug_log_path()
        debug_data = "\n".join(
            [
                f"Debug output at {_iso_now()}",
                f"Terminal: {width}x{height}",
                f"Total lines: {len(all_lines)}",
                "",
                "=== All rendered lines with visible widths ===",
                *(
                    f"[{index}] (w={visible_width(line)}) {json.dumps(line)}"
                    for index, line in enumerate(all_lines)
                ),
                "",
                "=== Agent messages (JSONL) ===",
                *(_dump_message(message) for message in self._ctx.session.messages),
                "",
            ]
        )

        os.makedirs(os.path.dirname(debug_log_path), exist_ok=True)
        with open(debug_log_path, "w", encoding="utf-8") as handle:
            handle.write(debug_data)

        self._ctx.chat_container.add_child(Spacer(1))
        self._ctx.chat_container.add_child(
            Text(
                f"{theme.fg('accent', '✓ Debug log written')}\n{theme.fg('muted', debug_log_path)}",
                1,
                1,
            )
        )
        self._ctx.ui.request_render()


def _locale_string(value: int) -> str:
    """``Number.toLocaleString()`` for the counts ``/session`` prints.

    Thousands separators are hard-coded to commas rather than read off the
    machine's locale: the TS runs with node's default locale in practice, and a
    number that renders one way in CI and another on a French laptop is a
    difference nobody asked for.
    """
    return f"{value:,}"


def _iso_now() -> str:
    """``new Date().toISOString()``: UTC, milliseconds, trailing ``Z``."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond // 1000:03d}Z"


def _dump_message(message: Any) -> str:
    """One message as a JSON line, whether it is a model or a plain dict."""
    dump: Callable[[], Any] | None = getattr(message, "model_dump", None)
    if callable(dump):
        return json.dumps(dump(), default=str)
    return json.dumps(message, default=str)
