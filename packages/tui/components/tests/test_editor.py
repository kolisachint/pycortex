"""Editor tests, ported from hoocode's `test/editor.test.ts`.

The screen-level contract lives in the parity corpus (`component/editor-*` in
`packages/tui/testkit/goldens/scenarios.json`), which diffs pycortex against
frames captured from the real TS. These are the TS unit tests, which assert on
text and cursor state rather than on a frame.

Four TS tests are **not** ported: they drive `CombinedAutocompleteProvider` from
`autocomplete.ts`, which is step 1.14. They are listed in
`TestAutocompleteBlockedOn114` so the gap stays visible.

Columns here are code point offsets where the TS counts UTF-16 code units. Every
assertion below is on ASCII spans, where the two agree; see `editor.py`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from cortex.tui.components import (
    Editor,
    EditorOptions,
    EditorTheme,
    SelectListTheme,
    word_wrap_line,
)
from cortex.tui.editing import AbortSignal, AutocompleteItem
from cortex.tui.render import CURSOR_MARKER, TUI
from cortex.tui.util import visible_width

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b_[^\x07]*\x07")
MARKER_RE = re.compile(r"\[paste #\d+ \+\d+ lines\]")

UP = "\x1b[A"
DOWN = "\x1b[B"
RIGHT = "\x1b[C"
LEFT = "\x1b[D"
HOME = "\x01"  # ctrl+a
END = "\x05"  # ctrl+e
BACKSPACE = "\x7f"
DELETE = "\x1b[3~"
KILL_TO_END = "\x0b"  # ctrl+k
KILL_TO_START = "\x15"  # ctrl+u
YANK = "\x19"  # ctrl+y
YANK_POP = "\x1by"  # alt+y
DELETE_WORD_BACK = "\x17"  # ctrl+w
DELETE_WORD_FORWARD = "\x1bd"  # alt+d
WORD_LEFT = "\x1b[1;5D"  # ctrl+left
WORD_RIGHT = "\x1b[1;5C"  # ctrl+right
UNDO = "\x1b[45;5u"  # ctrl+-
ENTER = "\r"
TAB = "\t"
JUMP_FORWARD = "\x1d"  # ctrl+]
JUMP_BACKWARD = "\x1b\x1d"  # ctrl+alt+]


def strip_ansi(line: str) -> str:
    return ANSI_RE.sub("", line)


class _StubTerminal:
    """The slice of the terminal protocol a `TUI` needs to exist."""

    def __init__(self, columns: int, rows: int) -> None:
        self.columns = columns
        self.rows = rows
        self.writes: list[str] = []

    def write(self, data: str) -> None:
        self.writes.append(data)

    def start(self, on_input: Callable[[str], None], on_resize: Callable[[], None]) -> None: ...

    def stop(self) -> None: ...

    def hide_cursor(self) -> None: ...

    def show_cursor(self) -> None: ...


def _identity(text: str) -> str:
    return text


def default_editor_theme() -> EditorTheme:
    """`test/test-themes.ts`'s `defaultEditorTheme`, minus the colours."""
    return EditorTheme(
        border_color=_identity,
        select_list=SelectListTheme(
            selected_prefix=_identity,
            selected_text=_identity,
            description=_identity,
            scroll_info=_identity,
            no_match=_identity,
        ),
    )


def make_editor(cols: int = 80, rows: int = 24, **options: Any) -> Editor:
    """`new Editor(createTestTUI(cols, rows), defaultEditorTheme, options)`."""
    tui = TUI(_StubTerminal(cols, rows), False)  # pyright: ignore[reportArgumentType]
    return Editor(tui, default_editor_theme(), EditorOptions(**options))


def type_text(editor: Editor, text: str) -> None:
    for char in text:
        editor.handle_input(char)


def press(editor: Editor, key: str, times: int = 1) -> None:
    for _ in range(times):
        editor.handle_input(key)


def bracketed_paste(text: str) -> str:
    return f"\x1b[200~{text}\x1b[201~"


def paste_with_marker(editor: Editor) -> str:
    """Simulate a large paste that creates a marker, as the TS helper does."""
    big_content = "line\n" * 20
    editor.handle_input(bracketed_paste(big_content.rstrip()))
    return editor.get_text()


# ---- autocomplete doubles -------------------------------------------------


@dataclass
class Item:
    value: str
    label: str
    description: str | None = None


@dataclass
class Suggestions:
    items: list[Item]
    prefix: str


@dataclass
class Completion:
    lines: list[str]
    cursor_line: int
    cursor_col: int


def apply_completion(
    lines: list[str], cursor_line: int, cursor_col: int, item: AutocompleteItem, prefix: str
) -> Completion:
    """The TS test helper: replace the prefix with `item.value`."""
    line = lines[cursor_line] if cursor_line < len(lines) else ""
    before = line[: cursor_col - len(prefix)]
    after = line[cursor_col:]
    new_lines = list(lines)
    new_lines[cursor_line] = before + item.value + after
    return Completion(new_lines, cursor_line, cursor_col - len(prefix) + len(item.value))


@dataclass
class MockProvider:
    """An `AutocompleteProvider` whose `get_suggestions` is supplied per test."""

    suggest: Callable[[list[str], int, int, bool], Suggestions | None]
    calls: int = 0
    should_trigger: Callable[[list[str], int, int], bool] | None = None

    async def get_suggestions(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        *,
        signal: AbortSignal,
        force: bool = False,
    ) -> Suggestions | None:
        self.calls += 1
        return self.suggest(lines, cursor_line, cursor_col, force)

    def apply_completion(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        item: AutocompleteItem,
        prefix: str,
    ) -> Completion:
        return apply_completion(lines, cursor_line, cursor_col, item, prefix)


def _no_suggestions(
    _lines: list[str], _cursor_line: int, _cursor_col: int, _force: bool
) -> Suggestions | None:
    return None


def argument_provider(command: str, values: list[str], *, prefilter: bool) -> MockProvider:
    """A `/<command> <arg>` completer, the shape the TS argument tests use."""
    pattern = re.compile(rf"^/{command}\s+(\S+)$")

    def suggest(lines: list[str], _line: int, col: int, _force: bool) -> Suggestions | None:
        match = pattern.match(lines[0][:col])
        if not match:
            return None
        argument = match.group(1)
        items = [Item(v, v) for v in values]
        if prefilter:
            items = [i for i in items if i.value.startswith(argument)]
            if not items:
                return None
        return Suggestions(items, argument)

    return MockProvider(suggest=suggest)


async def flush_autocomplete() -> None:
    """The TS `flushAutocomplete`, which yields until the request chain settles.

    Each keystroke queues a task that first awaits the previous one, so the
    chain is as long as the typed prefix and every link costs a loop iteration —
    `Promise.resolve()` drains all of them at once in JS, `sleep(0)` drains one.
    """
    for _ in range(200):
        await asyncio.sleep(0)


class TestPromptHistory:
    def test_does_nothing_on_up_when_history_is_empty(self) -> None:
        editor = make_editor()
        editor.handle_input(UP)
        assert editor.get_text() == ""

    def test_shows_most_recent_entry_on_up_when_empty(self) -> None:
        editor = make_editor()
        editor.add_to_history("first prompt")
        editor.add_to_history("second prompt")
        editor.handle_input(UP)
        assert editor.get_text() == "second prompt"

    def test_cycles_through_entries_on_repeated_up(self) -> None:
        editor = make_editor()
        for entry in ("first", "second", "third"):
            editor.add_to_history(entry)

        editor.handle_input(UP)
        assert editor.get_text() == "third"
        editor.handle_input(UP)
        assert editor.get_text() == "second"
        editor.handle_input(UP)
        assert editor.get_text() == "first"
        editor.handle_input(UP)
        assert editor.get_text() == "first"

    def test_returns_to_empty_editor_on_down(self) -> None:
        editor = make_editor()
        editor.add_to_history("prompt")

        editor.handle_input(UP)
        assert editor.get_text() == "prompt"
        editor.handle_input(DOWN)
        assert editor.get_text() == ""

    def test_navigates_forward_with_down(self) -> None:
        editor = make_editor()
        for entry in ("first", "second", "third"):
            editor.add_to_history(entry)

        press(editor, UP, 3)
        editor.handle_input(DOWN)
        assert editor.get_text() == "second"
        editor.handle_input(DOWN)
        assert editor.get_text() == "third"
        editor.handle_input(DOWN)
        assert editor.get_text() == ""

    def test_exits_history_mode_when_typing(self) -> None:
        editor = make_editor()
        editor.add_to_history("old prompt")
        editor.handle_input(UP)
        editor.handle_input("x")
        assert editor.get_text() == "old promptx"

    def test_exits_history_mode_on_set_text(self) -> None:
        editor = make_editor()
        editor.add_to_history("first")
        editor.add_to_history("second")

        editor.handle_input(UP)
        editor.set_text("")

        editor.handle_input(UP)
        assert editor.get_text() == "second"

    def test_does_not_add_empty_strings(self) -> None:
        editor = make_editor()
        editor.add_to_history("")
        editor.add_to_history("   ")
        editor.add_to_history("valid")

        editor.handle_input(UP)
        assert editor.get_text() == "valid"
        editor.handle_input(UP)
        assert editor.get_text() == "valid"

    def test_does_not_add_consecutive_duplicates(self) -> None:
        editor = make_editor()
        for _ in range(3):
            editor.add_to_history("same")

        editor.handle_input(UP)
        assert editor.get_text() == "same"
        editor.handle_input(UP)
        assert editor.get_text() == "same"

    def test_allows_non_consecutive_duplicates(self) -> None:
        editor = make_editor()
        editor.add_to_history("first")
        editor.add_to_history("second")
        editor.add_to_history("first")

        editor.handle_input(UP)
        assert editor.get_text() == "first"
        editor.handle_input(UP)
        assert editor.get_text() == "second"
        editor.handle_input(UP)
        assert editor.get_text() == "first"

    def test_uses_cursor_movement_when_editor_has_content(self) -> None:
        editor = make_editor()
        editor.add_to_history("history item")
        editor.set_text("line1\nline2")

        editor.handle_input(UP)
        editor.handle_input("X")
        assert editor.get_text() == "line1X\nline2"

    def test_limits_history_to_100_entries(self) -> None:
        editor = make_editor()
        for i in range(105):
            editor.add_to_history(f"prompt {i}")

        press(editor, UP, 100)
        assert editor.get_text() == "prompt 5"
        editor.handle_input(UP)
        assert editor.get_text() == "prompt 5"

    def test_multi_line_entry_exits_on_down_from_last_line(self) -> None:
        editor = make_editor()
        editor.add_to_history("line1\nline2\nline3")

        editor.handle_input(UP)
        assert editor.get_text() == "line1\nline2\nline3"
        editor.handle_input(DOWN)
        assert editor.get_text() == ""

    def test_multi_line_entry_moves_cursor_before_navigating_up(self) -> None:
        editor = make_editor()
        editor.add_to_history("older entry")
        editor.add_to_history("line1\nline2\nline3")

        editor.handle_input(UP)
        editor.handle_input(UP)
        assert editor.get_text() == "line1\nline2\nline3"
        editor.handle_input(UP)
        assert editor.get_text() == "line1\nline2\nline3"
        editor.handle_input(UP)
        assert editor.get_text() == "older entry"

    def test_navigates_back_to_newer_via_down_after_cursor_movement(self) -> None:
        editor = make_editor()
        editor.add_to_history("line1\nline2\nline3")

        press(editor, UP, 3)
        editor.handle_input(DOWN)
        assert editor.get_text() == "line1\nline2\nline3"
        editor.handle_input(DOWN)
        assert editor.get_text() == "line1\nline2\nline3"
        editor.handle_input(DOWN)
        assert editor.get_text() == ""


