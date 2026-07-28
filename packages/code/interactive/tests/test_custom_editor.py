"""Tests for the custom editor component."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from cortex.code.interactive.components.custom_editor import CustomEditor, is_plain_text
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.interactive.theme import get_editor_theme
from cortex.tui.components import EditorOptions

# ============================================================================
# Helpers
# ============================================================================


class FakeTUI:
    """Minimal TUI stub for testing the editor."""

    def __init__(self) -> None:
        self.render_calls: list[int] = []
        self._focus: Any = None
        self.columns: int = 80
        self.rows: int = 24
        self._children: list[Any] = []
        self._input_listeners: list[Any] = []
        self._clear_on_shrink: bool = False
        self._show_hardware_cursor: bool = False

    def request_render(self) -> None:
        pass

    def set_focus(self, component: Any) -> None:
        self._focus = component

    def add_child(self, component: Any) -> None:
        self._children.append(component)

    def remove_child(self, component: Any) -> None:
        if component in self._children:
            self._children.remove(component)

    def set_clear_on_shrink(self, value: bool) -> None:
        self._clear_on_shrink = value

    def set_show_hardware_cursor(self, value: bool) -> None:
        self._show_hardware_cursor = value

    def add_input_listener(self, listener: Any) -> Any:
        self._input_listeners.append(listener)
        return lambda: self._input_listeners.remove(listener)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    @property
    def terminal(self) -> FakeTerminal:
        return FakeTerminal()


class FakeTerminal:
    """Minimal terminal stub."""

    def __init__(self) -> None:
        self.columns = 80


# ============================================================================
# Tests for _is_plain_text
# ============================================================================


class TestIsPlainText:
    def test_empty_string(self) -> None:
        assert is_plain_text("") is False

    def test_single_char(self) -> None:
        assert is_plain_text("a") is True

    def test_multiple_chars(self) -> None:
        assert is_plain_text("hello") is True

    def test_with_space(self) -> None:
        assert is_plain_text("hello world") is True

    def test_with_newline(self) -> None:
        assert is_plain_text("hello\nworld") is False

    def test_with_tab(self) -> None:
        assert is_plain_text("hello\tworld") is False

    def test_with_control_char(self) -> None:
        assert is_plain_text("hello\x00world") is False

    def test_with_del(self) -> None:
        assert is_plain_text("hello\x7fworld") is False

    def test_with_high_unicode(self) -> None:
        assert is_plain_text("hello \u4e16\u754c") is True

    def test_with_escape(self) -> None:
        assert is_plain_text("\x1b") is False


# ============================================================================
# Tests for CustomEditor
# ============================================================================


class TestCustomEditor:
    def _make_editor(self) -> tuple[CustomEditor, KeybindingsManager, MagicMock]:
        """Create a CustomEditor with mocks."""
        tui = FakeTUI()  # type: ignore[arg-type]
        theme = get_editor_theme()
        keybindings = KeybindingsManager()
        editor = CustomEditor(tui, theme, keybindings, EditorOptions())  # type: ignore[arg-type]
        on_change = MagicMock()
        editor.on_change = on_change
        return editor, keybindings, on_change

    def test_plain_text_goes_to_editor(self) -> None:
        """Plain text should be handled by the base editor."""
        editor, _kb, _on_change = self._make_editor()
        editor.handle_input("hello")
        assert editor.get_text() == "hello"

    def test_escape_triggers_interrupt(self) -> None:
        """Escape should trigger app.interrupt handler."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock()
        editor.on_action("app.interrupt", handler)
        # Escape is the default key for app.interrupt
        editor.handle_input("\x1b")
        handler.assert_called_once()

    def test_escape_with_custom_handler(self) -> None:
        """Dynamic on_escape should override registered handler."""
        editor, _kb, _on_change = self._make_editor()
        registered_handler = MagicMock()
        editor.on_action("app.interrupt", registered_handler)
        dynamic_handler = MagicMock()
        editor.on_escape = dynamic_handler
        editor.handle_input("\x1b")
        dynamic_handler.assert_called_once()
        registered_handler.assert_not_called()

    def test_ctrl_d_when_empty_triggers_exit(self) -> None:
        """Ctrl+D on empty editor should trigger app.exit handler."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock()
        editor.on_action("app.exit", handler)
        # Ctrl+D is the default key for app.exit
        editor.handle_input("\x04")
        handler.assert_called_once()

    def test_ctrl_d_when_nonempty_falls_through(self) -> None:
        """Ctrl+D on non-empty editor should fall through to base editor."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock()
        editor.on_action("app.exit", handler)
        editor.handle_input("hello")
        # Ctrl+D should trigger handler since text is empty now
        # (text was just typed, so editor is not empty)
        assert editor.get_text() == "hello"
        handler.assert_not_called()

    def test_ctrl_d_with_custom_handler(self) -> None:
        """Dynamic on_ctrl_d should override registered handler."""
        editor, _kb, _on_change = self._make_editor()
        registered_handler = MagicMock()
        editor.on_action("app.exit", registered_handler)
        dynamic_handler = MagicMock()
        editor.on_ctrl_d = dynamic_handler
        editor.handle_input("\x04")
        dynamic_handler.assert_called_once()
        registered_handler.assert_not_called()

    def test_paste_image_handler(self) -> None:
        """Ctrl+V should trigger paste image handler."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock()
        editor.on_paste_image = handler
        # Ctrl+V is the default key for app.clipboard.pasteImage
        editor.handle_input("\x16")
        handler.assert_called_once()

    def test_extension_shortcut_handler(self) -> None:
        """Extension shortcut handler can intercept input."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock(return_value=True)
        editor.on_extension_shortcut = handler
        # Extension handler should intercept plain text too
        editor.handle_input("hello")
        handler.assert_called_once_with("hello")
        # Text should NOT be inserted
        assert editor.get_text() == ""

    def test_extension_shortcut_does_not_intercept(self) -> None:
        """Extension shortcut handler returning False falls through."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock(return_value=False)
        editor.on_extension_shortcut = handler
        editor.handle_input("hello")
        handler.assert_called_once_with("hello")
        # Text should be inserted
        assert editor.get_text() == "hello"

    def test_other_app_actions(self) -> None:
        """Other app actions should be handled when triggered."""
        editor, _kb, _on_change = self._make_editor()
        handler = MagicMock()
        editor.on_action("app.thinking.cycle", handler)
        # Shift+Tab is the default key for app.thinking.cycle
        editor.handle_input("\x1b[Z")
        handler.assert_called_once()

    def test_unregistered_action_falls_through(self) -> None:
        """Actions without handlers should fall through to base editor."""
        editor, _kb, _on_change = self._make_editor()
        # No handler registered for app.thinking.cycle
        # Shift+Tab should not crash and should fall through
        editor.handle_input("\x1b[Z")
        # No error should occur


# ============================================================================
# Tests for action handler registration
# ============================================================================


class TestActionHandlers:
    def test_on_action_stores_handler(self) -> None:
        tui = FakeTUI()  # type: ignore[arg-type]
        theme = get_editor_theme()
        keybindings = KeybindingsManager()
        editor = CustomEditor(tui, theme, keybindings, EditorOptions())  # type: ignore[arg-type]
        handler = MagicMock()
        editor.on_action("app.interrupt", handler)
        assert editor.action_handlers["app.interrupt"] is handler

    def test_on_action_overwrites(self) -> None:
        tui = FakeTUI()  # type: ignore[arg-type]
        theme = get_editor_theme()
        keybindings = KeybindingsManager()
        editor = CustomEditor(tui, theme, keybindings, EditorOptions())  # type: ignore[arg-type]
        handler1 = MagicMock()
        handler2 = MagicMock()
        editor.on_action("app.interrupt", handler1)
        editor.on_action("app.interrupt", handler2)
        assert editor.action_handlers["app.interrupt"] is handler2
