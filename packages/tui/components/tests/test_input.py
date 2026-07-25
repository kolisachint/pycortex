# pyright: reportPrivateUsage=false
# The cursor index and kill-ring/undo state have no public surface; asserting on
# them is how the editing semantics get pinned.
"""Input state a rendered frame cannot show.

The frames themselves — prompt, fake cursor, horizontal scrolling, the focused
cursor marker — are pinned by the 24 `component/input-*` scenarios in
`packages/tui/testkit`, which diff whole screens against the real TypeScript.
What is left is the cursor index, kill ring, undo coalescing and callbacks.
"""

from __future__ import annotations

import pytest
from cortex.tui.components import Input

# Key encodings, from the TS keybinding defaults.
LEFT = "\x1b[D"
RIGHT = "\x1b[C"
HOME = "\x01"  # ctrl+a
END = "\x05"  # ctrl+e
BACKSPACE = "\x7f"
FORWARD_DELETE = "\x04"  # ctrl+d
KILL_TO_END = "\x0b"  # ctrl+k
KILL_TO_START = "\x15"  # ctrl+u
KILL_WORD_BACK = "\x17"  # ctrl+w
KILL_WORD_FORWARD = "\x1bd"  # alt+d
YANK = "\x19"  # ctrl+y
YANK_POP = "\x1by"  # alt+y
UNDO = "\x1f"  # ctrl+-
WORD_LEFT = "\x1bb"
WORD_RIGHT = "\x1bf"
SUBMIT = "\r"
ESCAPE = "\x1b"


def typed(component: Input, text: str) -> None:
    for char in text:
        component.handle_input(char)


def send(component: Input, *keys: str) -> None:
    for key in keys:
        component.handle_input(key)


class TestTyping:
    def test_characters_accumulate_and_move_the_cursor(self) -> None:
        component = Input()
        typed(component, "hello")
        assert component.get_value() == "hello"
        assert component._cursor == 5

    def test_insertion_happens_at_the_cursor(self) -> None:
        component = Input()
        typed(component, "helo")
        send(component, LEFT)
        typed(component, "l")
        assert component.get_value() == "hello"

    def test_control_characters_are_rejected(self) -> None:
        component = Input()
        typed(component, "ab")
        send(component, "\x00", "\x1e", "\x7f\x7f")
        assert "\x00" not in component.get_value()
        assert "\x1e" not in component.get_value()

    def test_unicode_is_accepted(self) -> None:
        component = Input()
        typed(component, "héllo 世界")
        assert component.get_value() == "héllo 世界"

    def test_set_value_clamps_the_cursor(self) -> None:
        component = Input()
        typed(component, "longer text")
        component.set_value("ab")
        assert component._cursor == 2


class TestCursorMovement:
    def test_left_and_right(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, LEFT, LEFT)
        assert component._cursor == 1
        send(component, RIGHT)
        assert component._cursor == 2

    def test_movement_clamps_at_both_ends(self) -> None:
        component = Input()
        typed(component, "ab")
        send(component, LEFT, LEFT, LEFT, LEFT)
        assert component._cursor == 0
        send(component, RIGHT, RIGHT, RIGHT, RIGHT)
        assert component._cursor == 2

    def test_home_and_end(self) -> None:
        component = Input()
        typed(component, "hello")
        send(component, HOME)
        assert component._cursor == 0
        send(component, END)
        assert component._cursor == 5

    def test_movement_steps_over_whole_grapheme_clusters(self) -> None:
        component = Input()
        # Decomposed: one cluster, two code points. Landing between them would
        # split the character.
        component.set_value("éx")
        send(component, HOME, RIGHT)
        assert component._cursor == 2

    def test_word_left_skips_whitespace_then_the_word(self) -> None:
        component = Input()
        typed(component, "alpha beta gamma")
        send(component, WORD_LEFT)
        assert component._cursor == len("alpha beta ")
        send(component, WORD_LEFT)
        assert component._cursor == len("alpha ")

    def test_word_right(self) -> None:
        component = Input()
        typed(component, "alpha beta")
        send(component, HOME, WORD_RIGHT)
        assert component._cursor == len("alpha")

    def test_word_motion_treats_punctuation_as_its_own_run(self) -> None:
        component = Input()
        typed(component, "foo.bar")
        send(component, WORD_LEFT)
        assert component._cursor == len("foo.")
        send(component, WORD_LEFT)
        assert component._cursor == len("foo")