class TestPublicStateAccessors:
    def test_returns_cursor_position(self) -> None:
        editor = make_editor()
        assert editor.get_cursor() == (0, 0)

        type_text(editor, "abc")
        assert editor.get_cursor() == (0, 3)

        editor.handle_input(LEFT)
        assert editor.get_cursor() == (0, 2)

    def test_returns_lines_as_a_defensive_copy(self) -> None:
        editor = make_editor()
        editor.set_text("a\nb")

        lines = editor.get_lines()
        assert lines == ["a", "b"]

        lines[0] = "mutated"
        assert editor.get_lines() == ["a", "b"]


class TestBackslashEnterNewlineWorkaround:
    def test_inserts_backslash_immediately(self) -> None:
        editor = make_editor()
        editor.handle_input("\\")
        assert editor.get_text() == "\\"

    def test_converts_standalone_backslash_to_newline_on_enter(self) -> None:
        editor = make_editor()
        editor.handle_input("\\")
        editor.handle_input(ENTER)
        assert editor.get_text() == "\n"

    def test_inserts_backslash_normally_when_followed_by_characters(self) -> None:
        editor = make_editor()
        editor.handle_input("\\")
        editor.handle_input("x")
        assert editor.get_text() == "\\x"

    def test_does_not_trigger_newline_when_backslash_is_not_before_cursor(self) -> None:
        editor = make_editor()
        submitted = False

        def on_submit(_text: str) -> None:
            nonlocal submitted
            submitted = True

        editor.on_submit = on_submit
        editor.handle_input("\\")
        editor.handle_input("x")
        editor.handle_input(ENTER)
        assert submitted is True

    def test_only_removes_one_backslash_when_multiple_are_present(self) -> None:
        editor = make_editor()
        type_text(editor, "\\\\\\")
        assert editor.get_text() == "\\\\\\"

        editor.handle_input(ENTER)
        assert editor.get_text() == "\\\\\n"


class TestKittyCsiU:
    def test_ignores_printable_sequences_with_unsupported_modifiers(self) -> None:
        editor = make_editor()
        editor.handle_input("\x1b[99;9u")
        assert editor.get_text() == ""

    def test_inserts_shifted_csi_u_letters_as_text(self) -> None:
        editor = make_editor()
        editor.handle_input("\x1b[69;2u")
        assert editor.get_text() == "E"

    def test_inserts_shifted_modify_other_keys_letters_as_text(self) -> None:
        editor = make_editor()
        editor.handle_input("\x1b[27;2;69~")
        assert editor.get_text() == "E"


class TestUnicodeTextEditing:
    def test_inserts_mixed_ascii_umlauts_and_emoji(self) -> None:
        editor = make_editor()
        for char in ("H", "e", "l", "l", "o", " ", "ä", "ö", "ü", " ", "😀"):
            editor.handle_input(char)
        assert editor.get_text() == "Hello äöü 😀"

    def test_backspace_deletes_single_code_unit_characters(self) -> None:
        editor = make_editor()
        type_text(editor, "äöü")
        editor.handle_input(BACKSPACE)
        assert editor.get_text() == "äö"

    def test_backspace_deletes_a_whole_emoji(self) -> None:
        editor = make_editor()
        editor.handle_input("😀")
        editor.handle_input("👍")
        editor.handle_input(BACKSPACE)
        assert editor.get_text() == "😀"

    def test_inserts_at_the_right_position_after_moving_over_umlauts(self) -> None:
        editor = make_editor()
        type_text(editor, "äöü")
        press(editor, LEFT, 2)
        editor.handle_input("x")
        assert editor.get_text() == "äxöü"

    def test_arrow_keys_move_across_whole_emoji(self) -> None:
        editor = make_editor()
        for char in ("😀", "👍", "🎉"):
            editor.handle_input(char)
        press(editor, LEFT, 2)
        editor.handle_input("x")
        assert editor.get_text() == "😀x👍🎉"

    def test_preserves_umlauts_across_line_breaks(self) -> None:
        editor = make_editor()
        type_text(editor, "äöü")
        editor.handle_input("\n")
        type_text(editor, "ÄÖÜ")
        assert editor.get_text() == "äöü\nÄÖÜ"

    def test_set_text_replaces_the_document_with_unicode(self) -> None:
        editor = make_editor()
        editor.set_text("Hällö Wörld! 😀 äöüÄÖÜß")
        assert editor.get_text() == "Hällö Wörld! 😀 äöüÄÖÜß"

    def test_ctrl_a_moves_to_start_and_inserts_there(self) -> None:
        editor = make_editor()
        type_text(editor, "ab")
        editor.handle_input(HOME)
        editor.handle_input("x")
        assert editor.get_text() == "xab"

    def test_deletes_words_with_ctrl_w_and_alt_backspace(self) -> None:
        editor = make_editor()

        editor.set_text("foo bar baz")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo bar "

        editor.set_text("foo bar   ")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo "

        editor.set_text("foo bar...")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo bar"

        editor.set_text("line one\nline two")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "line one\nline "

        editor.set_text("line one\n")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "line one"

        editor.set_text("foo 😀😀 bar")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo 😀😀 "
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo "

        editor.set_text("foo bar")
        editor.handle_input("\x1b\x7f")  # alt+backspace (legacy)
        assert editor.get_text() == "foo "

    def test_navigates_words_with_ctrl_left_and_right(self) -> None:
        editor = make_editor()
        editor.set_text("foo bar... baz")

        editor.handle_input(WORD_LEFT)
        assert editor.get_cursor() == (0, 11)
        editor.handle_input(WORD_LEFT)
        assert editor.get_cursor() == (0, 7)
        editor.handle_input(WORD_LEFT)
        assert editor.get_cursor() == (0, 4)

        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (0, 7)
        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (0, 10)
        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (0, 14)

        editor.set_text("   foo bar")
        editor.handle_input(HOME)
        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (0, 6)


class TestGraphemeAwareWrapping:
    def test_wraps_lines_containing_wide_emoji(self) -> None:
        editor = make_editor()
        width = 20
        editor.set_text("Hello ✅ World")
        lines = editor.render(width)
        for index, line in enumerate(lines[1:-1], start=1):
            assert visible_width(line) == width, f"line {index}"

    def test_wraps_long_emoji_runs_at_the_right_positions(self) -> None:
        editor = make_editor()
        width = 10
        editor.set_text("✅✅✅✅✅✅")
        for line in editor.render(width)[1:-1]:
            assert visible_width(line) == width

    def test_renders_isolated_thai_and_lao_am_clusters_without_drift(self) -> None:
        for text in ("ำabc", "ຳabc"):
            editor = make_editor()
            width = 8
            editor.set_text(text)
            for line in editor.render(width):
                assert visible_width(line) == width, f"width drift for {text!r}: {line!r}"

    def test_wraps_cjk_characters(self) -> None:
        editor = make_editor()
        width = 10 + 1  # +1 col reserved for the cursor
        editor.set_text("日本語テスト")
        lines = editor.render(width)

        for line in lines[1:-1]:
            assert visible_width(line) == width

        content = [strip_ansi(line).strip() for line in lines[1:-1]]
        assert len(content) == 2
        assert content[0] == "日本語テス"
        assert content[1] == "ト"

    def test_handles_mixed_ascii_and_wide_characters(self) -> None:
        editor = make_editor()
        width = 15 + 1
        editor.set_text("Test ✅ OK 日本")
        content = editor.render(width)[1:-1]
        assert len(content) == 1
        assert visible_width(content[0]) == width

    def test_renders_the_cursor_on_wide_characters(self) -> None:
        editor = make_editor()
        width = 20
        editor.set_text("A✅B")
        line = editor.render(width)[1]
        assert "\x1b[7m" in line
        assert visible_width(line) == width

    def test_does_not_exceed_width_with_emoji_at_the_wrap_boundary(self) -> None:
        editor = make_editor()
        width = 11
        editor.set_text("0123456789✅")
        for line in editor.render(width)[1:-1]:
            assert visible_width(line) <= width

    def test_cursor_sits_at_line_end_before_wrap(self) -> None:
        width = 10
        for padding_x in (0, 1):
            editor = make_editor(width + padding_x, padding_x=padding_x)

            type_text(editor, "a" * 9)
            content = editor.render(width + padding_x)[1:-1]
            assert len(content) == 1, "should be 1 content line before wrap"
            assert content[0].endswith("\x1b[7m \x1b[0m"), "cursor should be at line end"

            editor.handle_input("a")
            content = editor.render(width + padding_x)[1:-1]
            assert len(content) == 2, "should wrap to 2 content lines"


