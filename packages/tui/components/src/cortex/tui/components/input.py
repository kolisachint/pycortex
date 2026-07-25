"""Single-line text input with horizontal scrolling.

Mechanical port of hoocode's ``packages/tui/src/components/input.ts``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cortex.tui.editing import KillRing, UndoStack
from cortex.tui.keys import decode_kitty_printable, get_keybindings
from cortex.tui.render import CURSOR_MARKER
from cortex.tui.util import (
    grapheme_segments,
    is_punctuation_char,
    is_whitespace_char,
    slice_by_column,
    visible_width,
)

__all__ = ["Input", "InputState"]

PASTE_START = "\x1b[200~"
PASTE_END = "\x1b[201~"


@dataclass
class InputState:
    """Undo snapshot."""

    value: str
    cursor: int


class Input:
    """Single-line text input. Implements ``Component`` and ``Focusable``."""

    def __init__(self) -> None:
        self._value = ""
        self._cursor = 0  # cursor position in the value, in code units
        self.on_submit: Callable[[str], None] | None = None
        self.on_escape: Callable[[], None] | None = None

        #: Focusable interface — set by the TUI when focus changes.
        self.focused = False

        # Bracketed paste buffering
        self._paste_buffer = ""
        self._is_in_paste = False

        # Emacs-style kill/yank
        self._kill_ring = KillRing()
        self._last_action: str | None = None  # "kill" | "yank" | "type-word"

        self._undo_stack: UndoStack[InputState] = UndoStack()

    def get_value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._cursor = min(self._cursor, len(value))

    # ---- input ------------------------------------------------------------

    def handle_input(self, data: str) -> None:  # noqa: C901 - 1:1 with the TS dispatch
        if PASTE_START in data:
            self._is_in_paste = True
            self._paste_buffer = ""
            data = data.replace(PASTE_START, "", 1)

        if self._is_in_paste:
            self._paste_buffer += data
            end_index = self._paste_buffer.find(PASTE_END)
            if end_index != -1:
                paste_content = self._paste_buffer[:end_index]
                self._handle_paste(paste_content)
                self._is_in_paste = False
                remaining = self._paste_buffer[end_index + len(PASTE_END) :]
                self._paste_buffer = ""
                if remaining:
                    self.handle_input(remaining)
            return

        kb = get_keybindings()

        if kb.matches(data, "tui.select.cancel"):
            if self.on_escape is not None:
                self.on_escape()
            return

        if kb.matches(data, "tui.editor.undo"):
            self._undo()
            return

        if kb.matches(data, "tui.input.submit") or data == "\n":
            if self.on_submit is not None:
                self.on_submit(self._value)
            return

        if kb.matches(data, "tui.editor.deleteCharBackward"):
            self._handle_backspace()
            return

        if kb.matches(data, "tui.editor.deleteCharForward"):
            self._handle_forward_delete()
            return

        if kb.matches(data, "tui.editor.deleteWordBackward"):
            self._delete_word_backwards()
            return

        if kb.matches(data, "tui.editor.deleteWordForward"):
            self._delete_word_forward()
            return

        if kb.matches(data, "tui.editor.deleteToLineStart"):
            self._delete_to_line_start()
            return

        if kb.matches(data, "tui.editor.deleteToLineEnd"):
            self._delete_to_line_end()
            return

        if kb.matches(data, "tui.editor.yank"):
            self._yank()
            return
        if kb.matches(data, "tui.editor.yankPop"):
            self._yank_pop()
            return

        if kb.matches(data, "tui.editor.cursorLeft"):
            self._last_action = None
            if self._cursor > 0:
                graphemes = grapheme_segments(self._value[: self._cursor])
                self._cursor -= len(graphemes[-1]) if graphemes else 1
            return

        if kb.matches(data, "tui.editor.cursorRight"):
            self._last_action = None
            if self._cursor < len(self._value):
                graphemes = grapheme_segments(self._value[self._cursor :])
                self._cursor += len(graphemes[0]) if graphemes else 1
            return

        if kb.matches(data, "tui.editor.cursorLineStart"):
            self._last_action = None
            self._cursor = 0
            return

        if kb.matches(data, "tui.editor.cursorLineEnd"):
            self._last_action = None
            self._cursor = len(self._value)
            return

        if kb.matches(data, "tui.editor.cursorWordLeft"):
            self._move_word_backwards()
            return

        if kb.matches(data, "tui.editor.cursorWordRight"):
            self._move_word_forwards()
            return

        # Kitty CSI-u printable (e.g. \x1b[97u for 'a'). Terminals with Kitty
        # flag 1 send CSI-u for every key, printables included, so decode before
        # the control-character check — those sequences contain \x1b and would
        # otherwise be rejected.
        kitty_printable = decode_kitty_printable(data)
        if kitty_printable is not None:
            self._insert_character(kitty_printable)
            return

        # Printable input, Unicode included; reject control characters
        # (C0 0x00-0x1F, DEL 0x7F, C1 0x80-0x9F).
        has_control_chars = any(
            ord(ch) < 32 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F for ch in data
        )
        if not has_control_chars:
            self._insert_character(data)

    # ---- editing primitives -----------------------------------------------

    def _insert_character(self, char: str) -> None:
        # Undo coalescing: a run of word characters is one undo unit.
        if is_whitespace_char(char) or self._last_action != "type-word":
            self._push_undo()
        self._last_action = "type-word"

        self._value = self._value[: self._cursor] + char + self._value[self._cursor :]
        self._cursor += len(char)

    def _handle_backspace(self) -> None:
        self._last_action = None
        if self._cursor > 0:
            self._push_undo()
            graphemes = grapheme_segments(self._value[: self._cursor])
            grapheme_length = len(graphemes[-1]) if graphemes else 1
            self._value = (
                self._value[: self._cursor - grapheme_length] + self._value[self._cursor :]
            )
            self._cursor -= grapheme_length

    def _handle_forward_delete(self) -> None:
        self._last_action = None
        if self._cursor < len(self._value):
            self._push_undo()
            graphemes = grapheme_segments(self._value[self._cursor :])
            grapheme_length = len(graphemes[0]) if graphemes else 1
            self._value = (
                self._value[: self._cursor] + self._value[self._cursor + grapheme_length :]
            )

    def _delete_to_line_start(self) -> None:
        if self._cursor == 0:
            return
        self._push_undo()
        deleted_text = self._value[: self._cursor]
        self._kill_ring.push(deleted_text, prepend=True, accumulate=self._last_action == "kill")
        self._last_action = "kill"
        self._value = self._value[self._cursor :]
        self._cursor = 0

    def _delete_to_line_end(self) -> None:
        if self._cursor >= len(self._value):
            return
        self._push_undo()
        deleted_text = self._value[self._cursor :]
        self._kill_ring.push(deleted_text, prepend=False, accumulate=self._last_action == "kill")
        self._last_action = "kill"
        self._value = self._value[: self._cursor]

    def _delete_word_backwards(self) -> None:
        if self._cursor == 0:
            return

        # Captured before moving: the move resets `_last_action`.
        was_kill = self._last_action == "kill"

        self._push_undo()

        old_cursor = self._cursor
        self._move_word_backwards()
        delete_from = self._cursor
        self._cursor = old_cursor

        deleted_text = self._value[delete_from : self._cursor]
        self._kill_ring.push(deleted_text, prepend=True, accumulate=was_kill)
        self._last_action = "kill"

        self._value = self._value[:delete_from] + self._value[self._cursor :]
        self._cursor = delete_from

    def _delete_word_forward(self) -> None:
        if self._cursor >= len(self._value):
            return

        was_kill = self._last_action == "kill"

        self._push_undo()

        old_cursor = self._cursor
        self._move_word_forwards()
        delete_to = self._cursor
        self._cursor = old_cursor

        deleted_text = self._value[self._cursor : delete_to]
        self._kill_ring.push(deleted_text, prepend=False, accumulate=was_kill)
        self._last_action = "kill"

        self._value = self._value[: self._cursor] + self._value[delete_to:]

    def _yank(self) -> None:
        text = self._kill_ring.peek()
        if not text:
            return

        self._push_undo()

        self._value = self._value[: self._cursor] + text + self._value[self._cursor :]
        self._cursor += len(text)
        self._last_action = "yank"

    def _yank_pop(self) -> None:
        if self._last_action != "yank" or self._kill_ring.length <= 1:
            return

        self._push_undo()

        # Remove what the last yank inserted; it is still at the end of the ring
        # until the rotation below.
        prev_text = self._kill_ring.peek() or ""
        self._value = self._value[: self._cursor - len(prev_text)] + self._value[self._cursor :]
        self._cursor -= len(prev_text)

        self._kill_ring.rotate()
        text = self._kill_ring.peek() or ""
        self._value = self._value[: self._cursor] + text + self._value[self._cursor :]
        self._cursor += len(text)
        self._last_action = "yank"

    def _push_undo(self) -> None:
        self._undo_stack.push(InputState(value=self._value, cursor=self._cursor))

    def _undo(self) -> None:
        snapshot = self._undo_stack.pop()
        if snapshot is None:
            return
        self._value = snapshot.value
        self._cursor = snapshot.cursor
        self._last_action = None

    def _move_word_backwards(self) -> None:
        if self._cursor == 0:
            return

        self._last_action = None
        graphemes = grapheme_segments(self._value[: self._cursor])

        # Skip trailing whitespace.
        while graphemes and is_whitespace_char(graphemes[-1]):
            self._cursor -= len(graphemes.pop())

        if graphemes:
            if is_punctuation_char(graphemes[-1]):
                while graphemes and is_punctuation_char(graphemes[-1]):
                    self._cursor -= len(graphemes.pop())
            else:
                while (
                    graphemes
                    and not is_whitespace_char(graphemes[-1])
                    and not is_punctuation_char(graphemes[-1])
                ):
                    self._cursor -= len(graphemes.pop())

    def _move_word_forwards(self) -> None:
        if self._cursor >= len(self._value):
            return

        self._last_action = None
        graphemes = grapheme_segments(self._value[self._cursor :])
        index = 0

        # Skip leading whitespace.
        while index < len(graphemes) and is_whitespace_char(graphemes[index]):
            self._cursor += len(graphemes[index])
            index += 1

        if index < len(graphemes):
            if is_punctuation_char(graphemes[index]):
                while index < len(graphemes) and is_punctuation_char(graphemes[index]):
                    self._cursor += len(graphemes[index])
                    index += 1
            else:
                while (
                    index < len(graphemes)
                    and not is_whitespace_char(graphemes[index])
                    and not is_punctuation_char(graphemes[index])
                ):
                    self._cursor += len(graphemes[index])
                    index += 1

    def _handle_paste(self, pasted_text: str) -> None:
        self._last_action = None
        self._push_undo()

        # A single-line input drops line breaks outright and widens tabs.
        clean_text = (
            pasted_text.replace("\r\n", "")
            .replace("\r", "")
            .replace("\n", "")
            .replace("\t", "    ")
        )

        self._value = self._value[: self._cursor] + clean_text + self._value[self._cursor :]
        self._cursor += len(clean_text)

    def invalidate(self) -> None:
        """No cached state to invalidate currently."""

    # ---- render -----------------------------------------------------------

    def render(self, width: int) -> list[str]:
        prompt = "> "
        available_width = width - len(prompt)

        if available_width <= 0:
            return [prompt]

        visible_text = ""
        cursor_display = self._cursor
        total_width = visible_width(self._value)

        if total_width < available_width:
            # Everything fits, with room for the cursor at the end.
            visible_text = self._value
        else:
            # Horizontal scrolling. Reserve a column for the cursor when it sits
            # at the very end.
            scroll_width = (
                available_width - 1 if self._cursor == len(self._value) else available_width
            )
            cursor_col = visible_width(self._value[: self._cursor])

            if scroll_width > 0:
                half_width = scroll_width // 2
                if cursor_col < half_width:
                    start_col = 0
                elif cursor_col > total_width - half_width:
                    start_col = max(0, total_width - scroll_width)
                else:
                    start_col = max(0, cursor_col - half_width)

                visible_text = slice_by_column(self._value, start_col, scroll_width, True)
                before_cursor = slice_by_column(
                    self._value, start_col, max(0, cursor_col - start_col), True
                )
                cursor_display = len(before_cursor)
            else:
                visible_text = ""
                cursor_display = 0

        # Fake cursor: the character under it, or a space past the end.
        graphemes = grapheme_segments(visible_text[cursor_display:])
        at_cursor = graphemes[0] if graphemes else " "

        before_cursor_text = visible_text[:cursor_display]
        after_cursor = visible_text[cursor_display + len(at_cursor) :]

        # Zero-width hardware-cursor marker, emitted before the fake cursor so
        # the IME candidate window lands in the right place.
        marker = CURSOR_MARKER if self.focused else ""

        cursor_char = f"\x1b[7m{at_cursor}\x1b[27m"  # reverse video, then normal
        text_with_cursor = before_cursor_text + marker + cursor_char + after_cursor

        visual_length = visible_width(text_with_cursor)
        padding = " " * max(0, available_width - visual_length)
        return [prompt + text_with_cursor + padding]
