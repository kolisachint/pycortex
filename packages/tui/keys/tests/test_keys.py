"""Tests for `cortex.tui.keys` (port of keys.ts/keybindings.ts tests)."""

from __future__ import annotations

from cortex.tui.keys import (
    KeybindingsManager,
    get_keybindings,
    is_kitty_protocol_active,
    matches_key,
    parse_key,
    set_keybindings,
    set_kitty_protocol_active,
)


def test_set_kitty_protocol_active() -> None:
    set_kitty_protocol_active(True)
    assert is_kitty_protocol_active()
    set_kitty_protocol_active(False)
    assert not is_kitty_protocol_active()


def test_parse_key_legacy() -> None:
    assert parse_key("a") == "a"
    assert parse_key("A") == "A"
    assert parse_key("\r") == "enter"
    assert parse_key("\t") == "tab"
    assert parse_key(" ") == "space"
    assert parse_key("\x7f") == "backspace"
    assert parse_key("\x1b[A") == "up"
    assert parse_key("\x1b[B") == "down"
    assert parse_key("\x1b[C") == "right"
    assert parse_key("\x1b[D") == "left"
    assert parse_key("\x1b[3~") == "delete"
    assert parse_key("\x1b[H") == "home"
    assert parse_key("\x1b[F") == "end"


def test_parse_key_ctrl() -> None:
    assert parse_key("\x03") == "ctrl+c"
    assert parse_key("\x01") == "ctrl+a"


def test_matches_key() -> None:
    assert matches_key("a", "a")
    assert matches_key("\x1b[A", "up")
    assert matches_key("\x03", "ctrl+c")
    assert not matches_key("a", "b")


def test_keybindings_manager_defaults() -> None:
    mgr = KeybindingsManager(
        {
            "tui.test.action": {
                "defaultKeys": "ctrl+t",
                "description": "Test action",
            }
        }
    )
    assert mgr.matches("\x14", "tui.test.action")
    assert not mgr.matches("a", "tui.test.action")
    assert mgr.get_keys("tui.test.action") == ["ctrl+t"]


def test_keybindings_manager_user_bindings() -> None:
    mgr = KeybindingsManager(
        {
            "tui.test.action": {
                "defaultKeys": "ctrl+t",
                "description": "Test action",
            }
        },
        {"tui.test.action": "x"},
    )
    assert mgr.matches("x", "tui.test.action")
    assert not mgr.matches("\x14", "tui.test.action")


def test_global_keybindings() -> None:
    set_keybindings(get_keybindings())
    assert "tui.select.confirm" in get_keybindings().definitions


class TestKeybindingTableCompleteness:
    """Step 1.3 shipped a subset; these pin the full TS table (step 1.16)."""

    def test_every_typescript_id_is_present(self) -> None:
        from cortex.tui.keys._keys import TUI_KEYBINDINGS

        # Verbatim from keybindings.ts `TUI_KEYBINDINGS`.
        expected = {
            "tui.editor.cursorUp",
            "tui.editor.cursorDown",
            "tui.editor.cursorLeft",
            "tui.editor.cursorRight",
            "tui.editor.cursorWordLeft",
            "tui.editor.cursorWordRight",
            "tui.editor.cursorLineStart",
            "tui.editor.cursorLineEnd",
            "tui.editor.jumpForward",
            "tui.editor.jumpBackward",
            "tui.editor.pageUp",
            "tui.editor.pageDown",
            "tui.editor.deleteCharBackward",
            "tui.editor.deleteCharForward",
            "tui.editor.deleteWordBackward",
            "tui.editor.deleteWordForward",
            "tui.editor.deleteToLineStart",
            "tui.editor.deleteToLineEnd",
            "tui.editor.yank",
            "tui.editor.yankPop",
            "tui.editor.undo",
            "tui.input.newLine",
            "tui.input.submit",
            "tui.input.tab",
            "tui.input.copy",
            "tui.select.up",
            "tui.select.down",
            "tui.select.pageUp",
            "tui.select.pageDown",
            "tui.select.confirm",
            "tui.select.cancel",
        }
        assert set(TUI_KEYBINDINGS) == expected

    def test_invented_names_are_gone(self) -> None:
        from cortex.tui.keys._keys import TUI_KEYBINDINGS

        # These three were never in the TS; the real ids carry a direction.
        for invented in (
            "tui.editor.backspace",
            "tui.editor.deleteChar",
            "tui.editor.deleteWord",
        ):
            assert invented not in TUI_KEYBINDINGS

    def test_emacs_alternates_are_bound(self) -> None:
        kb = get_keybindings()
        assert kb.matches("\x02", "tui.editor.cursorLeft"), "ctrl+b"
        assert kb.matches("\x06", "tui.editor.cursorRight"), "ctrl+f"
        assert kb.matches("\x01", "tui.editor.cursorLineStart"), "ctrl+a"
        assert kb.matches("\x05", "tui.editor.cursorLineEnd"), "ctrl+e"
        assert kb.matches("\x0b", "tui.editor.deleteToLineEnd"), "ctrl+k"
        assert kb.matches("\x15", "tui.editor.deleteToLineStart"), "ctrl+u"
        assert kb.matches("\x19", "tui.editor.yank"), "ctrl+y"
        assert kb.matches("\x17", "tui.editor.deleteWordBackward"), "ctrl+w"

    def test_primary_keys_still_match(self) -> None:
        kb = get_keybindings()
        assert kb.matches("\x7f", "tui.editor.deleteCharBackward")
        assert kb.matches("\r", "tui.input.submit")
        assert kb.matches("\t", "tui.input.tab")
        assert kb.matches("\x1b", "tui.select.cancel")


