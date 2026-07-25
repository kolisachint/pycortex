"""Multi-line editor: word wrap, history, kill ring, undo and autocomplete.

Mechanical port of hoocode's ``packages/tui/src/components/editor.ts``.

Two things read differently from the TS and are deliberate:

* ``Intl.Segmenter`` yields ``{segment, index}`` pairs; :func:`cortex.tui.util.
  grapheme_segments` yields plain clusters, so :class:`_Segment` re-attaches the
  index. Those indices — like every column in this module — are **code point**
  offsets, where the TS counts UTF-16 code units. They are internal and used
  consistently, so the screen is unaffected; only a caller reading
  :meth:`Editor.get_cursor` across an astral character would see the difference.
* ``setTimeout``/``AbortController`` become ``loop.call_later`` and the
  :mod:`cortex.tui.components.cancellable_loader` port of the same pair. With no
  running event loop there is nothing to schedule, so autocomplete simply does
  not fire — the same shape as the loader's animation.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol, runtime_checkable

from cortex.tui.components.cancellable_loader import AbortController
from cortex.tui.components.select_list import (
    SelectItem,
    SelectList,
    SelectListLayoutOptions,
    SelectListTheme,
)
from cortex.tui.editing import KillRing, UndoStack
from cortex.tui.keys import decode_printable_key, get_keybindings, matches_key
from cortex.tui.render import CURSOR_MARKER
from cortex.tui.util import (
    get_segmenter,
    is_punctuation_char,
    is_whitespace_char,
    truncate_to_width,
    visible_width,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cortex.tui.editing import (
        AutocompleteItem,
        AutocompleteProvider,
        AutocompleteSuggestions,
    )
    from cortex.tui.keys import KeybindingsManager
    from cortex.tui.render import TUI

__all__ = [
    "Editor",
    "EditorOptions",
    "EditorState",
    "EditorTheme",
    "TextChunk",
    "word_wrap_line",
]

_base_segmenter = get_segmenter()

#: JavaScript's ``\s``. Python's differs at both ends (it matches ``\x1c``-``\x1f``
#: and ``\x85``, and does not match ``﻿``), and these classes decide where a
#: completion context starts.
_JS_SPACE = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"

#: Regex matching paste markers like ``[paste #1 +123 lines]`` or ``[paste #2 1234 chars]``.
PASTE_MARKER_REGEX = re.compile(r"\[paste #(\d+)( (\+\d+ lines|\d+ chars))?\]")

#: Non-global version for single-segment testing.
PASTE_MARKER_SINGLE = re.compile(r"\A\[paste #(\d+)( (\+\d+ lines|\d+ chars))?\]\Z")

# Contexts that re-trigger completion after an edit.
_SYMBOL_CONTEXT_RE = re.compile(rf"(?:^|[{_JS_SPACE}])[@#][^{_JS_SPACE}]*$")
_SYMBOL_DEBOUNCE_RE = re.compile(rf'(?:^|[ \t])(?:@(?:"[^"]*|[^{_JS_SPACE}]*)|#[^{_JS_SPACE}]*)$')
_COMPLETION_CHAR_RE = re.compile(r"[a-zA-Z0-9.\-_]")
# JS `\w`, which is ASCII-only — Python's `\w` would also match letters with
# diacritics and change when a pasted path gets a leading space.
_WORD_CHAR_RE = re.compile(r"[A-Za-z0-9_]")
_PATH_START_RE = re.compile(r"^[/~.]")
_CSI_U_CTRL_RE = re.compile(r"\x1b\[(\d+);5u")

_SLASH_COMMAND_SELECT_LIST_LAYOUT = SelectListLayoutOptions(
    min_primary_column_width=12,
    max_primary_column_width=32,
)

_ATTACHMENT_AUTOCOMPLETE_DEBOUNCE_MS = 20


@dataclass(frozen=True, slots=True)
class _Segment:
    """``Intl.SegmentData``, minus the fields this module never reads.

    A dataclass rather than a `NamedTuple`: the TS field is called ``index``,
    which would shadow `tuple.index`.
    """

    segment: str
    index: int


def _is_paste_marker(segment: str) -> bool:
    """Is this segment a paste marker (i.e. was merged by :func:`_segment_with_markers`)?"""
    return len(segment) >= 10 and PASTE_MARKER_SINGLE.match(segment) is not None


def _base_segments(text: str) -> list[_Segment]:
    segments: list[_Segment] = []
    index = 0
    for cluster in _base_segmenter(text):
        segments.append(_Segment(cluster, index))
        index += len(cluster)
    return segments


def _segment_with_markers(text: str, valid_ids: set[int]) -> list[_Segment]:
    """Segment text, merging paste markers into single atomic segments.

    This makes cursor movement, deletion, word-wrap etc. treat paste markers as
    single units. Only markers whose numeric ID exists in ``valid_ids`` merge.
    """
    # Fast path: no paste markers in the text or no valid IDs.
    if not valid_ids or "[paste #" not in text:
        return _base_segments(text)

    # Find all marker spans with valid IDs.
    markers: list[tuple[int, int]] = []
    for match in PASTE_MARKER_REGEX.finditer(text):
        if int(match.group(1)) not in valid_ids:
            continue
        markers.append((match.start(), match.end()))
    if not markers:
        return _base_segments(text)

    # Build merged segment list.
    result: list[_Segment] = []
    marker_idx = 0

    for seg in _base_segments(text):
        # Skip past markers that are entirely before this segment.
        while marker_idx < len(markers) and markers[marker_idx][1] <= seg.index:
            marker_idx += 1

        marker = markers[marker_idx] if marker_idx < len(markers) else None

        if marker is not None and marker[0] <= seg.index < marker[1]:
            # This segment falls inside a marker. If this is the first segment
            # of the marker, emit a merged segment; otherwise skip it (already
            # merged into the first segment).
            if seg.index == marker[0]:
                result.append(_Segment(text[marker[0] : marker[1]], marker[0]))
        else:
            result.append(seg)

    return result


@dataclass
class TextChunk:
    """A chunk of text for word-wrap layout, with its position in the line."""

    text: str
    start_index: int
    end_index: int


def word_wrap_line(  # noqa: C901 - 1:1 with the TS
    line: str,
    max_width: int,
    pre_segmented: Sequence[_Segment] | None = None,
) -> list[TextChunk]:
    """Split a line into word-wrapped chunks.

    Wraps at word boundaries when possible, falling back to character-level
    wrapping for words longer than the available width.

    Args:
        line: The text line to wrap.
        max_width: Maximum visible width per chunk.
        pre_segmented: Optional pre-segmented graphemes (e.g. paste-marker
            aware). When omitted the default segmenter is used.
    """
    if not line or max_width <= 0:
        return [TextChunk("", 0, 0)]

    if visible_width(line) <= max_width:
        return [TextChunk(line, 0, len(line))]

    chunks: list[TextChunk] = []
    segments = list(pre_segmented) if pre_segmented is not None else _base_segments(line)

    current_width = 0
    chunk_start = 0

    # Wrap opportunity: the position after the last whitespace before a
    # non-whitespace grapheme, i.e. where a line break is allowed.
    wrap_opp_index = -1
    wrap_opp_width = 0

    for i, seg in enumerate(segments):
        grapheme = seg.segment
        g_width = visible_width(grapheme)
        char_index = seg.index
        is_ws = not _is_paste_marker(grapheme) and is_whitespace_char(grapheme)

        # Overflow check before advancing.
        if current_width + g_width > max_width:
            if wrap_opp_index >= 0 and current_width - wrap_opp_width + g_width <= max_width:
                # Backtrack to last wrap opportunity (the remaining content plus
                # the current grapheme still fits within max_width).
                chunks.append(
                    TextChunk(line[chunk_start:wrap_opp_index], chunk_start, wrap_opp_index)
                )
                chunk_start = wrap_opp_index
                current_width -= wrap_opp_width
            elif chunk_start < char_index:
                # No viable wrap opportunity: force-break at current position.
                # This also handles the case where backtracking to a word
                # boundary wouldn't help because the remaining content plus the
                # current grapheme (e.g. a wide character) still exceeds
                # max_width.
                chunks.append(TextChunk(line[chunk_start:char_index], chunk_start, char_index))
                chunk_start = char_index
                current_width = 0
            wrap_opp_index = -1

        if g_width > max_width:
            # Single atomic segment wider than max_width (e.g. a paste marker in
            # a narrow terminal). Re-wrap it at grapheme granularity. The segment
            # remains logically atomic for cursor movement / editing — the split
            # is purely visual for word-wrap layout.
            sub_chunks = word_wrap_line(grapheme, max_width)
            for sub in sub_chunks[:-1]:
                chunks.append(
                    TextChunk(sub.text, char_index + sub.start_index, char_index + sub.end_index)
                )
            last = sub_chunks[-1]
            chunk_start = char_index + last.start_index
            current_width = visible_width(last.text)
            wrap_opp_index = -1
            continue

        # Advance.
        current_width += g_width

        # Record wrap opportunity: whitespace followed by non-whitespace.
        # Multiple spaces join (no break between them); the break point is after
        # the last space before the next word.
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if (
            is_ws
            and nxt is not None
            and (_is_paste_marker(nxt.segment) or not is_whitespace_char(nxt.segment))
        ):
            wrap_opp_index = nxt.index
            wrap_opp_width = current_width

    # Push final chunk.
    chunks.append(TextChunk(line[chunk_start:], chunk_start, len(line)))

    return chunks


@dataclass
class EditorState:
    """Undo snapshot: the whole document plus the cursor."""

    lines: list[str] = field(default_factory=lambda: [""])
    cursor_line: int = 0
    cursor_col: int = 0


@dataclass
class _LayoutLine:
    text: str
    has_cursor: bool
    cursor_pos: int | None = None


class _VisualLine(NamedTuple):
    logical_line: int
    start_col: int
    length: int


@dataclass
class EditorTheme:
    border_color: Callable[[str], str]
    select_list: SelectListTheme


@dataclass
class EditorOptions:
    padding_x: float | None = None
    autocomplete_max_visible: float | None = None


@runtime_checkable
class _SupportsFileCompletionTrigger(Protocol):
    """The optional ``shouldTriggerFileCompletion`` half of the TS provider.

    Optional interface members have no Protocol equivalent, so the editor asks at
    runtime — which is what ``!provider.shouldTriggerFileCompletion || ...``
    does in the TS.
    """

    def should_trigger_file_completion(
        self, lines: list[str], cursor_line: int, cursor_col: int
    ) -> bool: ...


def _clamped_int(
    value: float | None, default: int, minimum: int, maximum: int | None = None
) -> int:
    """``Number.isFinite(x) ? clamp(Math.floor(x)) : default``."""
    if value is None or not math.isfinite(value):
        return default
    floored = math.floor(value)
    floored = max(minimum, floored)
    return floored if maximum is None else min(maximum, floored)


class Editor:
    """Multi-line editor. Implements ``Component`` and ``Focusable``."""

    def __init__(
        self,
        tui: TUI,
        theme: EditorTheme,
        options: EditorOptions | None = None,
    ) -> None:
        options = options if options is not None else EditorOptions()
        self._state = EditorState()

        #: Focusable interface — set by the TUI when focus changes.
        self.focused = False

        self.tui = tui
        self._theme = theme
        self._padding_x = _clamped_int(options.padding_x, 0, 0)

        # Last render width, kept for cursor navigation.
        self._last_width = 80

        # Vertical scrolling support
        self._scroll_offset = 0

        #: Border colour (can be changed dynamically).
        self.border_color: Callable[[str], str] = theme.border_color

        #: Prompt prefix shown on the first line (e.g. ``"> "`` or ``"! "``).
        self.prompt_prefix = ""
        #: Colour function for the prompt prefix.
        self.prompt_color: Callable[[str], str] = lambda s: s

        # Autocomplete support
        self._autocomplete_provider: AutocompleteProvider | None = None
        self._autocomplete_list: SelectList | None = None
        self._autocomplete_items: list[AutocompleteItem] = []
        self._autocomplete_select_items: list[SelectItem] = []
        self._autocomplete_state: Literal["regular", "force"] | None = None
        self._autocomplete_prefix = ""
        self._autocomplete_max_visible = _clamped_int(options.autocomplete_max_visible, 5, 3, 20)
        self._autocomplete_abort: AbortController | None = None
        self._autocomplete_debounce_timer: asyncio.TimerHandle | None = None
        self._autocomplete_request_task: asyncio.Future[None] | None = None
        self._autocomplete_start_token = 0
        self._autocomplete_request_id = 0

        # Paste tracking for large pastes
        self._pastes: dict[int, str] = {}
        self._paste_counter = 0

        # Bracketed paste mode buffering
        self._paste_buffer = ""
        self._is_in_paste = False

        # Prompt history for up/down navigation
        self._history: list[str] = []
        self._history_index = -1  # -1 = not browsing, 0 = most recent, 1 = older, …

        # Kill ring for Emacs-style kill/yank operations
        self._kill_ring = KillRing()
        self._last_action: Literal["kill", "yank", "type-word"] | None = None

        # Character jump mode
        self._jump_mode: Literal["forward", "backward"] | None = None

        # Preferred visual column for vertical cursor movement (sticky column)
        self._preferred_visual_col: int | None = None

        # When the cursor is snapped to the start of an atomic segment, e.g. a
        # paste marker, cursor_col no longer reflects where the cursor would have
        # landed. This field stores the pre-snap cursor_col so that the next
        # vertical move can resolve it to a visual column on whatever VL it
        # belongs to.
        self._snapped_from_cursor_col: int | None = None

        # Undo support
        self._undo_stack: UndoStack[EditorState] = UndoStack()

        self.on_submit: Callable[[str], None] | None = None
        self.on_change: Callable[[str], None] | None = None
        self.disable_submit = False

    # ---- segmentation -----------------------------------------------------

    def _valid_paste_ids(self) -> set[int]:
        """Set of currently valid paste IDs, for marker-aware segmentation."""
        return set(self._pastes)

    def _segment(self, text: str) -> list[_Segment]:
        """Segment text with paste-marker awareness, merging valid markers only."""
        return _segment_with_markers(text, self._valid_paste_ids())

    # ---- options ----------------------------------------------------------

    def get_padding_x(self) -> int:
        return self._padding_x

    def set_padding_x(self, padding: float) -> None:
        new_padding = _clamped_int(padding, 0, 0)
        if self._padding_x != new_padding:
            self._padding_x = new_padding
            self.tui.request_render()

    def get_autocomplete_max_visible(self) -> int:
        return self._autocomplete_max_visible

    def set_autocomplete_max_visible(self, max_visible: float) -> None:
        new_max_visible = _clamped_int(max_visible, 5, 3, 20)
        if self._autocomplete_max_visible != new_max_visible:
            self._autocomplete_max_visible = new_max_visible
            self.tui.request_render()

    def set_autocomplete_provider(self, provider: AutocompleteProvider) -> None:
        self._cancel_autocomplete()
        self._autocomplete_provider = provider

    # ---- history ----------------------------------------------------------

    def add_to_history(self, text: str) -> None:
        """Add a prompt to history for up/down arrow navigation.

        Called after successful submission.
        """
        trimmed = text.strip()
        if not trimmed:
            return
        # Don't add consecutive duplicates
        if self._history and self._history[0] == trimmed:
            return
        self._history.insert(0, trimmed)
        # Limit history size
        if len(self._history) > 100:
            self._history.pop()

    def _is_editor_empty(self) -> bool:
        return len(self._state.lines) == 1 and self._state.lines[0] == ""

    def _is_on_first_visual_line(self) -> bool:
        visual_lines = self._build_visual_line_map(self._last_width)
        return self._find_current_visual_line(visual_lines) == 0

    def _is_on_last_visual_line(self) -> bool:
        visual_lines = self._build_visual_line_map(self._last_width)
        return self._find_current_visual_line(visual_lines) == len(visual_lines) - 1

    def _navigate_history(self, direction: int) -> None:
        self._last_action = None
        if not self._history:
            return

        # Up(-1) increases the index, Down(1) decreases it.
        new_index = self._history_index - direction
        if new_index < -1 or new_index >= len(self._history):
            return

        # Capture state when first entering history browsing mode
        if self._history_index == -1 and new_index >= 0:
            self._push_undo_snapshot()

        self._history_index = new_index

        if self._history_index == -1:
            # Returned to "current" state - clear editor
            self._set_text_internal("")
        else:
            self._set_text_internal(self._history[self._history_index] or "")

    def _set_text_internal(self, text: str) -> None:
        """``setText`` that doesn't reset history state — used by history navigation."""
        lines = text.split("\n")
        self._state.lines = lines if lines else [""]
        self._state.cursor_line = len(self._state.lines) - 1
        self._set_cursor_col(len(self._state.lines[self._state.cursor_line]))
        # Reset scroll - render() will adjust to show the cursor
        self._scroll_offset = 0

        if self.on_change is not None:
            self.on_change(self.get_text())

    # ---- rendering --------------------------------------------------------

    def invalidate(self) -> None:
        """No cached state to invalidate currently."""

    def render(self, width: int) -> list[str]:  # noqa: C901 - 1:1 with the TS
        max_padding = max(0, (width - 1) // 2)
        padding_x = min(self._padding_x, max_padding)
        content_width = max(1, width - padding_x * 2)

        # Prompt prefix reserves space on the first line
        prompt_prefix_width = visible_width(f"{self.prompt_prefix} ") if self.prompt_prefix else 0

        # Layout width: with padding the cursor can overflow into it, without
        # padding we reserve 1 column for the cursor. Also reserve space for the
        # prompt prefix on the first line.
        layout_width = max(1, content_width - (0 if padding_x else 1) - prompt_prefix_width)

        # Store for cursor navigation (must match the wrapping width)
        self._last_width = layout_width

        horizontal = self.border_color("─")

        # Layout the text
        layout_lines = self._layout_text(layout_width)

        # Calculate max visible lines: 30% of terminal height, minimum 5 lines
        terminal_rows = self.tui.terminal.rows
        max_visible_lines = max(5, math.floor(terminal_rows * 0.3))

        # Find the cursor line index in layout_lines
        cursor_line_index = next(
            (i for i, line in enumerate(layout_lines) if line.has_cursor),
            -1,
        )
        if cursor_line_index == -1:
            cursor_line_index = 0

        # Adjust scroll offset to keep the cursor visible
        if cursor_line_index < self._scroll_offset:
            self._scroll_offset = cursor_line_index
        elif cursor_line_index >= self._scroll_offset + max_visible_lines:
            self._scroll_offset = cursor_line_index - max_visible_lines + 1

        # Clamp scroll offset to a valid range
        max_scroll_offset = max(0, len(layout_lines) - max_visible_lines)
        self._scroll_offset = max(0, min(self._scroll_offset, max_scroll_offset))

        # Get the visible lines slice
        visible_lines = layout_lines[self._scroll_offset : self._scroll_offset + max_visible_lines]

        result: list[str] = []
        left_padding = " " * padding_x
        right_padding = left_padding

        # Render top border (with scroll indicator if scrolled down)
        if self._scroll_offset > 0:
            indicator = f"─── ↑ {self._scroll_offset} more "
            remaining = width - visible_width(indicator)
            if remaining >= 0:
                result.append(self.border_color(indicator + "─" * remaining))
            else:
                result.append(self.border_color(truncate_to_width(indicator, width)))
        else:
            result.append(horizontal * width)

        # Render each visible layout line. Emit the hardware cursor marker only
        # when focused and not showing autocomplete.
        emit_cursor_marker = self.focused and not self._autocomplete_state

        for visible_line_index, layout_line in enumerate(visible_lines):
            display_text = layout_line.text
            line_visible_width = visible_width(layout_line.text)
            cursor_in_padding = False

            # Add the cursor if this line has it
            if layout_line.has_cursor and layout_line.cursor_pos is not None:
                before = display_text[: layout_line.cursor_pos]
                after = display_text[layout_line.cursor_pos :]

                # Hardware cursor marker (zero-width, emitted before the fake
                # cursor for IME positioning)
                marker = CURSOR_MARKER if emit_cursor_marker else ""

                if after:
                    # Cursor is on a character (grapheme) — replace it with a
                    # highlighted version.
                    after_graphemes = self._segment(after)
                    first_grapheme = after_graphemes[0].segment if after_graphemes else ""
                    rest_after = after[len(first_grapheme) :]
                    cursor = f"\x1b[7m{first_grapheme}\x1b[0m"
                    display_text = before + marker + cursor + rest_after
                    # line_visible_width stays the same — replacing, not adding.
                else:
                    # Cursor is at the end — add a highlighted space.
                    cursor = "\x1b[7m \x1b[0m"
                    display_text = before + marker + cursor
                    line_visible_width += 1
                    # If the cursor overflows content width into the padding, flag it
                    if line_visible_width > content_width and padding_x > 0:
                        cursor_in_padding = True

            # Prepend the prompt prefix to the first visible line
            if visible_line_index == 0 and self.prompt_prefix:
                colored_prefix = self.prompt_color(f"{self.prompt_prefix} ")
                display_text = colored_prefix + display_text
                line_visible_width += prompt_prefix_width

            # Calculate padding based on actual visible width
            padding = " " * max(0, content_width - line_visible_width)
            line_right_padding = right_padding[1:] if cursor_in_padding else right_padding

            # Render the line (no side borders, just horizontal lines above and below)
            result.append(f"{left_padding}{display_text}{padding}{line_right_padding}")

        # Render bottom border (with scroll indicator if more content below)
        lines_below = len(layout_lines) - (self._scroll_offset + len(visible_lines))
        if lines_below > 0:
            indicator = f"─── ↓ {lines_below} more "
            remaining = width - visible_width(indicator)
            result.append(self.border_color(indicator + "─" * max(0, remaining)))
        else:
            result.append(horizontal * width)

        # Add the autocomplete list if active
        if self._autocomplete_state and self._autocomplete_list is not None:
            for line in self._autocomplete_list.render(content_width):
                line_padding = " " * max(0, content_width - visible_width(line))
                result.append(f"{left_padding}{line}{line_padding}{right_padding}")

        return result

    def _layout_text(self, content_width: int) -> list[_LayoutLine]:  # noqa: C901 - 1:1 with the TS
        layout_lines: list[_LayoutLine] = []

        if not self._state.lines or (len(self._state.lines) == 1 and self._state.lines[0] == ""):
            # Empty editor
            layout_lines.append(_LayoutLine("", True, 0))
            return layout_lines

        # Process each logical line
        for i, line in enumerate(self._state.lines):
            is_current_line = i == self._state.cursor_line

            if visible_width(line) <= content_width:
                # Line fits in one layout line
                if is_current_line:
                    layout_lines.append(_LayoutLine(line, True, self._state.cursor_col))
                else:
                    layout_lines.append(_LayoutLine(line, False))
                continue

            # Line needs wrapping - use word-aware wrapping
            chunks = word_wrap_line(line, content_width, self._segment(line))

            for chunk_index, chunk in enumerate(chunks):
                cursor_pos = self._state.cursor_col
                is_last_chunk = chunk_index == len(chunks) - 1

                # Determine if the cursor is in this chunk. For word-wrapped
                # chunks we need to handle the case where the cursor might be in
                # trimmed whitespace at the end of a chunk.
                has_cursor_in_chunk = False
                adjusted_cursor_pos = 0

                if is_current_line:
                    if is_last_chunk:
                        # Last chunk: the cursor belongs here if >= start_index
                        has_cursor_in_chunk = cursor_pos >= chunk.start_index
                        adjusted_cursor_pos = cursor_pos - chunk.start_index
                    else:
                        # Non-last chunk: the cursor belongs here if in range
                        # [start_index, end_index), but we need to handle the
                        # visual position in the trimmed text.
                        has_cursor_in_chunk = chunk.start_index <= cursor_pos < chunk.end_index
                        if has_cursor_in_chunk:
                            adjusted_cursor_pos = cursor_pos - chunk.start_index
                            # Clamp to text length (in case the cursor was in
                            # trimmed whitespace)
                            adjusted_cursor_pos = min(adjusted_cursor_pos, len(chunk.text))

                if has_cursor_in_chunk:
                    layout_lines.append(_LayoutLine(chunk.text, True, adjusted_cursor_pos))
                else:
                    layout_lines.append(_LayoutLine(chunk.text, False))

        return layout_lines

    # ---- text access ------------------------------------------------------

    def get_text(self) -> str:
        return "\n".join(self._state.lines)

    def _expand_paste_markers(self, text: str) -> str:
        result = text
        for paste_id, paste_content in self._pastes.items():
            marker_regex = re.compile(rf"\[paste #{paste_id}( (\+\d+ lines|\d+ chars))?\]")
            # A replacement *function* so `\g` / `\1` inside pasted content is
            # literal, exactly as the TS callback form does.
            result = marker_regex.sub(lambda _match, text=paste_content: text, result)
        return result

    def get_expanded_text(self) -> str:
        """Text with paste markers expanded to their actual content.

        Use this when you need the full content (e.g. for an external editor).
        """
        return self._expand_paste_markers("\n".join(self._state.lines))

    def get_lines(self) -> list[str]:
        return list(self._state.lines)

    def get_cursor(self) -> tuple[int, int]:
        """``(line, col)`` — the TS returns ``{line, col}``."""
        return (self._state.cursor_line, self._state.cursor_col)

    def set_text(self, text: str) -> None:
        self._cancel_autocomplete()
        self._last_action = None
        self._history_index = -1  # Exit history browsing mode
        normalized = self._normalize_text(text)
        # Push an undo snapshot if the content differs (makes programmatic
        # changes undoable)
        if self.get_text() != normalized:
            self._push_undo_snapshot()
        self._set_text_internal(normalized)

    def insert_text_at_cursor(self, text: str) -> None:
        """Insert text at the current cursor position.

        Used for programmatic insertion (e.g. clipboard image markers). Atomic
        for undo — a single undo restores the entire pre-insert state.
        """
        if not text:
            return
        self._cancel_autocomplete()
        self._push_undo_snapshot()
        self._last_action = None
        self._history_index = -1
        self._insert_text_at_cursor_internal(text)

    def _normalize_text(self, text: str) -> str:
        """Normalize line endings (``\\r\\n``/``\\r`` → ``\\n``) and expand tabs."""
        return text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")

    def _insert_text_at_cursor_internal(self, text: str) -> None:
        """Insert at the cursor, single or multi-line.

        Does not push undo snapshots or trigger autocomplete — the caller is
        responsible. Normalizes line endings and calls ``on_change`` once at the
        end.
        """
        if not text:
            return

        normalized = self._normalize_text(text)
        inserted_lines = normalized.split("\n")

        current_line = self._state.lines[self._state.cursor_line]
        before_cursor = current_line[: self._state.cursor_col]
        after_cursor = current_line[self._state.cursor_col :]

        if len(inserted_lines) == 1:
            # Single line - insert at the cursor position
            self._state.lines[self._state.cursor_line] = before_cursor + normalized + after_cursor
            self._set_cursor_col(self._state.cursor_col + len(normalized))
        else:
            # Multi-line insertion
            self._state.lines = [
                *self._state.lines[: self._state.cursor_line],
                before_cursor + inserted_lines[0],
                *inserted_lines[1:-1],
                inserted_lines[-1] + after_cursor,
                *self._state.lines[self._state.cursor_line + 1 :],
            ]

            self._state.cursor_line += len(inserted_lines) - 1
            self._set_cursor_col(len(inserted_lines[-1]))

        if self.on_change is not None:
            self.on_change(self.get_text())

    # ---- input ------------------------------------------------------------

    def handle_input(self, data: str) -> None:  # noqa: C901, PLR0911, PLR0912 - 1:1 with the TS dispatch
        kb = get_keybindings()

        # Handle character jump mode (awaiting the next character to jump to)
        if self._jump_mode is not None:
            # Cancel if the hotkey is pressed again
            if kb.matches(data, "tui.editor.jumpForward") or kb.matches(
                data, "tui.editor.jumpBackward"
            ):
                self._jump_mode = None
                return

            printable = decode_printable_key(data)
            if printable is None and data and ord(data[0]) >= 32:
                printable = data
            if printable is not None:
                # Printable character - perform the jump
                direction = self._jump_mode
                self._jump_mode = None
                self._jump_to_char(printable, direction)
                return

            # Control character - cancel and fall through to normal handling
            self._jump_mode = None

        # Handle bracketed paste mode
        if "\x1b[200~" in data:
            self._is_in_paste = True
            self._paste_buffer = ""
            data = data.replace("\x1b[200~", "", 1)

        if self._is_in_paste:
            self._paste_buffer += data
            end_index = self._paste_buffer.find("\x1b[201~")
            if end_index != -1:
                paste_content = self._paste_buffer[:end_index]
                if paste_content:
                    self._handle_paste(paste_content)
                self._is_in_paste = False
                remaining = self._paste_buffer[end_index + 6 :]
                self._paste_buffer = ""
                if remaining:
                    self.handle_input(remaining)
            return

        # Ctrl+C - let the parent handle it (exit/clear)
        if kb.matches(data, "tui.input.copy"):
            return

        # Undo
        if kb.matches(data, "tui.editor.undo"):
            self._undo()
            return

        # Handle autocomplete mode
        if self._autocomplete_state and self._autocomplete_list is not None:
            if kb.matches(data, "tui.select.cancel"):
                self._cancel_autocomplete()
                return

            if kb.matches(data, "tui.select.up") or kb.matches(data, "tui.select.down"):
                self._autocomplete_list.handle_input(data)
                return

            if kb.matches(data, "tui.input.tab"):
                selected = self._autocomplete_list.get_selected_item()
                if selected is not None and self._autocomplete_provider is not None:
                    self._apply_selected_completion(selected)
                    self._cancel_autocomplete()
                    if self.on_change is not None:
                        self.on_change(self.get_text())
                return

            if kb.matches(data, "tui.select.confirm"):
                selected = self._autocomplete_list.get_selected_item()
                if selected is not None and self._autocomplete_provider is not None:
                    self._apply_selected_completion(selected)

                    if self._autocomplete_prefix.startswith("/"):
                        self._cancel_autocomplete()
                        # Fall through to submit
                    else:
                        self._cancel_autocomplete()
                        if self.on_change is not None:
                            self.on_change(self.get_text())
                        return

        # Tab - trigger completion
        if kb.matches(data, "tui.input.tab") and not self._autocomplete_state:
            self._handle_tab_completion()
            return

        # Deletion actions
        if kb.matches(data, "tui.editor.deleteToLineEnd"):
            self._delete_to_end_of_line()
            return
        if kb.matches(data, "tui.editor.deleteToLineStart"):
            self._delete_to_start_of_line()
            return
        if kb.matches(data, "tui.editor.deleteWordBackward"):
            self._delete_word_backwards()
            return
        if kb.matches(data, "tui.editor.deleteWordForward"):
            self._delete_word_forward()
            return
        if kb.matches(data, "tui.editor.deleteCharBackward") or matches_key(
            data, "shift+backspace"
        ):
            self._handle_backspace()
            return
        if kb.matches(data, "tui.editor.deleteCharForward") or matches_key(data, "shift+delete"):
            self._handle_forward_delete()
            return

        # Kill ring actions
        if kb.matches(data, "tui.editor.yank"):
            self._yank()
            return
        if kb.matches(data, "tui.editor.yankPop"):
            self._yank_pop()
            return

        # Cursor movement actions
        if kb.matches(data, "tui.editor.cursorLineStart"):
            self._move_to_line_start()
            return
        if kb.matches(data, "tui.editor.cursorLineEnd"):
            self._move_to_line_end()
            return
        if kb.matches(data, "tui.editor.cursorWordLeft"):
            self._move_word_backwards()
            return
        if kb.matches(data, "tui.editor.cursorWordRight"):
            self._move_word_forwards()
            return

        # New line
        if (
            kb.matches(data, "tui.input.newLine")
            or (data and ord(data[0]) == 10 and len(data) > 1)
            or data == "\x1b\r"
            or data == "\x1b[13;2~"
            or (len(data) > 1 and "\x1b" in data and "\r" in data)
            or data == "\n"
        ):
            if self._should_submit_on_backslash_enter(data, kb):
                self._handle_backspace()
                self._submit_value()
                return
            self._add_new_line()
            return

        # Submit (Enter)
        if kb.matches(data, "tui.input.submit"):
            if self.disable_submit:
                return

            # Workaround for terminals without Shift+Enter support: if the char
            # before the cursor is `\`, delete it and insert a newline instead of
            # submitting.
            current_line = self._state.lines[self._state.cursor_line]
            if self._state.cursor_col > 0 and current_line[self._state.cursor_col - 1] == "\\":
                self._handle_backspace()
                self._add_new_line()
                return

            self._submit_value()
            return

        # Arrow key navigation (with history support)
        if kb.matches(data, "tui.editor.cursorUp"):
            if self._is_editor_empty():
                self._navigate_history(-1)
            elif self._history_index > -1 and self._is_on_first_visual_line():
                self._navigate_history(-1)
            elif self._is_on_first_visual_line():
                # Already at the top - jump to start of line
                self._move_to_line_start()
            else:
                self._move_cursor(-1, 0)
            return
        if kb.matches(data, "tui.editor.cursorDown"):
            if self._history_index > -1 and self._is_on_last_visual_line():
                self._navigate_history(1)
            elif self._is_on_last_visual_line():
                # Already at the bottom - jump to end of line
                self._move_to_line_end()
            else:
                self._move_cursor(1, 0)
            return
        if kb.matches(data, "tui.editor.cursorRight"):
            self._move_cursor(0, 1)
            return
        if kb.matches(data, "tui.editor.cursorLeft"):
            self._move_cursor(0, -1)
            return

        # Page up/down - scroll by a page and move the cursor
        if kb.matches(data, "tui.editor.pageUp"):
            self._page_scroll(-1)
            return
        if kb.matches(data, "tui.editor.pageDown"):
            self._page_scroll(1)
            return

        # Character jump mode triggers
        if kb.matches(data, "tui.editor.jumpForward"):
            self._jump_mode = "forward"
            return
        if kb.matches(data, "tui.editor.jumpBackward"):
            self._jump_mode = "backward"
            return

        # Shift+Space - insert a regular space
        if matches_key(data, "shift+space"):
            self._insert_character(" ")
            return

        printable = decode_printable_key(data)
        if printable is not None:
            self._insert_character(printable)
            return

        # Regular characters
        if data and ord(data[0]) >= 32:
            self._insert_character(data)

    def _apply_selected_completion(self, selected: SelectItem) -> None:
        """The shared body of the Tab and Enter completion branches."""
        provider = self._autocomplete_provider
        if provider is None:
            return
        item = self._source_item(selected) or selected
        self._push_undo_snapshot()
        self._last_action = None
        result = provider.apply_completion(
            self._state.lines,
            self._state.cursor_line,
            self._state.cursor_col,
            item,
            self._autocomplete_prefix,
        )
        self._state.lines = result.lines
        self._state.cursor_line = result.cursor_line
        self._set_cursor_col(result.cursor_col)

    # ---- editing ----------------------------------------------------------

    def _insert_character(self, char: str, skip_undo_coalescing: bool = False) -> None:
        self._history_index = -1  # Exit history browsing mode

        # Undo coalescing (fish-style):
        # - Consecutive word chars coalesce into one undo unit
        # - Space captures state before itself (so undo removes space + the
        #   following word together)
        # - Each space is separately undoable
        # Skip coalescing when called from atomic operations (e.g. _handle_paste)
        if not skip_undo_coalescing:
            if is_whitespace_char(char) or self._last_action != "type-word":
                self._push_undo_snapshot()
            self._last_action = "type-word"

        line = self._state.lines[self._state.cursor_line]

        before = line[: self._state.cursor_col]
        after = line[self._state.cursor_col :]

        self._state.lines[self._state.cursor_line] = before + char + after
        self._set_cursor_col(self._state.cursor_col + len(char))

        if self.on_change is not None:
            self.on_change(self.get_text())

        # Check whether we should trigger or update autocomplete
        if not self._autocomplete_state:
            # Auto-trigger for "/" at the start of a line (slash commands)
            if char == "/" and self._is_at_start_of_message():
                self._try_trigger_autocomplete()
            # Auto-trigger for symbol-based completion like @ or # at token boundaries
            elif char in ("@", "#"):
                current_line = self._state.lines[self._state.cursor_line]
                text_before_cursor = current_line[: self._state.cursor_col]
                char_before_symbol = (
                    text_before_cursor[-2] if len(text_before_cursor) >= 2 else None
                )
                if len(text_before_cursor) == 1 or char_before_symbol in (" ", "\t"):
                    self._try_trigger_autocomplete()
            # Also auto-trigger when typing letters in a slash command or symbol
            # completion context
            elif _COMPLETION_CHAR_RE.search(char):
                current_line = self._state.lines[self._state.cursor_line]
                text_before_cursor = current_line[: self._state.cursor_col]
                # Are we in a slash command (with or without arguments)?
                if self._is_in_slash_command_context(text_before_cursor):
                    self._try_trigger_autocomplete()
                # Or in a symbol-based completion context like @ or #?
                elif _SYMBOL_CONTEXT_RE.search(text_before_cursor):
                    self._try_trigger_autocomplete()
        else:
            self._update_autocomplete()

    def _handle_paste(self, pasted_text: str) -> None:
        self._cancel_autocomplete()
        self._history_index = -1  # Exit history browsing mode
        self._last_action = None

        self._push_undo_snapshot()

        # Some terminals (e.g. tmux popups with extended-keys-format=csi-u)
        # re-encode control bytes inside bracketed paste as CSI-u Ctrl+<letter>
        # sequences (ESC [ <codepoint> ; 5 u). Decode those back to their literal
        # byte so the per-char filter below preserves newlines instead of
        # stripping ESC and leaking the printable tail (e.g. "[106;5u") into the
        # editor.
        def _decode_csi_u(match: re.Match[str]) -> str:
            cp = int(match.group(1))
            if 97 <= cp <= 122:
                return chr(cp - 96)
            if 65 <= cp <= 90:
                return chr(cp - 64)
            return match.group(0)

        decoded_text = _CSI_U_CTRL_RE.sub(_decode_csi_u, pasted_text)

        # Clean the pasted text: normalize line endings, expand tabs
        clean_text = self._normalize_text(decoded_text)

        # Filter out non-printable characters except newlines
        filtered_text = "".join(char for char in clean_text if char == "\n" or ord(char) >= 32)

        # If pasting a file path (starts with /, ~ or .) and the character before
        # the cursor is a word character, prepend a space for readability
        if _PATH_START_RE.match(filtered_text):
            current_line = self._state.lines[self._state.cursor_line]
            char_before_cursor = (
                current_line[self._state.cursor_col - 1] if self._state.cursor_col > 0 else ""
            )
            if char_before_cursor and _WORD_CHAR_RE.search(char_before_cursor):
                filtered_text = f" {filtered_text}"

        # Split into lines to check for a large paste
        pasted_lines = filtered_text.split("\n")

        # Is this a large paste (> 10 lines or > 1000 characters)?
        total_chars = len(filtered_text)
        if len(pasted_lines) > 10 or total_chars > 1000:
            # Store the paste and insert a marker
            self._paste_counter += 1
            paste_id = self._paste_counter
            self._pastes[paste_id] = filtered_text

            # Insert a marker like "[paste #1 +123 lines]" or "[paste #1 1234 chars]"
            marker = (
                f"[paste #{paste_id} +{len(pasted_lines)} lines]"
                if len(pasted_lines) > 10
                else f"[paste #{paste_id} {total_chars} chars]"
            )
            self._insert_text_at_cursor_internal(marker)
            return

        # Single or multi-line paste — insert atomically either way (never
        # triggering autocomplete during a paste).
        self._insert_text_at_cursor_internal(filtered_text)

    def _add_new_line(self) -> None:
        self._cancel_autocomplete()
        self._history_index = -1  # Exit history browsing mode
        self._last_action = None

        self._push_undo_snapshot()

        current_line = self._state.lines[self._state.cursor_line]

        before = current_line[: self._state.cursor_col]
        after = current_line[self._state.cursor_col :]

        # Split the current line
        self._state.lines[self._state.cursor_line] = before
        self._state.lines.insert(self._state.cursor_line + 1, after)

        # Move the cursor to the start of the new line
        self._state.cursor_line += 1
        self._set_cursor_col(0)

        if self.on_change is not None:
            self.on_change(self.get_text())

    def _should_submit_on_backslash_enter(self, data: str, kb: KeybindingsManager) -> bool:
        if self.disable_submit:
            return False
        if not matches_key(data, "enter"):
            return False
        submit_keys = kb.get_keys("tui.input.submit")
        has_shift_enter = "shift+enter" in submit_keys or "shift+return" in submit_keys
        if not has_shift_enter:
            return False

        current_line = self._state.lines[self._state.cursor_line]
        return self._state.cursor_col > 0 and current_line[self._state.cursor_col - 1] == "\\"

    def _submit_value(self) -> None:
        self._cancel_autocomplete()
        result = self._expand_paste_markers("\n".join(self._state.lines)).strip()

        self._state = EditorState()
        self._pastes.clear()
        self._paste_counter = 0
        self._history_index = -1
        self._scroll_offset = 0
        self._undo_stack.clear()
        self._last_action = None

        if self.on_change is not None:
            self.on_change("")
        if self.on_submit is not None:
            self.on_submit(result)

    def _handle_backspace(self) -> None:
        self._history_index = -1  # Exit history browsing mode
        self._last_action = None

        if self._state.cursor_col > 0:
            self._push_undo_snapshot()

            # Delete the grapheme before the cursor (handles emoji, combining
            # characters, etc.)
            line = self._state.lines[self._state.cursor_line]
            before_cursor = line[: self._state.cursor_col]

            graphemes = self._segment(before_cursor)
            grapheme_length = len(graphemes[-1].segment) if graphemes else 1

            before = line[: self._state.cursor_col - grapheme_length]
            after = line[self._state.cursor_col :]

            self._state.lines[self._state.cursor_line] = before + after
            self._set_cursor_col(self._state.cursor_col - grapheme_length)
        elif self._state.cursor_line > 0:
            self._push_undo_snapshot()

            # Merge with the previous line
            current_line = self._state.lines[self._state.cursor_line]
            previous_line = self._state.lines[self._state.cursor_line - 1]

            self._state.lines[self._state.cursor_line - 1] = previous_line + current_line
            del self._state.lines[self._state.cursor_line]

            self._state.cursor_line -= 1
            self._set_cursor_col(len(previous_line))

        if self.on_change is not None:
            self.on_change(self.get_text())

        # Update or re-trigger autocomplete after a backspace
        if self._autocomplete_state:
            self._update_autocomplete()
        else:
            self._retrigger_autocomplete_in_context()

    def _retrigger_autocomplete_in_context(self) -> None:
        """Re-trigger completion when the edit left us in a completable context.

        Autocomplete may have been cancelled because nothing matched; the TS
        repeats this block after backspace and forward-delete.
        """
        current_line = self._state.lines[self._state.cursor_line]
        text_before_cursor = current_line[: self._state.cursor_col]
        # Slash command context
        if self._is_in_slash_command_context(text_before_cursor):
            self._try_trigger_autocomplete()
        # Symbol-based completion context like @ or #
        elif _SYMBOL_CONTEXT_RE.search(text_before_cursor):
            self._try_trigger_autocomplete()

    # ---- cursor -----------------------------------------------------------

    def _set_cursor_col(self, col: int) -> None:
        """Set the cursor column and clear ``preferred_visual_col``.

        Use for all non-vertical cursor movements to reset sticky-column
        behaviour.
        """
        self._state.cursor_col = col
        self._preferred_visual_col = None
        self._snapped_from_cursor_col = None

    def _move_to_visual_line(  # noqa: C901 - 1:1 with the TS
        self,
        visual_lines: list[_VisualLine],
        current_visual_line: int,
        target_visual_line: int,
    ) -> None:
        """Move the cursor to a target visual line, applying sticky-column logic.

        Shared by :meth:`_move_cursor` and :meth:`_page_scroll`.
        """
        if not (0 <= current_visual_line < len(visual_lines)):
            return
        if not (0 <= target_visual_line < len(visual_lines)):
            return
        current_vl = visual_lines[current_visual_line]
        target_vl = visual_lines[target_visual_line]

        # When the cursor was snapped to a segment start, resolve the pre-snap
        # position against the VL it belongs to. This gives the correct visual
        # column even after a resize reshuffles VLs.
        if self._snapped_from_cursor_col is not None:
            vl_index = self._find_visual_line_at(
                visual_lines, current_vl.logical_line, self._snapped_from_cursor_col
            )
            current_visual_col = self._snapped_from_cursor_col - visual_lines[vl_index].start_col
        else:
            current_visual_col = self._state.cursor_col - current_vl.start_col

        # For non-last segments, clamp to length-1 to stay within the segment
        is_last_source_segment = (
            current_visual_line == len(visual_lines) - 1
            or visual_lines[current_visual_line + 1].logical_line != current_vl.logical_line
        )
        source_max_visual_col = (
            current_vl.length if is_last_source_segment else max(0, current_vl.length - 1)
        )

        is_last_target_segment = (
            target_visual_line == len(visual_lines) - 1
            or visual_lines[target_visual_line + 1].logical_line != target_vl.logical_line
        )
        target_max_visual_col = (
            target_vl.length if is_last_target_segment else max(0, target_vl.length - 1)
        )

        move_to_visual_col = self._compute_vertical_move_column(
            current_visual_col, source_max_visual_col, target_max_visual_col
        )

        # Set the cursor position
        self._state.cursor_line = target_vl.logical_line
        target_col = target_vl.start_col + move_to_visual_col
        logical_line = self._state.lines[target_vl.logical_line]
        self._state.cursor_col = min(target_col, len(logical_line))

        # Snap the cursor to an atomic segment boundary (e.g. paste markers) so
        # it never lands in the middle of a multi-grapheme unit. Single-grapheme
        # segments don't need snapping.
        for seg in self._segment(logical_line):
            if seg.index > self._state.cursor_col:
                break
            if len(seg.segment) <= 1:
                continue
            if self._state.cursor_col < seg.index + len(seg.segment):
                is_continuation = seg.index < target_vl.start_col
                is_moving_down = target_visual_line > current_visual_line

                if is_continuation and is_moving_down:
                    # The segment started on a previous visual line and we
                    # already visited it on the way down. Skip all remaining
                    # continuation VLs and land on the first VL past it.
                    seg_end = seg.index + len(seg.segment)
                    nxt = target_visual_line + 1
                    while (
                        nxt < len(visual_lines)
                        and visual_lines[nxt].logical_line == target_vl.logical_line
                        and visual_lines[nxt].start_col < seg_end
                    ):
                        nxt += 1
                    if nxt < len(visual_lines):
                        self._move_to_visual_line(visual_lines, current_visual_line, nxt)
                        return

                # Snap to the start of the segment so it gets highlighted. Store
                # the pre-snap position so the next vertical move can resolve it
                # to the correct visual column.
                self._snapped_from_cursor_col = self._state.cursor_col
                self._state.cursor_col = seg.index
                return

        # No snap occurred — we moved out of the atomic segment.
        self._snapped_from_cursor_col = None

    def _compute_vertical_move_column(
        self,
        current_visual_col: int,
        source_max_visual_col: int,
        target_max_visual_col: int,
    ) -> int:
        """Compute the target visual column for vertical cursor movement.

        Implements the sticky-column decision table::

            | P | S | T | U | Scenario                        | Preferred | Move To    |
            |---|---|---|---| ------------------------------- |-----------|------------|
            | 0 | * | 0 | - | Start nav, target fits          | null      | current    |
            | 0 | * | 1 | - | Start nav, target shorter       | current   | target end |
            | 1 | 0 | 0 | 0 | Clamped, target fits preferred  | null      | preferred  |
            | 1 | 0 | 0 | 1 | Clamped, still can't fit pref.  | keep      | target end |
            | 1 | 0 | 1 | - | Clamped, target even shorter    | keep      | target end |
            | 1 | 1 | 0 | - | Rewrapped, target fits current  | null      | current    |
            | 1 | 1 | 1 | - | Rewrapped, target < current     | current   | target end |

        Where P = preferred col is set, S = cursor in the middle of the source
        line (not clamped to the end), T = target line shorter than the current
        visual col, U = target line shorter than the preferred col.
        """
        preferred = self._preferred_visual_col
        has_preferred = preferred is not None  # P
        cursor_in_middle = current_visual_col < source_max_visual_col  # S
        target_too_short = target_max_visual_col < current_visual_col  # T

        if not has_preferred or cursor_in_middle:
            if target_too_short:
                # Cases 2 and 7
                self._preferred_visual_col = current_visual_col
                return target_max_visual_col

            # Cases 1 and 6
            self._preferred_visual_col = None
            return current_visual_col

        assert preferred is not None
        target_cant_fit_preferred = target_max_visual_col < preferred  # U
        if target_too_short or target_cant_fit_preferred:
            # Cases 4 and 5
            return target_max_visual_col

        # Case 3
        self._preferred_visual_col = None
        return preferred

    def _move_to_line_start(self) -> None:
        self._last_action = None
        self._set_cursor_col(0)

    def _move_to_line_end(self) -> None:
        self._last_action = None
        current_line = self._state.lines[self._state.cursor_line]
        self._set_cursor_col(len(current_line))

    # ---- deletion ---------------------------------------------------------

    def _delete_to_start_of_line(self) -> None:
        self._history_index = -1  # Exit history browsing mode

        current_line = self._state.lines[self._state.cursor_line]

        if self._state.cursor_col > 0:
            self._push_undo_snapshot()

            # Save the deleted text to the kill ring (backward deletion = prepend)
            deleted_text = current_line[: self._state.cursor_col]
            self._kill_ring.push(deleted_text, prepend=True, accumulate=self._last_action == "kill")
            self._last_action = "kill"

            # Delete from start of line up to the cursor
            self._state.lines[self._state.cursor_line] = current_line[self._state.cursor_col :]
            self._set_cursor_col(0)
        elif self._state.cursor_line > 0:
            self._push_undo_snapshot()

            # At start of line - merge with the previous line, treating the
            # newline as deleted text
            self._kill_ring.push("\n", prepend=True, accumulate=self._last_action == "kill")
            self._last_action = "kill"

            previous_line = self._state.lines[self._state.cursor_line - 1]
            self._state.lines[self._state.cursor_line - 1] = previous_line + current_line
            del self._state.lines[self._state.cursor_line]
            self._state.cursor_line -= 1
            self._set_cursor_col(len(previous_line))

        if self.on_change is not None:
            self.on_change(self.get_text())

    def _delete_to_end_of_line(self) -> None:
        self._history_index = -1  # Exit history browsing mode

        current_line = self._state.lines[self._state.cursor_line]

        if self._state.cursor_col < len(current_line):
            self._push_undo_snapshot()

            # Save the deleted text to the kill ring (forward deletion = append)
            deleted_text = current_line[self._state.cursor_col :]
            self._kill_ring.push(
                deleted_text, prepend=False, accumulate=self._last_action == "kill"
            )
            self._last_action = "kill"

            # Delete from the cursor to the end of the line
            self._state.lines[self._state.cursor_line] = current_line[: self._state.cursor_col]
        elif self._state.cursor_line < len(self._state.lines) - 1:
            self._push_undo_snapshot()

            # At end of line - merge with the next line, treating the newline as
            # deleted text
            self._kill_ring.push("\n", prepend=False, accumulate=self._last_action == "kill")
            self._last_action = "kill"

            next_line = self._state.lines[self._state.cursor_line + 1]
            self._state.lines[self._state.cursor_line] = current_line + next_line
            del self._state.lines[self._state.cursor_line + 1]

        if self.on_change is not None:
            self.on_change(self.get_text())

    def _delete_word_backwards(self) -> None:
        self._history_index = -1  # Exit history browsing mode

        current_line = self._state.lines[self._state.cursor_line]

        # If at the start of a line, behave like backspace at column 0 (merge
        # with the previous line)
        if self._state.cursor_col == 0:
            if self._state.cursor_line > 0:
                self._push_undo_snapshot()

                # Treat the newline as deleted text (backward deletion = prepend)
                self._kill_ring.push("\n", prepend=True, accumulate=self._last_action == "kill")
                self._last_action = "kill"

                previous_line = self._state.lines[self._state.cursor_line - 1]
                self._state.lines[self._state.cursor_line - 1] = previous_line + current_line
                del self._state.lines[self._state.cursor_line]
                self._state.cursor_line -= 1
                self._set_cursor_col(len(previous_line))
        else:
            self._push_undo_snapshot()

            # Save last_action before the cursor moves (_move_word_backwards resets it)
            was_kill = self._last_action == "kill"

            old_cursor_col = self._state.cursor_col
            self._move_word_backwards()
            delete_from = self._state.cursor_col
            self._set_cursor_col(old_cursor_col)

            deleted_text = current_line[delete_from : self._state.cursor_col]
            self._kill_ring.push(deleted_text, prepend=True, accumulate=was_kill)
            self._last_action = "kill"

            self._state.lines[self._state.cursor_line] = (
                current_line[:delete_from] + current_line[self._state.cursor_col :]
            )
            self._set_cursor_col(delete_from)

        if self.on_change is not None:
            self.on_change(self.get_text())

    def _delete_word_forward(self) -> None:
        self._history_index = -1  # Exit history browsing mode

        current_line = self._state.lines[self._state.cursor_line]

        # If at the end of a line, merge with the next line (delete the newline)
        if self._state.cursor_col >= len(current_line):
            if self._state.cursor_line < len(self._state.lines) - 1:
                self._push_undo_snapshot()

                # Treat the newline as deleted text (forward deletion = append)
                self._kill_ring.push("\n", prepend=False, accumulate=self._last_action == "kill")
                self._last_action = "kill"

                next_line = self._state.lines[self._state.cursor_line + 1]
                self._state.lines[self._state.cursor_line] = current_line + next_line
                del self._state.lines[self._state.cursor_line + 1]
        else:
            self._push_undo_snapshot()

            # Save last_action before the cursor moves (_move_word_forwards resets it)
            was_kill = self._last_action == "kill"

            old_cursor_col = self._state.cursor_col
            self._move_word_forwards()
            delete_to = self._state.cursor_col
            self._set_cursor_col(old_cursor_col)

            deleted_text = current_line[self._state.cursor_col : delete_to]
            self._kill_ring.push(deleted_text, prepend=False, accumulate=was_kill)
            self._last_action = "kill"

            self._state.lines[self._state.cursor_line] = (
                current_line[: self._state.cursor_col] + current_line[delete_to:]
            )

        if self.on_change is not None:
            self.on_change(self.get_text())

    def _handle_forward_delete(self) -> None:
        self._history_index = -1  # Exit history browsing mode
        self._last_action = None

        current_line = self._state.lines[self._state.cursor_line]

        if self._state.cursor_col < len(current_line):
            self._push_undo_snapshot()

            # Delete the grapheme at the cursor (handles emoji, combining
            # characters, etc.)
            after_cursor = current_line[self._state.cursor_col :]
            graphemes = self._segment(after_cursor)
            grapheme_length = len(graphemes[0].segment) if graphemes else 1

            before = current_line[: self._state.cursor_col]
            after = current_line[self._state.cursor_col + grapheme_length :]
            self._state.lines[self._state.cursor_line] = before + after
        elif self._state.cursor_line < len(self._state.lines) - 1:
            self._push_undo_snapshot()

            # At end of line - merge with the next line
            next_line = self._state.lines[self._state.cursor_line + 1]
            self._state.lines[self._state.cursor_line] = current_line + next_line
            del self._state.lines[self._state.cursor_line + 1]

        if self.on_change is not None:
            self.on_change(self.get_text())

        # Update or re-trigger autocomplete after a forward delete
        if self._autocomplete_state:
            self._update_autocomplete()
        else:
            self._retrigger_autocomplete_in_context()

    # ---- visual line map --------------------------------------------------

    def _build_visual_line_map(self, width: int) -> list[_VisualLine]:
        """Map visual lines to logical positions.

        Each element carries the index into ``state.lines``, the starting column
        in that logical line, and the length of the visual line segment.
        """
        visual_lines: list[_VisualLine] = []

        for i, line in enumerate(self._state.lines):
            if len(line) == 0:
                # An empty line still takes one visual line
                visual_lines.append(_VisualLine(i, 0, 0))
            elif visible_width(line) <= width:
                visual_lines.append(_VisualLine(i, 0, len(line)))
            else:
                # Line needs wrapping - use word-aware wrapping
                for chunk in word_wrap_line(line, width, self._segment(line)):
                    visual_lines.append(
                        _VisualLine(i, chunk.start_index, chunk.end_index - chunk.start_index)
                    )

        return visual_lines

    def _find_visual_line_at(self, visual_lines: list[_VisualLine], line: int, col: int) -> int:
        """Find the visual line index containing the given logical position."""
        for i, vl in enumerate(visual_lines):
            if vl.logical_line != line:
                continue
            offset = col - vl.start_col
            # The cursor is in this segment if it's within range. For the last
            # segment of a logical line the cursor can be at `length` (the end
            # position).
            is_last_segment_of_line = (
                i == len(visual_lines) - 1 or visual_lines[i + 1].logical_line != vl.logical_line
            )
            if offset >= 0 and (
                offset < vl.length or (is_last_segment_of_line and offset == vl.length)
            ):
                return i
        return len(visual_lines) - 1

    def _find_current_visual_line(self, visual_lines: list[_VisualLine]) -> int:
        """Find the visual line index for the current cursor position."""
        return self._find_visual_line_at(
            visual_lines, self._state.cursor_line, self._state.cursor_col
        )

    def _move_cursor(self, delta_line: int, delta_col: int) -> None:
        self._last_action = None
        visual_lines = self._build_visual_line_map(self._last_width)
        current_visual_line = self._find_current_visual_line(visual_lines)

        if delta_line != 0:
            target_visual_line = current_visual_line + delta_line

            if 0 <= target_visual_line < len(visual_lines):
                self._move_to_visual_line(visual_lines, current_visual_line, target_visual_line)

        if delta_col != 0:
            current_line = self._state.lines[self._state.cursor_line]

            if delta_col > 0:
                # Moving right - move by one grapheme (handles emoji, combining
                # characters, etc.)
                if self._state.cursor_col < len(current_line):
                    after_cursor = current_line[self._state.cursor_col :]
                    graphemes = self._segment(after_cursor)
                    step = len(graphemes[0].segment) if graphemes else 1
                    self._set_cursor_col(self._state.cursor_col + step)
                elif self._state.cursor_line < len(self._state.lines) - 1:
                    # Wrap to the start of the next logical line
                    self._state.cursor_line += 1
                    self._set_cursor_col(0)
                elif 0 <= current_visual_line < len(visual_lines):
                    # At the end of the last line - can't move, but set
                    # preferred_visual_col for up/down navigation
                    current_vl = visual_lines[current_visual_line]
                    self._preferred_visual_col = self._state.cursor_col - current_vl.start_col
            else:
                # Moving left - move by one grapheme
                if self._state.cursor_col > 0:
                    before_cursor = current_line[: self._state.cursor_col]
                    graphemes = self._segment(before_cursor)
                    step = len(graphemes[-1].segment) if graphemes else 1
                    self._set_cursor_col(self._state.cursor_col - step)
                elif self._state.cursor_line > 0:
                    # Wrap to the end of the previous logical line
                    self._state.cursor_line -= 1
                    prev_line = self._state.lines[self._state.cursor_line]
                    self._set_cursor_col(len(prev_line))

    def _page_scroll(self, direction: int) -> None:
        """Scroll by a page (-1 up, 1 down), moving the cursor and staying in bounds."""
        self._last_action = None
        terminal_rows = self.tui.terminal.rows
        page_size = max(5, math.floor(terminal_rows * 0.3))

        visual_lines = self._build_visual_line_map(self._last_width)
        current_visual_line = self._find_current_visual_line(visual_lines)
        target_visual_line = max(
            0, min(len(visual_lines) - 1, current_visual_line + direction * page_size)
        )

        self._move_to_visual_line(visual_lines, current_visual_line, target_visual_line)

    # ---- word motion ------------------------------------------------------

    def _move_word_backwards(self) -> None:
        self._last_action = None
        current_line = self._state.lines[self._state.cursor_line]

        # If at the start of a line, move to the end of the previous line
        if self._state.cursor_col == 0:
            if self._state.cursor_line > 0:
                self._state.cursor_line -= 1
                prev_line = self._state.lines[self._state.cursor_line]
                self._set_cursor_col(len(prev_line))
            return

        text_before_cursor = current_line[: self._state.cursor_col]
        graphemes = self._segment(text_before_cursor)
        new_col = self._state.cursor_col

        # Skip trailing whitespace
        while (
            graphemes
            and not _is_paste_marker(graphemes[-1].segment)
            and is_whitespace_char(graphemes[-1].segment)
        ):
            new_col -= len(graphemes.pop().segment)

        if graphemes:
            last_grapheme = graphemes[-1].segment
            if _is_paste_marker(last_grapheme):
                # A paste marker is a single atomic word
                new_col -= len(graphemes.pop().segment)
            elif is_punctuation_char(last_grapheme):
                # Skip the punctuation run
                while (
                    graphemes
                    and is_punctuation_char(graphemes[-1].segment)
                    and not _is_paste_marker(graphemes[-1].segment)
                ):
                    new_col -= len(graphemes.pop().segment)
            else:
                # Skip the word run
                while (
                    graphemes
                    and not is_whitespace_char(graphemes[-1].segment)
                    and not is_punctuation_char(graphemes[-1].segment)
                    and not _is_paste_marker(graphemes[-1].segment)
                ):
                    new_col -= len(graphemes.pop().segment)

        self._set_cursor_col(new_col)

    def _move_word_forwards(self) -> None:
        self._last_action = None
        current_line = self._state.lines[self._state.cursor_line]

        # If at the end of a line, move to the start of the next line
        if self._state.cursor_col >= len(current_line):
            if self._state.cursor_line < len(self._state.lines) - 1:
                self._state.cursor_line += 1
                self._set_cursor_col(0)
            return

        text_after_cursor = current_line[self._state.cursor_col :]
        segments = self._segment(text_after_cursor)
        index = 0
        new_col = self._state.cursor_col

        def current() -> str | None:
            return segments[index].segment if index < len(segments) else None

        # Skip leading whitespace
        seg = current()
        while seg is not None and not _is_paste_marker(seg) and is_whitespace_char(seg):
            new_col += len(seg)
            index += 1
            seg = current()

        if seg is not None:
            if _is_paste_marker(seg):
                # A paste marker is a single atomic word
                new_col += len(seg)
            elif is_punctuation_char(seg):
                # Skip the punctuation run
                while seg is not None and is_punctuation_char(seg) and not _is_paste_marker(seg):
                    new_col += len(seg)
                    index += 1
                    seg = current()
            else:
                # Skip the word run
                while (
                    seg is not None
                    and not is_whitespace_char(seg)
                    and not is_punctuation_char(seg)
                    and not _is_paste_marker(seg)
                ):
                    new_col += len(seg)
                    index += 1
                    seg = current()

        self._set_cursor_col(new_col)

    # ---- kill ring --------------------------------------------------------

    def _yank(self) -> None:
        """Yank (paste) the most recent kill ring entry at the cursor position."""
        if self._kill_ring.length == 0:
            return

        self._push_undo_snapshot()

        text = self._kill_ring.peek()
        assert text is not None
        self._insert_yanked_text(text)

        self._last_action = "yank"

    def _yank_pop(self) -> None:
        """Cycle through the kill ring (only right after a yank or yank-pop).

        Replaces the last yanked text with the previous entry in the ring.
        """
        if self._last_action != "yank" or self._kill_ring.length <= 1:
            return

        self._push_undo_snapshot()

        # Delete the previously yanked text (still at the end of the ring, before
        # rotation)
        self._delete_yanked_text()

        # Rotate the ring: move the end to the front
        self._kill_ring.rotate()

        # Insert the new most recent entry (now at the end after rotation)
        text = self._kill_ring.peek()
        assert text is not None
        self._insert_yanked_text(text)

        self._last_action = "yank"

    def _insert_yanked_text(self, text: str) -> None:
        """Insert text at the cursor position (used by yank operations)."""
        self._history_index = -1  # Exit history browsing mode
        lines = text.split("\n")

        if len(lines) == 1:
            # Single line - insert at the cursor
            current_line = self._state.lines[self._state.cursor_line]
            before = current_line[: self._state.cursor_col]
            after = current_line[self._state.cursor_col :]
            self._state.lines[self._state.cursor_line] = before + text + after
            self._set_cursor_col(self._state.cursor_col + len(text))
        else:
            # Multi-line insert
            current_line = self._state.lines[self._state.cursor_line]
            before = current_line[: self._state.cursor_col]
            after = current_line[self._state.cursor_col :]

            # The first line merges with the text before the cursor
            self._state.lines[self._state.cursor_line] = before + lines[0]

            # Insert the middle lines
            for i in range(1, len(lines) - 1):
                self._state.lines.insert(self._state.cursor_line + i, lines[i])

            # The last line merges with the text after the cursor
            last_line_index = self._state.cursor_line + len(lines) - 1
            self._state.lines.insert(last_line_index, lines[-1] + after)

            # Update the cursor position
            self._state.cursor_line = last_line_index
            self._set_cursor_col(len(lines[-1]))

        if self.on_change is not None:
            self.on_change(self.get_text())

    def _delete_yanked_text(self) -> None:
        """Delete the previously yanked text (used by yank-pop).

        The yanked text is derived from ``kill_ring[end]`` since it hasn't been
        rotated yet.
        """
        yanked_text = self._kill_ring.peek()
        if not yanked_text:
            return

        yank_lines = yanked_text.split("\n")

        if len(yank_lines) == 1:
            # Single line - delete backward from the cursor
            current_line = self._state.lines[self._state.cursor_line]
            delete_len = len(yanked_text)
            before = current_line[: self._state.cursor_col - delete_len]
            after = current_line[self._state.cursor_col :]
            self._state.lines[self._state.cursor_line] = before + after
            self._set_cursor_col(self._state.cursor_col - delete_len)
        else:
            # Multi-line delete - the cursor is at the end of the last yanked line
            start_line = self._state.cursor_line - (len(yank_lines) - 1)
            start_col = len(self._state.lines[start_line]) - len(yank_lines[0])

            # Text after the cursor on the current line
            after_cursor = self._state.lines[self._state.cursor_line][self._state.cursor_col :]

            # Text before the yank start position
            before_yank = self._state.lines[start_line][:start_col]

            # Remove all lines from start_line to cursor_line, replacing them
            # with the merged line
            self._state.lines[start_line : start_line + len(yank_lines)] = [
                before_yank + after_cursor
            ]

            # Update the cursor
            self._state.cursor_line = start_line
            self._set_cursor_col(start_col)

        if self.on_change is not None:
            self.on_change(self.get_text())

    # ---- undo -------------------------------------------------------------

    def _push_undo_snapshot(self) -> None:
        self._undo_stack.push(self._state)

    def _undo(self) -> None:
        self._history_index = -1  # Exit history browsing mode
        snapshot = self._undo_stack.pop()
        if snapshot is None:
            return
        self._state.lines = snapshot.lines
        self._state.cursor_line = snapshot.cursor_line
        self._state.cursor_col = snapshot.cursor_col
        self._last_action = None
        self._preferred_visual_col = None
        if self.on_change is not None:
            self.on_change(self.get_text())

    # ---- character jump ---------------------------------------------------

    def _jump_to_char(self, char: str, direction: Literal["forward", "backward"]) -> None:
        """Jump to the first occurrence of a character in the given direction.

        Multi-line search, case-sensitive, skipping the current cursor position.
        """
        self._last_action = None
        is_forward = direction == "forward"
        lines = self._state.lines

        end = len(lines) if is_forward else -1
        step = 1 if is_forward else -1

        line_idx = self._state.cursor_line
        while line_idx != end:
            line = lines[line_idx]
            is_current_line = line_idx == self._state.cursor_line

            # Current line: start after/before the cursor; other lines: search
            # the full line.
            if is_forward:
                start = self._state.cursor_col + 1 if is_current_line else 0
                idx = line.find(char, start)
            elif is_current_line:
                # JS `lastIndexOf(char, -1)` clamps fromIndex to 0, so a match at
                # index 0 still counts.
                stop = max(0, self._state.cursor_col - 1)
                idx = line.rfind(char, 0, stop + len(char))
            else:
                idx = line.rfind(char)

            if idx != -1:
                self._state.cursor_line = line_idx
                self._set_cursor_col(idx)
                return
            line_idx += step
        # No match found - the cursor stays in place

    # ---- autocomplete -----------------------------------------------------

    def _is_slash_menu_allowed(self) -> bool:
        """The slash menu is only allowed on the first line of the editor."""
        return self._state.cursor_line == 0

    def _is_at_start_of_message(self) -> bool:
        """Is the cursor at the start of the message (for slash command detection)?"""
        if not self._is_slash_menu_allowed():
            return False
        current_line = self._state.lines[self._state.cursor_line]
        before_cursor = current_line[: self._state.cursor_col]
        return before_cursor.strip() in ("", "/")

    def _is_in_slash_command_context(self, text_before_cursor: str) -> bool:
        return self._is_slash_menu_allowed() and text_before_cursor.lstrip().startswith("/")

    def _get_best_autocomplete_match_index(
        self, items: Sequence[AutocompleteItem], prefix: str
    ) -> int:
        """Best autocomplete item index for the prefix, or -1 if none matches.

        Match priority: an exact match (``prefix == item.value``) always wins,
        otherwise the first item whose value starts with the prefix, otherwise -1
        (keep the default highlight). Case-sensitive, ``item.value`` only.
        """
        if not prefix:
            return -1

        first_prefix_index = -1

        for i, item in enumerate(items):
            value = item.value
            if value == prefix:
                return i  # Exact match always wins
            if first_prefix_index == -1 and value.startswith(prefix):
                first_prefix_index = i

        return first_prefix_index

    def _create_autocomplete_list(
        self, prefix: str, items: Sequence[AutocompleteItem]
    ) -> SelectList:
        layout = _SLASH_COMMAND_SELECT_LIST_LAYOUT if prefix.startswith("/") else None
        # The TS hands the provider's own item objects to `SelectList` and gets
        # the same object back from `getSelectedItem()`. `SelectList` here takes
        # its own `SelectItem`, so keep both lists and map back by position —
        # otherwise `apply_completion` would receive a copy and any field the
        # provider attached beyond value/label/description would be lost.
        self._autocomplete_items = list(items)
        self._autocomplete_select_items = [
            SelectItem(value=item.value, label=item.label, description=item.description)
            for item in items
        ]
        return SelectList(
            self._autocomplete_select_items,
            self._autocomplete_max_visible,
            self._theme.select_list,
            layout,
        )

    def _source_item(self, selected: SelectItem) -> AutocompleteItem | None:
        """The provider's item behind a `SelectList` row, matched by identity."""
        for candidate, source in zip(
            self._autocomplete_select_items, self._autocomplete_items, strict=False
        ):
            if candidate is selected:
                return source
        return None

    def _try_trigger_autocomplete(self, explicit_tab: bool = False) -> None:
        self._request_autocomplete(force=False, explicit_tab=explicit_tab)

    def _handle_tab_completion(self) -> None:
        if self._autocomplete_provider is None:
            return

        current_line = self._state.lines[self._state.cursor_line]
        before_cursor = current_line[: self._state.cursor_col]

        if self._is_in_slash_command_context(before_cursor) and " " not in before_cursor.lstrip():
            self._handle_slash_command_completion()
        else:
            self._force_file_autocomplete(True)

    def _handle_slash_command_completion(self) -> None:
        self._request_autocomplete(force=False, explicit_tab=True)

    def _force_file_autocomplete(self, explicit_tab: bool = False) -> None:
        self._request_autocomplete(force=True, explicit_tab=explicit_tab)

    def _request_autocomplete(self, *, force: bool, explicit_tab: bool) -> None:
        provider = self._autocomplete_provider
        if provider is None:
            return

        if force and isinstance(provider, _SupportsFileCompletionTrigger):
            should_trigger = provider.should_trigger_file_completion(
                self._state.lines, self._state.cursor_line, self._state.cursor_col
            )
            if not should_trigger:
                return

        self._cancel_autocomplete_request()
        self._autocomplete_start_token += 1
        start_token = self._autocomplete_start_token

        # There is no `setTimeout` without a loop, and `get_suggestions` is a
        # coroutine — with no loop running there is nothing to schedule at all.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        debounce_ms = self._get_autocomplete_debounce_ms(force=force, explicit_tab=explicit_tab)
        if debounce_ms > 0:

            def fire() -> None:
                self._autocomplete_debounce_timer = None
                self._start_autocomplete_request(
                    start_token, force=force, explicit_tab=explicit_tab
                )

            self._autocomplete_debounce_timer = loop.call_later(debounce_ms / 1000, fire)
            return

        self._start_autocomplete_request(start_token, force=force, explicit_tab=explicit_tab)

    def _start_autocomplete_request(
        self, start_token: int, *, force: bool, explicit_tab: bool
    ) -> None:
        previous_task = self._autocomplete_request_task

        async def run() -> None:
            if previous_task is not None:
                await asyncio.wait([previous_task])
            if start_token != self._autocomplete_start_token or self._autocomplete_provider is None:
                return

            controller = AbortController()
            self._autocomplete_abort = controller
            self._autocomplete_request_id += 1
            request_id = self._autocomplete_request_id
            snapshot_text = self.get_text()
            snapshot_line = self._state.cursor_line
            snapshot_col = self._state.cursor_col

            await self._run_autocomplete_request(
                request_id,
                controller,
                snapshot_text,
                snapshot_line,
                snapshot_col,
                force=force,
                explicit_tab=explicit_tab,
            )

        self._autocomplete_request_task = asyncio.ensure_future(run())

    def _get_autocomplete_debounce_ms(self, *, force: bool, explicit_tab: bool) -> int:
        if explicit_tab or force:
            return 0

        current_line = self._state.lines[self._state.cursor_line]
        text_before_cursor = current_line[: self._state.cursor_col]
        is_symbol_autocomplete_context = _SYMBOL_DEBOUNCE_RE.search(text_before_cursor) is not None
        return _ATTACHMENT_AUTOCOMPLETE_DEBOUNCE_MS if is_symbol_autocomplete_context else 0

    async def _run_autocomplete_request(
        self,
        request_id: int,
        controller: AbortController,
        snapshot_text: str,
        snapshot_line: int,
        snapshot_col: int,
        *,
        force: bool,
        explicit_tab: bool,
    ) -> None:
        provider = self._autocomplete_provider
        if provider is None:
            return

        suggestions = await provider.get_suggestions(
            self._state.lines,
            self._state.cursor_line,
            self._state.cursor_col,
            signal=controller.signal,
            force=force,
        )

        if not self._is_autocomplete_request_current(
            request_id, controller, snapshot_text, snapshot_line, snapshot_col
        ):
            return

        self._autocomplete_abort = None

        if suggestions is None or not suggestions.items:
            self._cancel_autocomplete()
            self.tui.request_render()
            return

        if force and explicit_tab and len(suggestions.items) == 1:
            item = suggestions.items[0]
            self._push_undo_snapshot()
            self._last_action = None
            result = provider.apply_completion(
                self._state.lines,
                self._state.cursor_line,
                self._state.cursor_col,
                item,
                suggestions.prefix,
            )
            self._state.lines = result.lines
            self._state.cursor_line = result.cursor_line
            self._set_cursor_col(result.cursor_col)
            if self.on_change is not None:
                self.on_change(self.get_text())
            self.tui.request_render()
            return

        self._apply_autocomplete_suggestions(suggestions, "force" if force else "regular")
        self.tui.request_render()

    def _is_autocomplete_request_current(
        self,
        request_id: int,
        controller: AbortController,
        snapshot_text: str,
        snapshot_line: int,
        snapshot_col: int,
    ) -> bool:
        return (
            not controller.signal.aborted
            and request_id == self._autocomplete_request_id
            and self.get_text() == snapshot_text
            and self._state.cursor_line == snapshot_line
            and self._state.cursor_col == snapshot_col
        )

    def _apply_autocomplete_suggestions(
        self, suggestions: AutocompleteSuggestions, state: Literal["regular", "force"]
    ) -> None:
        self._autocomplete_prefix = suggestions.prefix
        self._autocomplete_list = self._create_autocomplete_list(
            suggestions.prefix, suggestions.items
        )

        best_match_index = self._get_best_autocomplete_match_index(
            suggestions.items, suggestions.prefix
        )
        if best_match_index >= 0:
            self._autocomplete_list.set_selected_index(best_match_index)

        self._autocomplete_state = state

    def _cancel_autocomplete_request(self) -> None:
        self._autocomplete_start_token += 1
        if self._autocomplete_debounce_timer is not None:
            self._autocomplete_debounce_timer.cancel()
            self._autocomplete_debounce_timer = None
        if self._autocomplete_abort is not None:
            self._autocomplete_abort.abort()
        self._autocomplete_abort = None

    def _clear_autocomplete_ui(self) -> None:
        self._autocomplete_state = None
        self._autocomplete_list = None
        self._autocomplete_prefix = ""

    def _cancel_autocomplete(self) -> None:
        self._cancel_autocomplete_request()
        self._clear_autocomplete_ui()

    def is_showing_autocomplete(self) -> bool:
        return self._autocomplete_state is not None

    def _update_autocomplete(self) -> None:
        if not self._autocomplete_state or self._autocomplete_provider is None:
            return
        self._request_autocomplete(force=self._autocomplete_state == "force", explicit_tab=False)
