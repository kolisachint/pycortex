"""App-level keybindings for the coding agent.

Port of ``core/keybindings.ts`` — the ``KEYBINDINGS`` definitions table, the
``KeybindingsManager`` subclass that loads/saves user overrides, and the
migration helpers for legacy keybinding names.

The TUI-level keybindings live in ``cortex.tui.keys``; this module extends
them with app-specific actions (interrupt, exit, model cycling, etc.) and
provides the config-file round-trip the real app needs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cortex.tui.keys import (
    TUI_KEYBINDINGS,
    KeybindingConfig,
    KeybindingDefinitions,
)
from cortex.tui.keys import (
    KeybindingsManager as TuiKeybindingsManager,
)

__all__ = [
    "AppKeybinding",
    "KEYBINDINGS",
    "KeybindingsManager",
    "KeybindingConfig",
    "is_legacy_keybinding_name",
    "load_raw_config",
    "load_keybindings_from_file",
    "migrate_keybindings_config",
    "order_keybindings_config",
    "to_keybindings_config",
]

# App-level keybinding identifiers — the actions the interactive app handles.
APP_KEYBINDING_IDS: frozenset[str] = frozenset(
    {
        "app.interrupt",
        "app.clear",
        "app.exit",
        "app.suspend",
        "app.thinking.cycle",
        "app.model.cycleForward",
        "app.model.cycleBackward",
        "app.model.select",
        "app.tools.expand",
        "app.thinking.toggle",
        "app.tasks.cycleView",
        "app.team.focus",
        "app.team.nudge",
        "app.team.attach",
        "app.session.toggleNamedFilter",
        "app.editor.external",
        "app.message.followUp",
        "app.message.dequeue",
        "app.clipboard.pasteImage",
        "app.input.voiceTranscribe",
        "app.session.new",
        "app.session.tree",
        "app.session.fork",
        "app.session.resume",
        "app.tree.foldOrUp",
        "app.tree.unfoldOrDown",
        "app.tree.editLabel",
        "app.tree.toggleLabelTimestamp",
        "app.session.togglePath",
        "app.session.toggleSort",
        "app.session.rename",
        "app.session.delete",
        "app.session.deleteNoninvasive",
        "app.models.save",
        "app.models.enableAll",
        "app.models.clearAll",
        "app.models.toggleProvider",
        "app.models.reorderUp",
        "app.models.reorderDown",
        "app.tree.filter.default",
        "app.tree.filter.noTools",
        "app.tree.filter.userOnly",
        "app.tree.filter.labeledOnly",
        "app.tree.filter.all",
        "app.tree.filter.cycleForward",
        "app.tree.filter.cycleBackward",
    }
)

AppKeybinding = str

# Full port of KEYBINDINGS from core/keybindings.ts.  TUI_KEYBINDINGS provides
# the base; this module extends it with the app-specific actions.
KEYBINDINGS: KeybindingDefinitions = {
    **TUI_KEYBINDINGS,
    "app.interrupt": {"defaultKeys": "escape", "description": "Cancel or abort"},
    "app.clear": {"defaultKeys": "ctrl+c", "description": "Clear editor"},
    "app.exit": {"defaultKeys": "ctrl+d", "description": "Exit when editor is empty"},
    "app.suspend": {
        "defaultKeys": [] if os.name == "nt" else "ctrl+z",
        "description": "Suspend to background",
    },
    "app.thinking.cycle": {
        "defaultKeys": "shift+tab",
        "description": "Cycle thinking level",
    },
    "app.model.cycleForward": {
        "defaultKeys": "ctrl+p",
        "description": "Cycle to next model",
    },
    "app.model.cycleBackward": {
        "defaultKeys": "shift+ctrl+p",
        "description": "Cycle to previous model",
    },
    "app.model.select": {"defaultKeys": "ctrl+l", "description": "Open model selector"},
    "app.tools.expand": {"defaultKeys": "ctrl+o", "description": "Toggle tool output"},
    "app.thinking.toggle": {
        "defaultKeys": "ctrl+t",
        "description": "Toggle thinking blocks",
    },
    "app.tasks.cycleView": {
        "defaultKeys": "ctrl+n",
        "description": "Cycle task panel view (tasks → subagents → teams, skips empty lenses)",
    },
    "app.session.toggleNamedFilter": {
        "defaultKeys": "ctrl+n",
        "description": "Toggle named session filter",
    },
    "app.team.focus": {
        "defaultKeys": "alt+n",
        "description": "Focus the team roster (navigate roles, n nudge, a attach)",
    },
    "app.team.nudge": {
        "defaultKeys": "n",
        "description": "Nudge the selected team role (team focus mode)",
    },
    "app.team.attach": {
        "defaultKeys": "a",
        "description": "Attach to the selected team role (team focus mode)",
    },
    "app.editor.external": {
        "defaultKeys": "ctrl+g",
        "description": "Open external editor",
    },
    "app.message.followUp": {
        "defaultKeys": "alt+enter",
        "description": "Queue follow-up message",
    },
    "app.message.dequeue": {
        "defaultKeys": "alt+up",
        "description": "Restore queued messages",
    },
    "app.clipboard.pasteImage": {
        "defaultKeys": "alt+v" if os.name == "nt" else "ctrl+v",
        "description": "Paste image from clipboard",
    },
    "app.input.voiceTranscribe": {
        "defaultKeys": "ctrl+r",
        "description": "Record voice and transcribe into the editor",
    },
    "app.session.new": {"defaultKeys": [], "description": "Start a new session"},
    "app.session.tree": {"defaultKeys": [], "description": "Open session tree"},
    "app.session.fork": {"defaultKeys": [], "description": "Fork current session"},
    "app.session.resume": {"defaultKeys": [], "description": "Resume a session"},
    "app.tree.foldOrUp": {
        "defaultKeys": ["ctrl+left", "alt+left"],
        "description": "Fold tree branch or move up",
    },
    "app.tree.unfoldOrDown": {
        "defaultKeys": ["ctrl+right", "alt+right"],
        "description": "Unfold tree branch or move down",
    },
    "app.tree.editLabel": {
        "defaultKeys": "shift+l",
        "description": "Edit tree label",
    },
    "app.tree.toggleLabelTimestamp": {
        "defaultKeys": "shift+t",
        "description": "Toggle tree label timestamps",
    },
    "app.session.togglePath": {
        "defaultKeys": "ctrl+p",
        "description": "Toggle session path display",
    },
    "app.session.toggleSort": {
        "defaultKeys": "ctrl+s",
        "description": "Toggle session sort mode",
    },
    "app.session.rename": {
        "defaultKeys": "ctrl+r",
        "description": "Rename session",
    },
    "app.session.delete": {
        "defaultKeys": "ctrl+d",
        "description": "Delete session",
    },
    "app.session.deleteNoninvasive": {
        "defaultKeys": "ctrl+backspace",
        "description": "Delete session when query is empty",
    },
    "app.models.save": {
        "defaultKeys": "ctrl+s",
        "description": "Save model selection",
    },
    "app.models.enableAll": {
        "defaultKeys": "ctrl+a",
        "description": "Enable all models",
    },
    "app.models.clearAll": {
        "defaultKeys": "ctrl+x",
        "description": "Clear all models",
    },
    "app.models.toggleProvider": {
        "defaultKeys": "ctrl+p",
        "description": "Toggle all models for provider",
    },
    "app.models.reorderUp": {
        "defaultKeys": "alt+up",
        "description": "Move model up in order",
    },
    "app.models.reorderDown": {
        "defaultKeys": "alt+down",
        "description": "Move model down in order",
    },
    "app.options.next": {
        "defaultKeys": "right",
        "description": "Confirm and advance to the next question",
    },
    "app.options.back": {
        "defaultKeys": "left",
        "description": "Go back to the previous question",
    },
    "app.tree.filter.default": {
        "defaultKeys": "ctrl+d",
        "description": "Tree filter: default view",
    },
    "app.tree.filter.noTools": {
        "defaultKeys": "ctrl+t",
        "description": "Tree filter: hide tool results",
    },
    "app.tree.filter.userOnly": {
        "defaultKeys": "ctrl+u",
        "description": "Tree filter: user messages only",
    },
    "app.tree.filter.labeledOnly": {
        "defaultKeys": "ctrl+l",
        "description": "Tree filter: labeled entries only",
    },
    "app.tree.filter.all": {
        "defaultKeys": "ctrl+a",
        "description": "Tree filter: show all entries",
    },
    "app.tree.filter.cycleForward": {
        "defaultKeys": "ctrl+o",
        "description": "Tree filter: cycle forward",
    },
    "app.tree.filter.cycleBackward": {
        "defaultKeys": "shift+ctrl+o",
        "description": "Tree filter: cycle backward",
    },
}


# ---------------------------------------------------------------------------
# Legacy key name migration (from core/keybindings.ts)
# ---------------------------------------------------------------------------

KEYBINDING_NAME_MIGRATIONS: dict[str, str] = {
    "cursorUp": "tui.editor.cursorUp",
    "cursorDown": "tui.editor.cursorDown",
    "cursorLeft": "tui.editor.cursorLeft",
    "cursorRight": "tui.editor.cursorRight",
    "cursorWordLeft": "tui.editor.cursorWordLeft",
    "cursorWordRight": "tui.editor.cursorWordRight",
    "cursorLineStart": "tui.editor.cursorLineStart",
    "cursorLineEnd": "tui.editor.cursorLineEnd",
    "jumpForward": "tui.editor.jumpForward",
    "jumpBackward": "tui.editor.jumpBackward",
    "pageUp": "tui.editor.pageUp",
    "pageDown": "tui.editor.pageDown",
    "deleteCharBackward": "tui.editor.deleteCharBackward",
    "deleteCharForward": "tui.editor.deleteCharForward",
    "deleteWordBackward": "tui.editor.deleteWordBackward",
    "deleteWordForward": "tui.editor.deleteWordForward",
    "deleteToLineStart": "tui.editor.deleteToLineStart",
    "deleteToLineEnd": "tui.editor.deleteToLineEnd",
    "yank": "tui.editor.yank",
    "yankPop": "tui.editor.yankPop",
    "undo": "tui.editor.undo",
    "newLine": "tui.input.newLine",
    "submit": "tui.input.submit",
    "tab": "tui.input.tab",
    "copy": "tui.input.copy",
    "selectUp": "tui.select.up",
    "selectDown": "tui.select.down",
    "selectPageUp": "tui.select.pageUp",
    "selectPageDown": "tui.select.pageDown",
    "selectConfirm": "tui.select.confirm",
    "selectCancel": "tui.select.cancel",
    "interrupt": "app.interrupt",
    "clear": "app.clear",
    "exit": "app.exit",
    "suspend": "app.suspend",
    "cycleThinkingLevel": "app.thinking.cycle",
    "cycleModelForward": "app.model.cycleForward",
    "cycleModelBackward": "app.model.cycleBackward",
    "selectModel": "app.model.select",
    "expandTools": "app.tools.expand",
    "toggleThinking": "app.thinking.toggle",
    "toggleSessionNamedFilter": "app.session.toggleNamedFilter",
    "externalEditor": "app.editor.external",
    "followUp": "app.message.followUp",
    "dequeue": "app.message.dequeue",
    "pasteImage": "app.clipboard.pasteImage",
    "newSession": "app.session.new",
    "tree": "app.session.tree",
    "fork": "app.session.fork",
    "resume": "app.session.resume",
    "treeFoldOrUp": "app.tree.foldOrUp",
    "treeUnfoldOrDown": "app.tree.unfoldOrDown",
    "treeEditLabel": "app.tree.editLabel",
    "treeToggleLabelTimestamp": "app.tree.toggleLabelTimestamp",
    "toggleSessionPath": "app.session.togglePath",
    "toggleSessionSort": "app.session.toggleSort",
    "renameSession": "app.session.rename",
    "deleteSession": "app.session.delete",
    "deleteSessionNoninvasive": "app.session.deleteNoninvasive",
}


def is_legacy_keybinding_name(key: str) -> bool:
    """True if *key* is a legacy name that should be migrated."""
    return key in KEYBINDING_NAME_MIGRATIONS


def to_keybindings_config(value: Any) -> KeybindingConfig:
    """Coerce a raw JSON value to a ``KeybindingConfig``."""
    if not isinstance(value, dict):
        return {}
    config: KeybindingConfig = {}
    for key, binding in value.items():
        if isinstance(binding, str):
            config[key] = binding
        elif isinstance(binding, list) and all(isinstance(entry, str) for entry in binding):
            config[key] = binding
    return config


def migrate_keybindings_config(raw_config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Migrate legacy keybinding names in *raw_config*.

    Returns ``(config, migrated)`` where *migrated* is ``True`` when any
    key was rewritten.
    """
    config: dict[str, Any] = {}
    migrated = False

    for key, value in raw_config.items():
        next_key = KEYBINDING_NAME_MIGRATIONS[key] if is_legacy_keybinding_name(key) else key
        if next_key != key:
            migrated = True
        if key != next_key and next_key in raw_config:
            migrated = True
            continue
        config[next_key] = value

    return order_keybindings_config(config), migrated


