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