class TestPromptPrefix:
    def test_renders_the_prefix_on_the_first_line(self) -> None:
        editor = make_editor()
        editor.prompt_prefix = ">"
        editor.set_text("hello")
        content = [strip_ansi(line).rstrip() for line in editor.render(20)[1:-1]]
        assert len(content) == 1
        assert content[0].startswith("> ")
        assert "hello" in content[0]

    def test_applies_the_prompt_colour_to_the_prefix(self) -> None:
        editor = make_editor()
        editor.prompt_prefix = "!"
        editor.prompt_color = lambda s: f"\x1b[32m{s}\x1b[0m"
        editor.set_text("bash")
        assert "\x1b[32m" in editor.render(20)[1]

    def test_accounts_for_prefix_width_in_first_line_wrapping(self) -> None:
        editor = make_editor()
        editor.prompt_prefix = ">"
        editor.set_text("helloworld")
        content = [strip_ansi(line).rstrip() for line in editor.render(12)[1:-1]]
        assert len(content) == 2
        assert content[0].startswith("> ")
        assert not content[1].startswith(">")


class TestWordWrapping:
    def test_wraps_at_word_boundaries(self) -> None:
        editor = make_editor()
        editor.set_text("Hello world this is a test of word wrapping functionality")
        content = [strip_ansi(line).strip() for line in editor.render(40)[1:-1]]

        assert not content[0].endswith("-")
        for line in content:
            last_char = line.rstrip()[-1:]
            assert last_char == "" or re.match(r"[\w.,!?;:]", last_char), (
                f"line ends unexpectedly with: {last_char!r}"
            )

    def test_does_not_start_lines_with_leading_whitespace(self) -> None:
        editor = make_editor()
        editor.set_text("Word1 Word2 Word3 Word4 Word5 Word6")
        for line in editor.render(20)[1:-1]:
            plain = strip_ansi(line)
            if plain.lstrip():
                assert not re.match(r"^\s+\S", plain.rstrip())

    def test_breaks_long_urls_at_character_level(self) -> None:
        editor = make_editor()
        width = 30
        editor.set_text("Check https://example.com/very/long/path/that/exceeds/width here")
        for line in editor.render(width)[1:-1]:
            assert visible_width(line) == width

    def test_preserves_multiple_spaces_within_a_line(self) -> None:
        editor = make_editor()
        editor.set_text("Word1   Word2    Word3")
        assert "Word1   Word2" in strip_ansi(editor.render(50)[1]).strip()

    def test_handles_the_empty_string(self) -> None:
        editor = make_editor()
        editor.set_text("")
        assert len(editor.render(40)) == 3

    def test_handles_a_single_word_that_fits_exactly(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890")
        lines = editor.render(10 + 1)
        assert len(lines) == 3
        assert "1234567890" in strip_ansi(lines[1])

    def test_wraps_a_word_ending_exactly_at_the_width(self) -> None:
        chunks = word_wrap_line("hello world test", 11)
        assert len(chunks) == 2
        assert chunks[0].text == "hello "
        assert chunks[1].text == "world test"

    def test_keeps_whitespace_at_the_width_boundary(self) -> None:
        chunks = word_wrap_line("hello world test", 12)
        assert len(chunks) == 2
        assert chunks[0].text == "hello world "
        assert chunks[1].text == "test"

    def test_unbreakable_word_filling_width_then_a_space(self) -> None:
        chunks = word_wrap_line("aaaaaaaaaaaa aaaa", 12)
        assert len(chunks) == 2
        assert chunks[0].text == "aaaaaaaaaaaa"
        assert chunks[1].text == " aaaa"

    def test_wraps_a_word_that_fits_the_width_but_not_the_remainder(self) -> None:
        chunks = word_wrap_line("      aaaaaaaaaaaa", 12)
        assert len(chunks) == 2
        assert chunks[0].text == "      "
        assert chunks[1].text == "aaaaaaaaaaaa"

    def test_keeps_multi_space_and_following_word_together(self) -> None:
        chunks = word_wrap_line("Lorem ipsum dolor sit amet,    consectetur", 30)
        assert len(chunks) == 2
        assert chunks[0].text == "Lorem ipsum dolor sit "
        assert chunks[1].text == "amet,    consectetur"

    def test_keeps_them_together_when_they_fill_the_width_exactly(self) -> None:
        chunks = word_wrap_line("Lorem ipsum dolor sit amet,              consectetur", 30)
        assert len(chunks) == 2
        assert chunks[0].text == "Lorem ipsum dolor sit "
        assert chunks[1].text == "amet,              consectetur"

    def test_splits_when_word_plus_spaces_plus_word_exceeds_width(self) -> None:
        chunks = word_wrap_line("Lorem ipsum dolor sit amet,               consectetur", 30)
        assert len(chunks) == 3
        assert chunks[0].text == "Lorem ipsum dolor sit "
        assert chunks[1].text == "amet,               "
        assert chunks[2].text == "consectetur"

    def test_breaks_long_whitespace_at_the_line_boundary(self) -> None:
        chunks = word_wrap_line(
            "Lorem ipsum dolor sit amet,                         consectetur", 30
        )
        assert len(chunks) == 3
        assert chunks[0].text == "Lorem ipsum dolor sit "
        assert chunks[1].text == "amet,                         "
        assert chunks[2].text == "consectetur"

    def test_breaks_long_whitespace_at_the_line_boundary_2(self) -> None:
        chunks = word_wrap_line(
            "Lorem ipsum dolor sit amet,                          consectetur", 30
        )
        assert len(chunks) == 3
        assert chunks[0].text == "Lorem ipsum dolor sit "
        assert chunks[1].text == "amet,                         "
        assert chunks[2].text == " consectetur"

    def test_breaks_whitespace_spanning_full_lines(self) -> None:
        chunks = word_wrap_line(
            "Lorem ipsum dolor sit amet,                                     consectetur", 30
        )
        assert len(chunks) == 3
        assert chunks[0].text == "Lorem ipsum dolor sit "
        assert chunks[1].text == "amet,                         "
        assert chunks[2].text == "            consectetur"

    def test_force_breaks_when_a_wide_char_still_overflows_after_backtracking(self) -> None:
        line = f" {'a' * 186}你"
        chunks = word_wrap_line(line, 187)
        for chunk in chunks:
            assert visible_width(chunk.text) <= 187
        reconstructed = "".join(line[c.start_index : c.end_index] for c in chunks)
        assert reconstructed == line


class TestWordWrapAtomicSegments:
    """`wordWrapLine` with pre-segmented input — the paste-marker shape."""

    @staticmethod
    def _segments(line: str, pieces: list[str]) -> list[Any]:
        from cortex.tui.components.editor import _Segment  # pyright: ignore[reportPrivateUsage]

        segments: list[Any] = []
        index = 0
        for piece in pieces:
            segments.append(_Segment(piece, index))
            index += len(piece)
        assert "".join(pieces) == line
        return segments

    def test_splits_an_oversized_atomic_segment(self) -> None:
        marker = "[paste #1 +20 lines]"
        line = f"A{marker}B"
        chunks = word_wrap_line(line, 10, self._segments(line, ["A", marker, "B"]))
        for chunk in chunks:
            assert visible_width(chunk.text) <= 10
        assert "".join(line[c.start_index : c.end_index] for c in chunks) == line

    def test_splits_an_oversized_atomic_segment_at_line_start(self) -> None:
        marker = "[paste #1 +20 lines]"
        line = f"{marker}B"
        chunks = word_wrap_line(line, 10, self._segments(line, [marker, "B"]))
        for chunk in chunks:
            assert visible_width(chunk.text) <= 10
        assert "B" in chunks[-1].text
        assert "".join(line[c.start_index : c.end_index] for c in chunks) == line

    def test_splits_an_oversized_atomic_segment_at_line_end(self) -> None:
        marker = "[paste #1 +20 lines]"
        line = f"A{marker}"
        chunks = word_wrap_line(line, 10, self._segments(line, ["A", marker]))
        for chunk in chunks:
            assert visible_width(chunk.text) <= 10
        assert chunks[0].text == "A"
        assert "".join(line[c.start_index : c.end_index] for c in chunks) == line

    def test_splits_consecutive_oversized_atomic_segments(self) -> None:
        first = "[paste #1 +20 lines]"
        second = "[paste #2 +30 lines]"
        line = f"{first}{second}"
        chunks = word_wrap_line(line, 10, self._segments(line, [first, second]))
        for chunk in chunks:
            assert visible_width(chunk.text) <= 10
        assert "".join(line[c.start_index : c.end_index] for c in chunks) == line

    def test_wraps_normally_after_an_oversized_atomic_segment(self) -> None:
        marker = "[paste #1 +20 lines]"
        line = f"{marker} hello world"
        pieces = [marker, *list(" hello world")]
        chunks = word_wrap_line(line, 10, self._segments(line, pieces))
        for chunk in chunks:
            assert visible_width(chunk.text) <= 10
        assert chunks[-1].text == "world"
        assert "".join(line[c.start_index : c.end_index] for c in chunks) == line


class TestKillRing:
    def test_ctrl_w_saves_to_the_ring_and_ctrl_y_yanks_it(self) -> None:
        editor = make_editor()
        editor.set_text("foo bar baz")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo bar "

        editor.handle_input(HOME)
        editor.handle_input(YANK)
        assert editor.get_text() == "bazfoo bar "

    def test_ctrl_u_saves_to_the_ring(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)

        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == "world"

        editor.handle_input(YANK)
        assert editor.get_text() == "hello world"

    def test_ctrl_k_saves_to_the_ring(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        editor.handle_input(KILL_TO_END)
        assert editor.get_text() == ""

        editor.handle_input(YANK)
        assert editor.get_text() == "hello world"

    def test_ctrl_y_does_nothing_when_the_ring_is_empty(self) -> None:
        editor = make_editor()
        editor.set_text("test")
        editor.handle_input(YANK)
        assert editor.get_text() == "test"

    def test_alt_y_cycles_through_the_ring_after_ctrl_y(self) -> None:
        editor = make_editor()
        for word in ("first", "second", "third"):
            editor.set_text(word)
            editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == ""

        editor.handle_input(YANK)
        assert editor.get_text() == "third"
        editor.handle_input(YANK_POP)
        assert editor.get_text() == "second"
        editor.handle_input(YANK_POP)
        assert editor.get_text() == "first"
        editor.handle_input(YANK_POP)
        assert editor.get_text() == "third"

    def test_alt_y_does_nothing_when_not_preceded_by_a_yank(self) -> None:
        editor = make_editor()
        editor.set_text("test")
        editor.handle_input(DELETE_WORD_BACK)
        editor.set_text("other")

        editor.handle_input("x")
        assert editor.get_text() == "otherx"

        editor.handle_input(YANK_POP)
        assert editor.get_text() == "otherx"

    def test_alt_y_does_nothing_with_one_entry(self) -> None:
        editor = make_editor()
        editor.set_text("only")
        editor.handle_input(DELETE_WORD_BACK)

        editor.handle_input(YANK)
        assert editor.get_text() == "only"
        editor.handle_input(YANK_POP)
        assert editor.get_text() == "only"

    def test_consecutive_ctrl_w_accumulates_into_one_entry(self) -> None:
        editor = make_editor()
        editor.set_text("one two three")
        press(editor, DELETE_WORD_BACK, 3)
        assert editor.get_text() == ""

        editor.handle_input(YANK)
        assert editor.get_text() == "one two three"

    def test_ctrl_u_accumulates_multiline_deletes_including_newlines(self) -> None:
        editor = make_editor()
        editor.set_text("line1\nline2\nline3")

        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == "line1\nline2\n"
        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == "line1\nline2"
        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == "line1\n"
        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == "line1"
        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == ""

        editor.handle_input(YANK)
        assert editor.get_text() == "line1\nline2\nline3"

    def test_backward_prepends_and_forward_appends_while_accumulating(self) -> None:
        editor = make_editor()
        editor.set_text("prefix|suffix")
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)

        editor.handle_input(KILL_TO_END)
        editor.handle_input(KILL_TO_END)
        assert editor.get_text() == "prefix"

        editor.handle_input(YANK)
        assert editor.get_text() == "prefix|suffix"

    def test_non_delete_actions_break_accumulation(self) -> None:
        editor = make_editor()
        editor.set_text("foo bar baz")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo bar "

        editor.handle_input("x")
        assert editor.get_text() == "foo bar x"

        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "foo bar "

        editor.handle_input(YANK)
        assert editor.get_text() == "foo bar x"

        editor.handle_input(YANK_POP)
        assert editor.get_text() == "foo bar baz"

    def test_non_yank_actions_break_the_alt_y_chain(self) -> None:
        editor = make_editor()
        editor.set_text("first")
        editor.handle_input(DELETE_WORD_BACK)
        editor.set_text("second")
        editor.handle_input(DELETE_WORD_BACK)
        editor.set_text("")

        editor.handle_input(YANK)
        assert editor.get_text() == "second"

        editor.handle_input("x")
        assert editor.get_text() == "secondx"

        editor.handle_input(YANK_POP)
        assert editor.get_text() == "secondx"

    def test_ring_rotation_persists_after_cycling(self) -> None:
        editor = make_editor()
        for word in ("first", "second", "third"):
            editor.set_text(word)
            editor.handle_input(DELETE_WORD_BACK)
        editor.set_text("")

        editor.handle_input(YANK)
        editor.handle_input(YANK_POP)
        assert editor.get_text() == "second"

        editor.handle_input("x")
        editor.set_text("")

        editor.handle_input(YANK)
        assert editor.get_text() == "second"

    def test_consecutive_deletions_across_lines_coalesce(self) -> None:
        editor = make_editor()
        editor.set_text("1\n2\n3")

        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "1\n2\n"
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "1\n2"
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "1\n"
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "1"
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == ""

        editor.handle_input(YANK)
        assert editor.get_text() == "1\n2\n3"

    def test_ctrl_k_at_line_end_deletes_the_newline_and_coalesces(self) -> None:
        editor = make_editor()
        editor.set_text("")
        type_text(editor, "ab")
        editor.handle_input("\n")
        type_text(editor, "cd")
        editor.handle_input(UP)
        editor.handle_input(END)

        editor.handle_input(KILL_TO_END)
        assert editor.get_text() == "abcd"
        editor.handle_input(KILL_TO_END)
        assert editor.get_text() == "ab"

        editor.handle_input(YANK)
        assert editor.get_text() == "ab\ncd"

    def test_yank_in_the_middle_of_text(self) -> None:
        editor = make_editor()
        editor.set_text("word")
        editor.handle_input(DELETE_WORD_BACK)
        editor.set_text("hello world")

        editor.handle_input(HOME)
        press(editor, RIGHT, 6)
        editor.handle_input(YANK)
        assert editor.get_text() == "hello wordworld"

    def test_yank_pop_in_the_middle_of_text(self) -> None:
        editor = make_editor()
        editor.set_text("FIRST")
        editor.handle_input(DELETE_WORD_BACK)
        editor.set_text("SECOND")
        editor.handle_input(DELETE_WORD_BACK)

        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)

        editor.handle_input(YANK)
        assert editor.get_text() == "hello SECONDworld"

        editor.handle_input(YANK_POP)
        assert editor.get_text() == "hello FIRSTworld"

    def test_multiline_yank_and_yank_pop_in_the_middle(self) -> None:
        editor = make_editor()
        editor.set_text("SINGLE")
        editor.handle_input(DELETE_WORD_BACK)

        editor.set_text("A\nB")
        press(editor, KILL_TO_START, 3)

        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)

        editor.handle_input(YANK)
        assert editor.get_text() == "hello A\nBworld"

        editor.handle_input(YANK_POP)
        assert editor.get_text() == "hello SINGLEworld"

    def test_alt_d_deletes_word_forward_and_saves_it(self) -> None:
        editor = make_editor()
        editor.set_text("hello world test")
        editor.handle_input(HOME)

        editor.handle_input(DELETE_WORD_FORWARD)
        assert editor.get_text() == " world test"
        editor.handle_input(DELETE_WORD_FORWARD)
        assert editor.get_text() == " test"

        editor.handle_input(YANK)
        assert editor.get_text() == "hello world test"

    def test_alt_d_at_line_end_deletes_the_newline(self) -> None:
        editor = make_editor()
        editor.set_text("line1\nline2")
        editor.handle_input(UP)
        editor.handle_input(END)

        editor.handle_input(DELETE_WORD_FORWARD)
        assert editor.get_text() == "line1line2"

        editor.handle_input(YANK)
        assert editor.get_text() == "line1\nline2"