def order_keybindings_config(config: dict[str, Any]) -> dict[str, Any]:
    """Re-order *config* to match the order in ``KEYBINDINGS``."""
    ordered: dict[str, Any] = {}
    for keybinding in KEYBINDINGS:
        if keybinding in config:
            ordered[keybinding] = config[keybinding]

    extras = sorted(k for k in config if k not in ordered)
    for key in extras:
        ordered[key] = config[key]

    return ordered


# ---------------------------------------------------------------------------
# Config file I/O
# ---------------------------------------------------------------------------


def load_raw_config(path: str | Path) -> dict[str, Any] | None:
    """Load a JSON keybindings file, returning ``None`` on missing/broken."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        parsed = json.loads(p.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


class KeybindingsManager(TuiKeybindingsManager):
    """App-level keybindings manager with config-file round-trip.

    Extends the TUI's ``KeybindingsManager`` with:
    - loading/saving a ``keybindings.json`` file;
    - legacy name migration on load;
    - ``get_effective_config()`` for the footer display.
    """

    def __init__(
        self,
        user_bindings: KeybindingConfig | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        # If config_path is provided, load bindings from file (user_bindings param
        # is ignored in this case)
        if config_path is not None:
            self.config_path = Path(config_path)
            file_bindings = load_keybindings_from_file(self.config_path)
            super().__init__(KEYBINDINGS, file_bindings)
        else:
            self.config_path = None
            super().__init__(KEYBINDINGS, user_bindings or {})

    @classmethod
    def create(cls, agent_dir: str | Path | None = None) -> KeybindingsManager:
        """Create a manager from the agent directory's ``keybindings.json``."""
        if agent_dir is None:
            agent_dir = Path.home() / ".config" / "hoocode"
        p = Path(agent_dir) / "keybindings.json"
        user_bindings = load_keybindings_from_file(p)
        return cls(user_bindings, p)

    def reload(self) -> None:
        """Re-load bindings from disk."""
        if self.config_path is None:
            return
        self.set_user_bindings(load_keybindings_from_file(self.config_path))

    def get_effective_config(self) -> KeybindingConfig:
        """Return the resolved bindings (user overrides + defaults)."""
        return self.get_resolved_bindings()


def load_keybindings_from_file(path: str | Path) -> KeybindingConfig:
    """Load and migrate keybindings from a JSON file."""
    raw = load_raw_config(path)
    if raw is None:
        return {}
    config, _migrated = migrate_keybindings_config(raw)
    return to_keybindings_config(config)