class TestDeletion:
    def test_backspace(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, BACKSPACE)
        assert component.get_value() == "ab"

    def test_backspace_at_the_start_is_a_noop(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, HOME, BACKSPACE)
        assert component.get_value() == "abc"

    def test_backspace_removes_a_whole_grapheme_cluster(self) -> None:
        component = Input()
        component.set_value("é")
        component._cursor = 2
        send(component, BACKSPACE)
        assert component.get_value() == ""

    def test_forward_delete(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, HOME, FORWARD_DELETE)
        assert component.get_value() == "bc"
        assert component._cursor == 0

    def test_forward_delete_at_the_end_is_a_noop(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, FORWARD_DELETE)
        assert component.get_value() == "abc"


class TestKillRing:
    def test_kill_to_line_end_then_yank(self) -> None:
        component = Input()
        typed(component, "hello world")
        send(component, HOME, KILL_TO_END)
        assert component.get_value() == ""
        send(component, YANK)
        assert component.get_value() == "hello world"

    def test_kill_to_line_start(self) -> None:
        component = Input()
        typed(component, "hello world")
        send(component, KILL_TO_START)
        assert component.get_value() == ""
        send(component, YANK)
        assert component.get_value() == "hello world"

    def test_kill_word_backward(self) -> None:
        component = Input()
        typed(component, "alpha beta")
        send(component, KILL_WORD_BACK)
        assert component.get_value() == "alpha "
        send(component, YANK)
        assert component.get_value() == "alpha beta"

    def test_kill_word_forward(self) -> None:
        component = Input()
        typed(component, "alpha beta")
        send(component, HOME, KILL_WORD_FORWARD)
        assert component.get_value() == " beta"

    def test_consecutive_kills_accumulate_into_one_entry(self) -> None:
        component = Input()
        typed(component, "one two three")
        send(component, KILL_WORD_BACK, KILL_WORD_BACK)
        assert component.get_value() == "one "
        send(component, YANK)
        # Both kills came back together, in the original order.
        assert component.get_value() == "one two three"

    def test_a_non_kill_between_kills_starts_a_new_entry(self) -> None:
        component = Input()
        typed(component, "one two")
        send(component, KILL_WORD_BACK, LEFT, KILL_WORD_BACK)
        send(component, YANK)
        assert "one" in component.get_value()

    def test_yank_pop_cycles_through_the_ring(self) -> None:
        component = Input()
        typed(component, "first")
        send(component, KILL_TO_START)
        typed(component, "second")
        send(component, KILL_TO_START, YANK)
        assert component.get_value() == "second"
        send(component, YANK_POP)
        assert component.get_value() == "first"

    def test_yank_pop_only_follows_a_yank(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, KILL_TO_START, YANK_POP)
        assert component.get_value() == ""

    def test_yank_with_an_empty_ring_is_a_noop(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, YANK)
        assert component.get_value() == "abc"


class TestUndo:
    def test_undo_reverts_the_last_edit(self) -> None:
        component = Input()
        typed(component, "hello")
        send(component, BACKSPACE)
        assert component.get_value() == "hell"
        send(component, UNDO)
        assert component.get_value() == "hello"

    def test_a_word_run_coalesces_into_one_undo_unit(self) -> None:
        component = Input()
        typed(component, "hello world")
        send(component, UNDO)
        # Whitespace snapshots *before* inserting itself, and then still sets
        # lastAction to "type-word" — so the space and everything after it are
        # one unit, and undo lands on "hello", not "hello ". Confirmed against
        # the TS by the component/input-undo golden.
        assert component.get_value() == "hello"

    def test_each_typing_run_is_its_own_undo_step(self) -> None:
        component = Input()
        typed(component, "one two three")
        send(component, UNDO)
        assert component.get_value() == "one two"
        send(component, UNDO)
        assert component.get_value() == "one"

    def test_undo_restores_the_cursor_too(self) -> None:
        component = Input()
        typed(component, "abc")
        send(component, HOME, FORWARD_DELETE)
        send(component, UNDO)
        assert component.get_value() == "abc"
        assert component._cursor == 0

    def test_undo_on_an_empty_stack_is_a_noop(self) -> None:
        component = Input()
        send(component, UNDO)
        assert component.get_value() == ""