class TestUndo:
    def test_does_nothing_when_the_stack_is_empty(self) -> None:
        editor = make_editor()
        editor.handle_input(UNDO)
        assert editor.get_text() == ""

    def test_coalesces_consecutive_word_characters(self) -> None:
        editor = make_editor()
        type_text(editor, "hello world")
        assert editor.get_text() == "hello world"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello"
        editor.handle_input(UNDO)
        assert editor.get_text() == ""

    def test_undoes_spaces_one_at_a_time(self) -> None:
        editor = make_editor()
        type_text(editor, "hello  ")
        assert editor.get_text() == "hello  "

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello "
        editor.handle_input(UNDO)
        assert editor.get_text() == "hello"
        editor.handle_input(UNDO)
        assert editor.get_text() == ""

    def test_undoes_newlines(self) -> None:
        editor = make_editor()
        type_text(editor, "hello")
        editor.handle_input("\n")
        type_text(editor, "world")
        assert editor.get_text() == "hello\nworld"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello\n"
        editor.handle_input(UNDO)
        assert editor.get_text() == "hello"
        editor.handle_input(UNDO)
        assert editor.get_text() == ""

    def test_undoes_backspace(self) -> None:
        editor = make_editor()
        type_text(editor, "hello")
        editor.handle_input(BACKSPACE)
        assert editor.get_text() == "hell"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello"

    def test_undoes_forward_delete(self) -> None:
        editor = make_editor()
        type_text(editor, "hello")
        editor.handle_input(HOME)
        editor.handle_input(RIGHT)
        editor.handle_input(DELETE)
        assert editor.get_text() == "hllo"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello"

    def test_undoes_delete_word_backward(self) -> None:
        editor = make_editor()
        type_text(editor, "hello world")
        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == "hello "

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

    def test_undoes_delete_to_line_end(self) -> None:
        editor = make_editor()
        type_text(editor, "hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)

        editor.handle_input(KILL_TO_END)
        assert editor.get_text() == "hello "

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

        editor.handle_input("|")
        assert editor.get_text() == "hello |world"

    def test_undoes_delete_to_line_start(self) -> None:
        editor = make_editor()
        type_text(editor, "hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)

        editor.handle_input(KILL_TO_START)
        assert editor.get_text() == "world"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

    def test_undoes_yank(self) -> None:
        editor = make_editor()
        type_text(editor, "hello ")
        editor.handle_input(DELETE_WORD_BACK)
        editor.handle_input(YANK)
        assert editor.get_text() == "hello "

        editor.handle_input(UNDO)
        assert editor.get_text() == ""

    def test_undoes_single_line_paste_atomically(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 5)

        editor.handle_input(bracketed_paste("beep boop"))
        assert editor.get_text() == "hellobeep boop world"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

        editor.handle_input("|")
        assert editor.get_text() == "hello| world"

    async def test_does_not_trigger_autocomplete_during_a_paste(self) -> None:
        editor = make_editor()
        provider = MockProvider(suggest=_no_suggestions)
        editor.set_autocomplete_provider(provider)

        editor.handle_input(bracketed_paste("look at @node_modules/react/index.js please"))
        await flush_autocomplete()

        assert editor.get_text() == "look at @node_modules/react/index.js please"
        assert provider.calls == 0
        assert editor.is_showing_autocomplete() is False

    def test_decodes_csi_u_ctrl_letters_inside_a_paste(self) -> None:
        editor = make_editor()
        # tmux popups with extended-keys-format=csi-u re-encode \n in pastes as
        # \x1b[106;5u (Ctrl+J).
        editor.handle_input(bracketed_paste("line1\x1b[106;5uline2\x1b[106;5uline3"))
        assert editor.get_text() == "line1\nline2\nline3"

    def test_undoes_multi_line_paste_atomically(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 5)

        editor.handle_input(bracketed_paste("line1\nline2\nline3"))
        assert editor.get_text() == "helloline1\nline2\nline3 world"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

        editor.handle_input("|")
        assert editor.get_text() == "hello| world"

    def test_undoes_insert_text_at_cursor_atomically(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 5)

        editor.insert_text_at_cursor("/tmp/image.png")
        assert editor.get_text() == "hello/tmp/image.png world"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

        editor.handle_input("|")
        assert editor.get_text() == "hello| world"

    def test_insert_text_at_cursor_handles_multiline_text(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 5)

        editor.insert_text_at_cursor("line1\nline2\nline3")
        assert editor.get_text() == "helloline1\nline2\nline3 world"
        assert editor.get_cursor() == (2, 5)

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

    def test_insert_text_at_cursor_normalizes_line_endings(self) -> None:
        editor = make_editor()
        editor.set_text("")

        editor.insert_text_at_cursor("a\r\nb\r\nc")
        assert editor.get_text() == "a\nb\nc"

        editor.handle_input(UNDO)
        assert editor.get_text() == ""

        editor.insert_text_at_cursor("x\ry\rz")
        assert editor.get_text() == "x\ny\nz"

    def test_undoes_set_text_to_empty(self) -> None:
        editor = make_editor()
        type_text(editor, "hello world")
        editor.set_text("")
        assert editor.get_text() == ""

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

    def test_clears_the_undo_stack_on_submit(self) -> None:
        editor = make_editor()
        submitted = ""

        def on_submit(text: str) -> None:
            nonlocal submitted
            submitted = text

        editor.on_submit = on_submit
        type_text(editor, "hello")
        editor.handle_input(ENTER)

        assert submitted == "hello"
        assert editor.get_text() == ""

        editor.handle_input(UNDO)
        assert editor.get_text() == ""

    def test_exits_history_browsing_on_undo(self) -> None:
        editor = make_editor()
        editor.add_to_history("hello")
        assert editor.get_text() == ""

        type_text(editor, "world")
        assert editor.get_text() == "world"

        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == ""

        editor.handle_input(UP)
        assert editor.get_text() == "hello"

        editor.handle_input(UNDO)
        assert editor.get_text() == ""

        editor.handle_input(UNDO)
        assert editor.get_text() == "world"

    def test_undo_restores_pre_history_state_after_several_navigations(self) -> None:
        editor = make_editor()
        for entry in ("first", "second", "third"):
            editor.add_to_history(entry)

        type_text(editor, "current")
        assert editor.get_text() == "current"

        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == ""

        editor.handle_input(UP)
        assert editor.get_text() == "third"
        editor.handle_input(UP)
        assert editor.get_text() == "second"
        editor.handle_input(UP)
        assert editor.get_text() == "first"

        editor.handle_input(UNDO)
        assert editor.get_text() == ""
        editor.handle_input(UNDO)
        assert editor.get_text() == "current"

    def test_cursor_movement_starts_a_new_undo_unit(self) -> None:
        editor = make_editor()
        type_text(editor, "hello world")
        press(editor, LEFT, 5)
        type_text(editor, "lol")
        assert editor.get_text() == "hello lolworld"

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello world"

        editor.handle_input("|")
        assert editor.get_text() == "hello |world"

    def test_no_op_deletes_do_not_push_snapshots(self) -> None:
        editor = make_editor()
        type_text(editor, "hello")
        assert editor.get_text() == "hello"

        editor.handle_input(DELETE_WORD_BACK)
        assert editor.get_text() == ""
        press(editor, DELETE_WORD_BACK, 2)

        editor.handle_input(UNDO)
        assert editor.get_text() == "hello"

    async def test_undoes_autocomplete(self) -> None:
        editor = make_editor()

        def suggest(lines: list[str], _line: int, col: int, _force: bool) -> Suggestions | None:
            if lines[0][:col] == "di":
                return Suggestions([Item("dist/", "dist/")], "di")
            return None

        editor.set_autocomplete_provider(MockProvider(suggest=suggest))

        type_text(editor, "di")
        assert editor.get_text() == "di"

        editor.handle_input(TAB)
        await flush_autocomplete()
        assert editor.get_text() == "dist/"
        assert editor.is_showing_autocomplete() is False

        editor.handle_input(UNDO)
        assert editor.get_text() == "di"


class TestAutocomplete:
    async def test_auto_applies_a_single_force_file_suggestion(self) -> None:
        editor = make_editor()

        def suggest(lines: list[str], _line: int, col: int, force: bool) -> Suggestions | None:
            if not force:
                return None
            if lines[0][:col] == "Work":
                return Suggestions([Item("Workspace/", "Workspace/")], "Work")
            return None

        editor.set_autocomplete_provider(MockProvider(suggest=suggest))

        type_text(editor, "Work")
        assert editor.get_text() == "Work"

        editor.handle_input(TAB)
        await flush_autocomplete()
        assert editor.get_text() == "Workspace/"
        assert editor.is_showing_autocomplete() is False

        editor.handle_input(UNDO)
        assert editor.get_text() == "Work"

    async def test_shows_the_menu_when_force_file_has_several_suggestions(self) -> None:
        editor = make_editor()

        def suggest(lines: list[str], _line: int, col: int, force: bool) -> Suggestions | None:
            if not force:
                return None
            if lines[0][:col] == "src":
                return Suggestions([Item("src/", "src/"), Item("src.txt", "src.txt")], "src")
            return None

        editor.set_autocomplete_provider(MockProvider(suggest=suggest))

        type_text(editor, "src")
        editor.handle_input(TAB)
        await flush_autocomplete()
        assert editor.get_text() == "src"
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(TAB)
        assert editor.get_text() == "src/"
        assert editor.is_showing_autocomplete() is False

    async def test_keeps_suggestions_open_while_typing_in_force_mode(self) -> None:
        editor = make_editor()
        all_files = [
            Item("readme.md", "readme.md"),
            Item("package.json", "package.json"),
            Item("src/", "src/"),
            Item("dist/", "dist/"),
        ]

        def suggest(lines: list[str], _line: int, col: int, force: bool) -> Suggestions | None:
            prefix = lines[0][:col]
            if not (force or "/" in prefix or prefix.startswith(".")):
                return None
            filtered = [f for f in all_files if f.value.lower().startswith(prefix.lower())]
            return Suggestions(filtered, prefix) if filtered else None

        editor.set_autocomplete_provider(MockProvider(suggest=suggest))

        editor.handle_input(TAB)
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input("r")
        await flush_autocomplete()
        assert editor.get_text() == "r"
        assert editor.is_showing_autocomplete() is True

        editor.handle_input("e")
        await flush_autocomplete()
        assert editor.get_text() == "re"
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(TAB)
        assert editor.get_text() == "readme.md"
        assert editor.is_showing_autocomplete() is False

    async def test_debounces_at_autocomplete_while_typing(self) -> None:
        editor = make_editor()
        provider = MockProvider(
            suggest=lambda lines, _line, col, _force: Suggestions(
                [Item("@main.ts", "main.ts")], lines[0][:col]
            )
        )
        editor.set_autocomplete_provider(provider)

        type_text(editor, "@mai")

        assert provider.calls == 0
        assert editor.is_showing_autocomplete() is False

        await asyncio.sleep(0.05)
        await flush_autocomplete()

        assert provider.calls == 1
        assert editor.is_showing_autocomplete() is True

    async def test_debounces_hash_autocomplete_while_typing(self) -> None:
        editor = make_editor()
        provider = MockProvider(
            suggest=lambda lines, _line, col, _force: Suggestions(
                [Item("#2983", "#2983")], lines[0][:col]
            )
        )
        editor.set_autocomplete_provider(provider)

        type_text(editor, "#298")

        assert provider.calls == 0
        assert editor.is_showing_autocomplete() is False

        await asyncio.sleep(0.05)
        await flush_autocomplete()

        assert provider.calls == 1
        assert editor.is_showing_autocomplete() is True

    async def test_aborts_an_active_request_when_typing_continues(self) -> None:
        """The TS listens for the signal's `abort` event; the port polls `.aborted`.

        Same contract either way: the editor must trip the signal of a request
        whose editor state has moved on.
        """
        editor = make_editor()
        aborts = 0
        signals: list[AbortSignal] = []

        async def slow(signal: AbortSignal) -> Suggestions | None:
            nonlocal aborts
            for _ in range(50):
                await asyncio.sleep(0.01)
                if signal.aborted:
                    aborts += 1
                    return None
            return Suggestions([Item("@main.ts", "main.ts")], "@main")

        @dataclass
        class SlowProvider:
            pending: list[asyncio.Task[Suggestions | None]] = field(default_factory=list)

            async def get_suggestions(
                self,
                lines: list[str],
                cursor_line: int,
                cursor_col: int,
                *,
                signal: AbortSignal,
                force: bool = False,
            ) -> Suggestions | None:
                signals.append(signal)
                return await slow(signal)

            def apply_completion(
                self,
                lines: list[str],
                cursor_line: int,
                cursor_col: int,
                item: AutocompleteItem,
                prefix: str,
            ) -> Completion:
                return apply_completion(lines, cursor_line, cursor_col, item, prefix)

        editor.set_autocomplete_provider(SlowProvider())

        type_text(editor, "@mai")
        await asyncio.sleep(0.25)
        editor.handle_input("n")
        await asyncio.sleep(0.1)

        assert aborts == 1

    async def test_hides_autocomplete_when_backspacing_a_slash_command_away(self) -> None:
        editor = make_editor()
        commands = [
            Item("/model", "model", "Change model"),
            Item("/help", "help", "Show help"),
        ]

        def suggest(lines: list[str], _line: int, col: int, _force: bool) -> Suggestions | None:
            prefix = lines[0][:col]
            if prefix.startswith("/"):
                query = prefix[1:]
                filtered = [c for c in commands if c.value.startswith(query)]
                if filtered:
                    return Suggestions(filtered, prefix)
            return None

        editor.set_autocomplete_provider(MockProvider(suggest=suggest))

        editor.handle_input("/")
        await flush_autocomplete()
        assert editor.get_text() == "/"
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(BACKSPACE)
        await flush_autocomplete()
        assert editor.get_text() == ""
        assert editor.is_showing_autocomplete() is False

    async def test_applies_the_exact_typed_argument_on_enter(self) -> None:
        editor = make_editor()
        editor.set_autocomplete_provider(
            argument_provider("argtest", ["one", "two", "three"], prefilter=True)
        )

        type_text(editor, "/argtest two")
        assert editor.get_text() == "/argtest two"
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(ENTER)
        assert editor.get_text() == "/argtest two"

    async def test_selects_the_first_prefix_match_when_not_exact(self) -> None:
        editor = make_editor()
        editor.set_autocomplete_provider(
            argument_provider("argtest", ["two", "three", "twelve"], prefilter=True)
        )

        type_text(editor, "/argtest t")
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(ENTER)
        assert editor.get_text() == "/argtest two"

    async def test_highlights_a_unique_prefix_match_before_the_exact_match(self) -> None:
        editor = make_editor()
        editor.set_autocomplete_provider(
            argument_provider("argtest", ["one", "two", "three"], prefilter=False)
        )

        type_text(editor, "/argtest tw")
        assert editor.get_text() == "/argtest tw"
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(ENTER)
        assert editor.get_text() == "/argtest two"

    async def test_selects_the_first_prefix_match_when_several_match(self) -> None:
        editor = make_editor()
        editor.set_autocomplete_provider(
            argument_provider("argtest", ["one", "two", "three"], prefilter=False)
        )

        type_text(editor, "/argtest t")
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(ENTER)
        assert editor.get_text() == "/argtest two"

    async def test_command_argument_completion_keeps_an_exact_typed_value(self) -> None:
        editor = make_editor()
        editor.set_autocomplete_provider(
            argument_provider("model", ["gpt-4o", "gpt-4o-mini", "claude-sonnet"], prefilter=True)
        )

        type_text(editor, "/model gpt-4o-mini")
        assert editor.get_text() == "/model gpt-4o-mini"
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(ENTER)
        assert editor.get_text() == "/model gpt-4o-mini"


class TestAutocompleteBlockedOn114:
    """The four TS tests that drive `CombinedAutocompleteProvider` (step 1.14)."""

    @pytest.mark.parametrize(
        "ts_test",
        [
            "awaits async slash command argument completions",
            "ignores invalid slash command argument completion results",
            "does not show argument completions when command has no argument completer",
            "auto-applies single force-file suggestion via shouldTriggerFileCompletion",
        ],
    )
    def test_unported(self, ts_test: str) -> None:
        pytest.skip(f"needs autocomplete.ts (step 1.14): {ts_test}")


class TestCharacterJump:
    def test_jumps_forward_to_the_first_occurrence_on_the_line(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        assert editor.get_cursor() == (0, 0)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("o")
        assert editor.get_cursor() == (0, 4)

    def test_jumps_forward_to_the_next_occurrence_after_the_cursor(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)
        press(editor, RIGHT, 4)
        assert editor.get_cursor() == (0, 4)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("o")
        assert editor.get_cursor() == (0, 7)

    def test_jumps_forward_across_lines(self) -> None:
        editor = make_editor()
        editor.set_text("abc\ndef\nghi")
        press(editor, UP, 2)
        editor.handle_input(HOME)
        assert editor.get_cursor() == (0, 0)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("g")
        assert editor.get_cursor() == (2, 0)

    def test_jumps_backward_on_the_same_line(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        assert editor.get_cursor() == (0, 11)

        editor.handle_input(JUMP_BACKWARD)
        editor.handle_input("o")
        assert editor.get_cursor() == (0, 7)

    def test_jumps_backward_across_lines(self) -> None:
        editor = make_editor()
        editor.set_text("abc\ndef\nghi")
        assert editor.get_cursor() == (2, 3)

        editor.handle_input(JUMP_BACKWARD)
        editor.handle_input("a")
        assert editor.get_cursor() == (0, 0)

    def test_does_nothing_when_the_character_is_not_found_forward(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("z")
        assert editor.get_cursor() == (0, 0)

    def test_does_nothing_when_the_character_is_not_found_backward(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")

        editor.handle_input(JUMP_BACKWARD)
        editor.handle_input("z")
        assert editor.get_cursor() == (0, 11)

    def test_is_case_sensitive(self) -> None:
        editor = make_editor()
        editor.set_text("Hello World")
        editor.handle_input(HOME)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("h")
        assert editor.get_cursor() == (0, 0)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("W")
        assert editor.get_cursor() == (0, 6)

    def test_cancels_jump_mode_when_the_hotkey_is_pressed_again(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input(JUMP_FORWARD)

        editor.handle_input("o")
        assert editor.get_text() == "ohello world"

    def test_cancels_jump_mode_on_escape_and_processes_the_escape(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("\x1b")
        assert editor.get_cursor() == (0, 0)

        editor.handle_input("o")
        assert editor.get_text() == "ohello world"

    def test_cancels_backward_jump_mode_when_pressed_again(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")

        editor.handle_input(JUMP_BACKWARD)
        editor.handle_input(JUMP_BACKWARD)

        editor.handle_input("o")
        assert editor.get_text() == "hello worldo"

    def test_searches_for_special_characters(self) -> None:
        editor = make_editor()
        editor.set_text("foo(bar) = baz;")
        editor.handle_input(HOME)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("(")
        assert editor.get_cursor() == (0, 3)

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("=")
        assert editor.get_cursor() == (0, 9)

    def test_handles_empty_text(self) -> None:
        editor = make_editor()
        editor.set_text("")

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("x")
        assert editor.get_cursor() == (0, 0)

    def test_resets_last_action_when_jumping(self) -> None:
        editor = make_editor()
        editor.set_text("hello world")
        editor.handle_input(HOME)

        editor.handle_input("x")
        assert editor.get_text() == "xhello world"

        editor.handle_input(JUMP_FORWARD)
        editor.handle_input("o")

        editor.handle_input("Y")
        assert editor.get_text() == "xhellYo world"

        editor.handle_input(UNDO)
        assert editor.get_text() == "xhello world"


def position_cursor(editor: Editor, line: int, col: int) -> None:
    """The TS `positionCursor` helper."""
    press(editor, UP, 20)
    press(editor, DOWN, line)
    editor.handle_input(HOME)
    press(editor, RIGHT, col)


class TestStickyColumn:
    def test_preserves_the_target_column_moving_up_through_a_shorter_line(self) -> None:
        editor = make_editor()
        editor.set_text("2222222222x222\n\n1111111111_111111111111")

        assert editor.get_cursor() == (2, 23)
        editor.handle_input(HOME)
        press(editor, RIGHT, 10)
        assert editor.get_cursor() == (2, 10)

        editor.handle_input(UP)
        assert editor.get_cursor() == (1, 0)

        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 10)

    def test_preserves_the_target_column_moving_down_through_a_shorter_line(self) -> None:
        editor = make_editor()
        editor.set_text("1111111111_111\n\n2222222222x222222222222")

        press(editor, UP, 2)
        editor.handle_input(HOME)
        press(editor, RIGHT, 10)
        assert editor.get_cursor() == (0, 10)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 0)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (2, 10)

    def test_resets_on_left_arrow(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 5)
        assert editor.get_cursor() == (2, 5)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 5)

        editor.handle_input(LEFT)
        assert editor.get_cursor() == (0, 4)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 4)

    def test_resets_on_right_arrow(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        press(editor, UP, 2)
        editor.handle_input(HOME)
        press(editor, RIGHT, 5)
        assert editor.get_cursor() == (0, 5)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 5)

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (2, 6)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 6)

    def test_resets_on_typing(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 8)
        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 8)

        editor.handle_input("X")
        assert editor.get_cursor() == (0, 9)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 9)

    def test_resets_on_backspace(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 8)
        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 8)

        editor.handle_input(BACKSPACE)
        assert editor.get_cursor() == (0, 7)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 7)

    def test_resets_on_move_to_line_start(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 8)
        editor.handle_input(UP)

        editor.handle_input(HOME)
        assert editor.get_cursor() == (1, 0)

        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 0)

    def test_resets_on_move_to_line_end(self) -> None:
        editor = make_editor()
        editor.set_text("12345\n\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 3)
        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 3)

        editor.handle_input(END)
        assert editor.get_cursor() == (0, 5)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 5)

    def test_resets_on_word_movement_left(self) -> None:
        editor = make_editor()
        editor.set_text("hello world\n\nhello world")
        assert editor.get_cursor() == (2, 11)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 11)

        editor.handle_input(WORD_LEFT)
        assert editor.get_cursor() == (0, 6)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 6)

    def test_resets_on_word_movement_right(self) -> None:
        editor = make_editor()
        editor.set_text("hello world\n\nhello world")

        press(editor, UP, 2)
        editor.handle_input(HOME)
        assert editor.get_cursor() == (0, 0)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 0)

        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (2, 5)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 5)

    def test_resets_on_undo(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        press(editor, UP, 2)
        editor.handle_input(HOME)
        press(editor, RIGHT, 8)
        assert editor.get_cursor() == (0, 8)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 8)

        editor.handle_input("X")
        assert editor.get_text() == "1234567890\n\n12345678X90"
        assert editor.get_cursor() == (2, 9)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 9)

        editor.handle_input(UNDO)
        assert editor.get_text() == "1234567890\n\n1234567890"
        assert editor.get_cursor() == (2, 8)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 8)

    def test_handles_multiple_consecutive_vertical_movements(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\nab\ncd\nef\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 7)
        assert editor.get_cursor() == (4, 7)

        press(editor, UP, 4)
        assert editor.get_cursor() == (0, 7)

        press(editor, DOWN, 4)
        assert editor.get_cursor() == (4, 7)

    def test_moves_through_wrapped_visual_lines_without_getting_stuck(self) -> None:
        editor = make_editor(15, 24)
        editor.set_text("short\n123456789012345678901234567890")
        editor.render(15)

        assert editor.get_cursor() == (1, 30)

        editor.handle_input(UP)
        assert editor.get_cursor()[0] == 1
        editor.handle_input(UP)
        assert editor.get_cursor()[0] == 1
        editor.handle_input(UP)
        assert editor.get_cursor()[0] == 0

    def test_set_text_resets_the_sticky_column(self) -> None:
        editor = make_editor()
        editor.set_text("1234567890\n\n1234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 8)
        editor.handle_input(UP)

        editor.set_text("abcdefghij\n\nabcdefghij")
        assert editor.get_cursor() == (2, 10)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 10)

    def test_right_at_end_of_the_last_line_sets_the_preferred_column(self) -> None:
        editor = make_editor()
        editor.set_text("111111111x1111111111\n\n333333333_")

        press(editor, UP, 2)
        editor.handle_input(END)
        assert editor.get_cursor() == (0, 20)

        press(editor, DOWN, 2)
        assert editor.get_cursor() == (2, 10)

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (2, 10)

        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 10)

    def test_handles_resizes_when_preferred_col_is_on_the_same_line(self) -> None:
        editor = make_editor(80, 24)
        editor.set_text("12345678901234567890\n\n12345678901234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 15)
        press(editor, UP, 2)
        assert editor.get_cursor() == (0, 15)

        editor.render(12)

        press(editor, DOWN, 2)
        assert editor.get_cursor()[1] == 4

    def test_handles_resizes_when_preferred_col_is_on_a_different_line(self) -> None:
        editor = make_editor(80, 24)
        editor.set_text("short\n12345678901234567890")

        editor.handle_input(HOME)
        press(editor, RIGHT, 15)
        assert editor.get_cursor() == (1, 15)

        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 5)

        editor.render(10)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 8)

        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 5)

        editor.render(80)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 15)

    def test_rewrapped_lines_target_fits_the_current_visual_column(self) -> None:
        editor = make_editor(80, 24)
        editor.set_text("abcdefghijklmnopqr\n123456789012345678")

        position_cursor(editor, 0, 18)
        assert editor.get_cursor() == (0, 18)

        editor.render(10)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 8)

        editor.render(80)
        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 8)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 8)

    def test_rewrapped_lines_target_shorter_than_the_current_visual_column(self) -> None:
        editor = make_editor(80, 24)
        editor.set_text("abcdefghijklmnopqr\n123456789012345678\nab")

        position_cursor(editor, 0, 18)
        assert editor.get_cursor() == (0, 18)

        editor.render(10)
        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 8)

        editor.render(80)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (2, 2)

        editor.handle_input(UP)
        assert editor.get_cursor() == (1, 8)


