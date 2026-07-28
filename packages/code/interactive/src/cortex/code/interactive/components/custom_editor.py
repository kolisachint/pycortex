"""Custom editor that handles app-level keybindings for coding-agent.

Port of ``components/custom-editor.ts`` — an ``Editor`` subclass that
intercepts input before it reaches the base editor, dispatching app-level
keybindings (interrupt, exit, paste-image, etc.) and passing plain text
straight through for flat per-keystroke cost.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from cortex.tui.components import Editor, EditorOptions, EditorTheme
from cortex.tui.render import TUI

if TYPE_CHECKING:
    from cortex.code.interactive.keybindings import AppKeybinding, KeybindingsManager


__all__ = ["CustomEditor", "is_plain_text"]


def is_plain_text(data: str) -> bool:
    """True when *data* contains no control bytes (C0 range or DEL).

    Plain printable input — typed characters and paste payloads — can never
    match an app keybinding because those all arrive as control characters or
    escape sequences.
    """
    if not data:
        return False
    for ch in data:
        code = ord(ch)
        if code < 32 or code == 127:
            return False
    return True


class CustomEditor(Editor):
    """Editor with app-level keybinding dispatch.

    The base :class:`Editor` deliberately ignores Ctrl+C ("let the parent
    handle it") and offers no hook for app actions. This subclass checks
    app keybindings first, then falls through to the base editor for
    text editing and cursor movement.
    """

    def __init__(
        self,
        tui: TUI,
        theme: EditorTheme,
        keybindings: KeybindingsManager,
        options: EditorOptions | None = None,
    ) -> None:
        super().__init__(tui, theme, options)
        self.keybindings = keybindings
        self.action_handlers: dict[AppKeybinding, Callable[[], None]] = {}

        # Special handlers that can be dynamically replaced
        self.on_escape: Callable[[], None] | None = None
        self.on_ctrl_d: Callable[[], None] | None = None
        self.on_paste_image: Callable[[], None] | None = None
        # Handler for extension-registered shortcuts. Returns True if handled.
        self.on_extension_shortcut: Callable[[str], bool] | None = None

    def on_action(self, action: AppKeybinding, handler: Callable[[], None]) -> None:
        """Register a handler for an app action."""
        self.action_handlers[action] = handler

    def handle_input(self, data: str) -> None:  # noqa: C901 — dispatch table would obscure the TS parity
        # Check extension-registered shortcuts first
        if self.on_extension_shortcut is not None and self.on_extension_shortcut(data):
            return

        # Plain printable input (typed characters, paste payloads) can never
        # match an app keybinding — those all arrive as control characters or
        # escape sequences. Hand the text straight to the editor so per-keystroke
        # cost stays flat no matter how many app actions get registered.
        if is_plain_text(data):
            super().handle_input(data)
            return

        # Check for paste image keybinding
        if self.keybindings.matches(data, "app.clipboard.pasteImage"):
            if self.on_paste_image is not None:
                self.on_paste_image()
            return

        # Escape/interrupt — only if autocomplete is NOT active
        if self.keybindings.matches(data, "app.interrupt"):
            if not self.is_showing_autocomplete():
                # Use dynamic on_escape if set, otherwise registered handler
                handler = self.on_escape or self.action_handlers.get("app.interrupt")
                if handler is not None:
                    handler()
                    return
            # Let parent handle escape for autocomplete cancellation
            super().handle_input(data)
            return

        # Exit (Ctrl+D) — only when editor is empty
        if self.keybindings.matches(data, "app.exit"):
            if len(self.get_text()) == 0:
                handler = self.on_ctrl_d or self.action_handlers.get("app.exit")
                if handler is not None:
                    handler()
                return
            # Fall through to editor handling for delete-char-forward when not empty

        # Check all other app actions
        for action, handler in self.action_handlers.items():
            if action != "app.interrupt" and action != "app.exit":
                if self.keybindings.matches(data, action):
                    handler()
                    return

        # Pass to parent for editor handling
        super().handle_input(data)