class TestLegacyEscapeSequences:
    """Step 1.17. Values verified against the TS `parseKey` under bun."""

    def test_lone_control_codes(self) -> None:
        assert parse_key("\x1c") == "ctrl+\\"
        assert parse_key("\x1d") == "ctrl+]"
        assert parse_key("\x1f") == "ctrl+-"
        assert parse_key("\x08") == "backspace"
        assert parse_key("\x00") == "ctrl+space"

    def test_emacs_word_motion_aliases_map_to_arrows(self) -> None:
        # Deliberately NOT "alt+b"/"alt+f": the TS aliases these to the arrow
        # forms so they hit the cursorWordLeft/Right bindings.
        assert parse_key("\x1bb") == "alt+left"
        assert parse_key("\x1bf") == "alt+right"
        assert parse_key("\x1bp") == "alt+up"
        assert parse_key("\x1bn") == "alt+down"
        assert parse_key("\x1bB") == "alt+left"
        assert parse_key("\x1bF") == "alt+right"

    def test_generic_alt_letters_and_digits(self) -> None:
        assert parse_key("\x1bd") == "alt+d"
        assert parse_key("\x1by") == "alt+y"
        assert parse_key("\x1b5") == "alt+5"

    def test_esc_prefixed_control_codes_are_ctrl_alt(self) -> None:
        assert parse_key("\x1b\x01") == "ctrl+alt+a"
        assert parse_key("\x1b\x1a") == "ctrl+alt+z"

    def test_ctrl_alt_bracket_family(self) -> None:
        assert parse_key("\x1b\x1b") == "ctrl+alt+["
        assert parse_key("\x1b\x1c") == "ctrl+alt+\\"
        assert parse_key("\x1b\x1d") == "ctrl+alt+]"
        assert parse_key("\x1b\x1f") == "ctrl+alt+-"

    def test_alt_whitespace_forms(self) -> None:
        assert parse_key("\x1b\r") == "alt+enter"
        assert parse_key("\x1b ") == "alt+space"
        assert parse_key("\x1b\x7f") == "alt+backspace"

    def test_the_bindings_these_unblock_now_match(self) -> None:
        kb = get_keybindings()
        assert kb.matches("\x1f", "tui.editor.undo")
        assert kb.matches("\x1bb", "tui.editor.cursorWordLeft")
        assert kb.matches("\x1bf", "tui.editor.cursorWordRight")
        assert kb.matches("\x1bd", "tui.editor.deleteWordForward")
        assert kb.matches("\x1by", "tui.editor.yankPop")