class TestPasteMarkerAtomicBehaviour:
    def test_creates_a_marker_for_large_pastes(self) -> None:
        editor = make_editor()
        assert MARKER_RE.search(paste_with_marker(editor))

    def test_right_arrow_treats_the_marker_as_one_unit(self) -> None:
        editor = make_editor()
        editor.handle_input("A")
        paste_with_marker(editor)
        editor.handle_input("B")

        editor.handle_input(HOME)
        assert editor.get_cursor() == (0, 0)

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, 1)

        editor.handle_input(RIGHT)
        marker = MARKER_RE.search(editor.get_text())
        assert marker is not None
        assert editor.get_cursor() == (0, 1 + len(marker.group(0)))

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, 1 + len(marker.group(0)) + 1)

    def test_left_arrow_treats_the_marker_as_one_unit(self) -> None:
        editor = make_editor()
        editor.handle_input("A")
        paste_with_marker(editor)
        editor.handle_input("B")

        editor.handle_input(LEFT)
        marker = MARKER_RE.search(editor.get_text())
        assert marker is not None
        assert editor.get_cursor() == (0, 1 + len(marker.group(0)))

        editor.handle_input(LEFT)
        assert editor.get_cursor() == (0, 1)

        editor.handle_input(LEFT)
        assert editor.get_cursor() == (0, 0)

    def test_backspace_treats_the_marker_as_one_unit(self) -> None:
        editor = make_editor()
        editor.handle_input("A")
        paste_with_marker(editor)
        editor.handle_input("B")

        marker = MARKER_RE.search(editor.get_text())
        assert marker is not None

        editor.handle_input(HOME)
        press(editor, RIGHT, 2)
        assert editor.get_cursor() == (0, 1 + len(marker.group(0)))

        editor.handle_input(BACKSPACE)
        assert editor.get_text() == "AB"
        assert editor.get_cursor() == (0, 1)

    def test_forward_delete_treats_the_marker_as_one_unit(self) -> None:
        editor = make_editor()
        editor.handle_input("A")
        paste_with_marker(editor)
        editor.handle_input("B")

        editor.handle_input(HOME)
        editor.handle_input(RIGHT)

        editor.handle_input(DELETE)
        assert editor.get_text() == "AB"
        assert editor.get_cursor() == (0, 1)

    def test_word_movement_treats_the_marker_as_one_unit(self) -> None:
        editor = make_editor()
        editor.handle_input("X")
        editor.handle_input(" ")
        paste_with_marker(editor)
        editor.handle_input(" ")
        editor.handle_input("Y")

        marker = MARKER_RE.search(editor.get_text())
        assert marker is not None

        editor.handle_input(HOME)

        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (0, 1)

        editor.handle_input(WORD_RIGHT)
        assert editor.get_cursor() == (0, 2 + len(marker.group(0)))

    def test_undo_restores_the_marker_after_a_backspace(self) -> None:
        editor = make_editor()
        editor.handle_input("A")
        paste_with_marker(editor)
        editor.handle_input("B")

        text_before = editor.get_text()

        editor.handle_input(HOME)
        press(editor, RIGHT, 2)
        editor.handle_input(BACKSPACE)
        assert editor.get_text() == "AB"

        editor.handle_input(UNDO)
        assert editor.get_text() == text_before

    def test_handles_multiple_markers_on_one_line(self) -> None:
        editor = make_editor()
        paste_with_marker(editor)
        editor.handle_input(" ")
        paste_with_marker(editor)

        markers = MARKER_RE.findall(editor.get_text())
        assert len(markers) == 2
        first, second = (len(m) for m in markers)

        editor.handle_input(HOME)

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, first)

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, first + 1)

        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, first + 1 + second)

    def test_manually_typed_marker_text_is_not_atomic(self) -> None:
        editor = make_editor()
        fake_marker = "[paste #99 +5 lines]"
        type_text(editor, fake_marker)
        assert editor.get_text() == fake_marker

        editor.handle_input(HOME)
        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, 1)

    def test_does_not_crash_when_the_marker_is_wider_than_the_width(self) -> None:
        editor = make_editor()
        editor.handle_input(bracketed_paste(("line\n" * 47).rstrip()))

        marker = MARKER_RE.search(editor.get_text())
        assert marker is not None
        assert visible_width(marker.group(0)) > 8

        for line in editor.render(8):
            assert visible_width(line) <= 8, f"line exceeds width 8: {line!r}"

    def test_does_not_crash_with_the_cursor_on_a_wrapped_marker(self) -> None:
        editor = make_editor()
        type_text(editor, "b" * 35)
        editor.handle_input(bracketed_paste(("line\n" * 27).rstrip()))
        type_text(editor, "b" * 4)

        press(editor, LEFT, 5)

        for line in editor.render(54):
            assert visible_width(line) <= 54, f"line exceeds width 54: {line!r}"

    def test_word_wrap_rechecks_overflow_after_backtracking(self) -> None:
        editor = make_editor()
        editor.handle_input(" ")
        type_text(editor, "b" * 35)
        editor.handle_input(bracketed_paste(("line\n" * 27).rstrip()))
        type_text(editor, "b" * 4)

        for line in editor.render(54):
            assert visible_width(line) <= 54, f"line exceeds width 54: {line!r}"

    def test_expands_large_pasted_content_literally(self) -> None:
        editor = make_editor()
        pasted = "\n".join(
            [
                *(f"line {i}" for i in range(1, 11)),
                "tokens $1 $2 $& $$ $` $' end",
            ]
        )
        editor.handle_input(bracketed_paste(pasted))

        assert MARKER_RE.search(editor.get_text())
        assert editor.get_expanded_text() == pasted

    def test_snaps_to_the_marker_start_when_navigating_down_into_it(self) -> None:
        editor = make_editor()
        editor.set_text("12345678901234567890\n\nhello ")

        editor.handle_input(bracketed_paste("x" * 2000))
        editor.render(80)

        press(editor, UP, 2)
        editor.handle_input(HOME)
        press(editor, RIGHT, 10)
        assert editor.get_cursor() == (0, 10)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 0)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (2, 6)

    def test_preserves_the_sticky_column_through_a_marker_line(self) -> None:
        editor = make_editor(30, 24)

        type_text(editor, "1234567890123456")
        editor.handle_input("\n")
        editor.handle_input("\n")
        editor.handle_input(bracketed_paste("x" * 2000))
        editor.handle_input("\n")
        editor.handle_input("\n")
        type_text(editor, "abcdefghijklmnop")
        editor.render(30)

        press(editor, UP, 4)
        editor.handle_input(HOME)
        press(editor, RIGHT, 10)
        assert editor.get_cursor() == (0, 10)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 0)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (2, 0)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (3, 0)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (4, 10)

    def test_does_not_get_stuck_moving_down_from_a_multi_line_marker(self) -> None:
        editor = make_editor(20, 24)

        type_text(editor, "abcdefgh")
        editor.handle_input(bracketed_paste(("line\n" * 100).rstrip()))
        type_text(editor, "ijklmnopqr")
        editor.handle_input("\n")
        type_text(editor, "123456789012345678")
        editor.render(20)

        marker = re.search(r"\[paste #\d+ \+\d+ lines]", editor.get_text())
        assert marker is not None
        marker_len = len(marker.group(0))
        assert marker_len > 20
        marker_start = 8
        marker_end = marker_start + marker_len

        editor.handle_input(UP)
        editor.handle_input(HOME)
        press(editor, RIGHT, 6)
        assert editor.get_cursor() == (0, 6)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (0, marker_start)

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (0, marker_end)

        editor.handle_input(UP)
        assert editor.get_cursor() == (0, marker_start)

        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 6)

    def test_skips_marker_continuation_lines_when_preferred_col_is_in_the_tail(self) -> None:
        editor = make_editor(20, 24)

        type_text(editor, "abcdefgh")
        editor.handle_input(bracketed_paste(("line\n" * 100).rstrip()))
        type_text(editor, "ijklmnopqr")
        editor.handle_input("\n")
        type_text(editor, "123456789012345678")
        editor.render(20)

        editor.handle_input(UP)
        editor.handle_input(HOME)
        press(editor, RIGHT, 3)
        assert editor.get_cursor() == (0, 3)

        editor.handle_input(DOWN)
        assert editor.get_cursor()[1] == 8

        editor.handle_input(DOWN)
        assert editor.get_cursor() == (1, 3)

        editor.handle_input(UP)
        assert editor.get_cursor()[1] == 8
        editor.handle_input(UP)
        assert editor.get_cursor() == (0, 3)

    def test_submits_large_pasted_content_literally(self) -> None:
        editor = make_editor()
        pasted = "\n".join(
            [
                *(f"line {i}" for i in range(1, 11)),
                "tokens $1 $2 $& $$ $` $' end",
            ]
        )
        submitted = ""

        def on_submit(text: str) -> None:
            nonlocal submitted
            submitted = text

        editor.on_submit = on_submit
        editor.handle_input(bracketed_paste(pasted))
        editor.handle_input(ENTER)

        assert submitted == pasted


