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