class TestCsiModifierSequences:
    """Step 1.19 — `parseKittySequence`'s other three branches.

    Every value below was diffed against the TS `parseKey` under bun rather than
    reasoned about; `\x1b[1;3D` returning `None` is what made
    `component/editor-word-motion` diverge.
    """

    def test_modified_arrows(self) -> None:
        assert parse_key("\x1b[1;3D") == "alt+left"
        assert parse_key("\x1b[1;3C") == "alt+right"
        assert parse_key("\x1b[1;3A") == "alt+up"
        assert parse_key("\x1b[1;3B") == "alt+down"
        assert parse_key("\x1b[1;5D") == "ctrl+left"
        assert parse_key("\x1b[1;5C") == "ctrl+right"
        assert parse_key("\x1b[1;2A") == "shift+up"

    def test_unmodified_arrow_form_still_parses(self) -> None:
        assert parse_key("\x1b[1;1D") == "left"

    def test_modified_functional_keys(self) -> None:
        assert parse_key("\x1b[3;3~") == "alt+delete"
        assert parse_key("\x1b[3;5~") == "ctrl+delete"
        assert parse_key("\x1b[2;2~") == "shift+insert"
        assert parse_key("\x1b[5;3~") == "alt+pageUp"
        assert parse_key("\x1b[6;3~") == "alt+pageDown"
        assert parse_key("\x1b[7;3~") == "alt+home"
        assert parse_key("\x1b[8;3~") == "alt+end"

    def test_unknown_functional_number_is_not_a_key(self) -> None:
        # `\x1b[13;2~` is one of the shift+enter encodings, and the TS does not
        # claim it here either — editor.ts matches the literal sequence.
        assert parse_key("\x1b[13;2~") is None

    def test_modified_home_end(self) -> None:
        assert parse_key("\x1b[1;2H") == "shift+home"
        assert parse_key("\x1b[1;2F") == "shift+end"
        assert parse_key("\x1b[1;5H") == "ctrl+home"

    def test_event_type_suffix_is_accepted(self) -> None:
        # Kitty flag 2 appends `:<event>`; the key is the same key.
        assert parse_key("\x1b[1;3:1D") == "alt+left"
        assert parse_key("\x1b[3;3:2~") == "alt+delete"
        assert parse_key("\x1b[1;2:3H") == "shift+home"

    def test_kitty_functional_codepoints_map_to_real_keys(self) -> None:
        # The port had invented numbers here: arrows at 57352-57355 (which kitty
        # never sends) and delete at 57399 (which is kitty's KP_0).
        assert parse_key("\x1b[57417u") == "left"
        assert parse_key("\x1b[57418u") == "right"
        assert parse_key("\x1b[57419;3u") == "alt+up"
        assert parse_key("\x1b[57420u") == "down"
        assert parse_key("\x1b[57421u") == "pageUp"
        assert parse_key("\x1b[57422u") == "pageDown"
        assert parse_key("\x1b[57423u") == "home"
        assert parse_key("\x1b[57424u") == "end"
        assert parse_key("\x1b[57425u") == "insert"
        assert parse_key("\x1b[57426u") == "delete"

    def test_keypad_keys_fold_onto_their_characters(self) -> None:
        assert parse_key("\x1b[57399u") == "0"
        assert parse_key("\x1b[57408u") == "9"
        assert parse_key("\x1b[57410u") == "/"
        assert parse_key("\x1b[57414u") == "enter"  # KP_Enter

    def test_letter_l_is_not_enter(self) -> None:
        # `kpEnter` was 108 — the codepoint of "l".
        assert parse_key("\x1b[108u") == "l"

    def test_modifier_order_and_lock_masking(self) -> None:
        # The TS emits shift, ctrl, alt, super in that order…
        assert parse_key("\x1b[97;6u") == "shift+ctrl+a"
        assert parse_key("\x1b[97;7u") == "ctrl+alt+a"
        # …masks Caps/Num Lock off entirely (ctrl|caps = 68, encoded +1)…
        assert parse_key("\x1b[97;69u") == "ctrl+a"
        # …and refuses to name a chord it has no name for (hyper = 16).
        assert parse_key("\x1b[97;17u") is None

    def test_the_bindings_this_unblocks_now_match(self) -> None:
        kb = get_keybindings()
        assert kb.matches("\x1b[1;3D", "tui.editor.cursorWordLeft")
        assert kb.matches("\x1b[1;5D", "tui.editor.cursorWordLeft")
        assert kb.matches("\x1b[1;3C", "tui.editor.cursorWordRight")
        assert kb.matches("\x1b[1;5C", "tui.editor.cursorWordRight")
        assert kb.matches("\x1b[3;3~", "tui.editor.deleteWordForward")
        assert matches_key("\x1b[1;2H", "shift+home")