class TestMutationGaps:
    """Behaviours the first mutation run showed nothing was watching.

    Each of these exists because breaking the corresponding line in `editor.py`
    left the whole corpus and the ported TS tests green. They have no TS
    counterpart — the TS suite has the same blind spots.
    """

    def test_cursor_marker_is_emitted_only_when_focused(self) -> None:
        # `CURSOR_MARKER` is a zero-width APC string, so it is invisible on the
        # parity Surface: focus can only be checked on the raw line.
        editor = make_editor()
        editor.set_text("hi")

        assert CURSOR_MARKER not in "".join(editor.render(20))

        editor.focused = True
        assert CURSOR_MARKER in "".join(editor.render(20))

    def test_marker_text_for_another_paste_id_is_not_atomic(self) -> None:
        # The valid-id check only runs once *some* paste exists; with none, the
        # fast path in `_segment_with_markers` returns first.
        editor = make_editor()
        paste_with_marker(editor)
        real = MARKER_RE.search(editor.get_text())
        assert real is not None

        type_text(editor, " [paste #99 +5 lines]")

        editor.handle_input(HOME)
        editor.handle_input(RIGHT)
        assert editor.get_cursor() == (0, len(real.group(0))), "the real marker is atomic"

        press(editor, RIGHT, 2)
        assert editor.get_cursor() == (0, len(real.group(0)) + 2), (
            "id 99 has no paste, so its marker steps one grapheme at a time"
        )

    def test_set_text_expands_tabs_to_four_spaces(self) -> None:
        editor = make_editor()
        editor.set_text("a\tb")
        assert editor.get_text() == "a    b"

    def test_pasted_tabs_expand_to_four_spaces(self) -> None:
        editor = make_editor()
        editor.handle_input(bracketed_paste("a\tb"))
        assert editor.get_text() == "a    b"

    def test_history_holds_one_entry_per_distinct_consecutive_prompt(self) -> None:
        # Asserting the *text* after each Up cannot tell one "same" from two;
        # walking back down can.
        editor = make_editor()
        editor.add_to_history("same")
        editor.add_to_history("same")
        editor.add_to_history("x")

        press(editor, UP, 3)
        assert editor.get_text() == "same"

        editor.handle_input(DOWN)
        assert editor.get_text() == "x", "there is only one 'same' to walk back through"

    async def test_exact_match_beats_an_earlier_prefix_match(self) -> None:
        editor = make_editor()
        editor.set_autocomplete_provider(
            argument_provider("argtest", ["twoify", "two"], prefilter=False)
        )

        type_text(editor, "/argtest two")
        await flush_autocomplete()
        assert editor.is_showing_autocomplete() is True

        editor.handle_input(ENTER)
        assert editor.get_text() == "/argtest two"

    async def test_slash_completion_is_first_line_only(self) -> None:
        editor = make_editor()
        provider = MockProvider(
            suggest=lambda lines, line, col, _force: Suggestions(
                [Item("/model", "model")], lines[line][:col]
            )
        )
        editor.set_autocomplete_provider(provider)

        editor.handle_input("\n")
        editor.handle_input("/")
        await flush_autocomplete()

        assert editor.get_text() == "\n/"
        assert provider.calls == 0
        assert editor.is_showing_autocomplete() is False

    async def test_symbol_completion_is_debounced_not_merely_slow(self) -> None:
        editor = make_editor()
        provider = MockProvider(
            suggest=lambda lines, _line, col, _force: Suggestions(
                [Item("@main.ts", "main.ts")], lines[0][:col]
            )
        )
        editor.set_autocomplete_provider(provider)

        type_text(editor, "@mai")
        # Draining the loop is not enough: the request is behind a timer.
        await flush_autocomplete()
        assert provider.calls == 0

        await asyncio.sleep(0.05)
        await flush_autocomplete()
        assert provider.calls == 1

    async def test_a_response_for_a_moved_cursor_is_discarded(self) -> None:
        editor = make_editor()
        started = asyncio.Event()
        release = asyncio.Event()

        @dataclass
        class SlowProvider:
            async def get_suggestions(
                self,
                lines: list[str],
                cursor_line: int,
                cursor_col: int,
                *,
                signal: AbortSignal,
                force: bool = False,
            ) -> Suggestions | None:
                started.set()
                await release.wait()
                return Suggestions([Item("@main.ts", "main.ts")], "@m")

            def apply_completion(
                self,
                lines: list[str],
                cursor_line: int,
                cursor_col: int,
                item: AutocompleteItem,
                prefix: str,
            ) -> Completion:
                return apply_completion(lines, cursor_line, cursor_col, item, prefix)

        editor.set_autocomplete_provider(SlowProvider())

        type_text(editor, "@m")
        await asyncio.wait_for(started.wait(), timeout=1)

        # Moving the cursor does not cancel the request, but it does invalidate
        # the snapshot the response was asked for.
        editor.handle_input(LEFT)
        release.set()
        await flush_autocomplete()

        assert editor.is_showing_autocomplete() is False