class TestPaste:
    def test_bracketed_paste_inserts_at_the_cursor(self) -> None:
        component = Input()
        typed(component, "ab")
        send(component, HOME)
        send(component, "\x1b[200~XY\x1b[201~")
        assert component.get_value() == "XYab"

    def test_newlines_are_dropped_and_tabs_widened(self) -> None:
        component = Input()
        send(component, "\x1b[200~one\ntwo\r\nthree\tfour\x1b[201~")
        assert component.get_value() == "onetwothree    four"

    def test_a_paste_split_across_chunks_is_buffered(self) -> None:
        component = Input()
        send(component, "\x1b[200~par")
        assert component.get_value() == "", "nothing lands until the end marker"
        send(component, "tial\x1b[201~")
        assert component.get_value() == "partial"

    def test_input_after_the_end_marker_is_processed(self) -> None:
        component = Input()
        send(component, "\x1b[200~pasted\x1b[201~z")
        assert component.get_value() == "pastedz"

    def test_a_paste_is_a_single_undo_unit(self) -> None:
        component = Input()
        typed(component, "a")
        send(component, "\x1b[200~lots of text\x1b[201~", UNDO)
        assert component.get_value() == "a"


class TestCallbacks:
    def test_submit_reports_the_current_value(self) -> None:
        component = Input()
        submitted: list[str] = []
        component.on_submit = submitted.append
        typed(component, "done")
        send(component, SUBMIT)
        assert submitted == ["done"]
        assert component.get_value() == "done", "submit does not clear the field"

    def test_bare_newline_also_submits(self) -> None:
        component = Input()
        submitted: list[str] = []
        component.on_submit = submitted.append
        typed(component, "x")
        send(component, "\n")
        assert submitted == ["x"]

    def test_escape_fires_on_escape(self) -> None:
        component = Input()
        calls: list[int] = []
        component.on_escape = lambda: calls.append(1)
        send(component, ESCAPE)
        assert calls == [1]

    def test_callbacks_are_optional(self) -> None:
        component = Input()
        send(component, SUBMIT, ESCAPE)  # must not raise
        assert component.get_value() == ""


class TestKittyInput:
    def test_a_csi_u_printable_is_inserted(self) -> None:
        component = Input()
        # \x1b[97u is 'a' under the Kitty protocol; the raw bytes contain ESC and
        # would otherwise be rejected as control characters.
        send(component, "\x1b[97u")
        assert component.get_value() == "a"


class TestRender:
    def test_prompt_and_fake_cursor(self) -> None:
        component = Input()
        typed(component, "hi")
        line = component.render(20)[0]
        assert line.startswith("> hi")
        assert "\x1b[7m" in line, "the cursor is drawn with reverse video"

    def test_the_cursor_marker_appears_only_when_focused(self) -> None:
        from cortex.tui.render import CURSOR_MARKER

        component = Input()
        typed(component, "hi")
        assert CURSOR_MARKER not in component.render(20)[0]
        component.focused = True
        assert CURSOR_MARKER in component.render(20)[0]

    @pytest.mark.parametrize("width", [1, 2, 3])
    def test_degenerate_widths_do_not_crash(self, width: int) -> None:
        component = Input()
        typed(component, "hello")
        assert len(component.render(width)) == 1

    def test_the_line_never_exceeds_the_width(self) -> None:
        from cortex.tui.util import visible_width

        component = Input()
        component.set_value("the quick brown fox jumps over the lazy dog")
        for width in (10, 20, 40, 80):
            line = component.render(width)[0]
            assert visible_width(line) <= width, f"overflow at width {width}"
