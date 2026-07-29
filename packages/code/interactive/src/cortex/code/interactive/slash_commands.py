"""The built-in slash-command table. Port of ``core/slash-commands.ts``.

This is the menu `/` opens: :data:`BUILTIN_SLASH_COMMANDS` is what
``createBaseAutocompleteProvider`` maps over, so the table *is* the list of
commands the app advertises. It is ported whole — the descriptions are user-
visible strings and a subset would silently change the screen.

Advertising and dispatching are two different things, and this port keeps them
one step apart. :class:`~cortex.code.interactive.interactive_mode.InteractiveMode`
registers the handlers that have something to drive today
(``create_built_in_slash_commands``), and offers only those
(``setup_autocomplete_provider``). The TS has a handler for every name here
because it has the
model registry, the session-replacement runtime and the extension runner that
those handlers reach for; this port does not yet, and a menu entry that submits
its own name to the model as a prompt would be worse than no entry at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cortex.code.config import APP_NAME
from cortex.code.prompts import SourceInfo

__all__ = [
    "BUILTIN_SLASH_COMMANDS",
    "BuiltinSlashCommand",
    "SlashCommandInfo",
    "SlashCommandSource",
]

#: Where a non-built-in command came from.
SlashCommandSource = Literal["extension", "prompt", "skill"]


@dataclass(frozen=True)
class SlashCommandInfo:
    """A command contributed by an extension, prompt template or skill.

    The extension-facing shape (``getCommands()`` in the TS's extension API).
    Nothing in this port produces one yet — the extension runner and the
    resource loader are later steps — but the type is what those will build.
    """

    name: str
    source: SlashCommandSource
    source_info: SourceInfo
    description: str | None = None


@dataclass(frozen=True)
class BuiltinSlashCommand:
    """One row of :data:`BUILTIN_SLASH_COMMANDS`."""

    name: str
    description: str


#: The commands the app ships with, in the order the `/` menu lists them.
BUILTIN_SLASH_COMMANDS: tuple[BuiltinSlashCommand, ...] = (
    BuiltinSlashCommand("settings", "Open settings menu"),
    BuiltinSlashCommand("model", "Select model (opens selector UI)"),
    BuiltinSlashCommand("scoped-models", "Enable/disable models for Ctrl+P cycling"),
    BuiltinSlashCommand("export", "Export session (HTML default, or specify path: .html/.jsonl)"),
    BuiltinSlashCommand("import", "Import and resume a session from a JSONL file"),
    BuiltinSlashCommand("share", "Share session as a secret GitHub gist"),
    BuiltinSlashCommand("copy", "Copy last agent message to clipboard"),
    BuiltinSlashCommand("name", "Set session display name"),
    BuiltinSlashCommand("session", "Show session info and stats"),
    BuiltinSlashCommand("changelog", "Show changelog entries"),
    BuiltinSlashCommand("hotkeys", "Show all keyboard shortcuts"),
    BuiltinSlashCommand("fork", "Create a new fork from a previous user message"),
    BuiltinSlashCommand("clone", "Duplicate the current session at the current position"),
    BuiltinSlashCommand("tree", "Navigate session tree (switch branches)"),
    BuiltinSlashCommand("login", "Configure provider authentication"),
    BuiltinSlashCommand("logout", "Remove provider authentication"),
    BuiltinSlashCommand("new", "Start a new session"),
    BuiltinSlashCommand("compact", "Manually compact the session context"),
    BuiltinSlashCommand("resume", "Resume a different session"),
    BuiltinSlashCommand("reload", "Reload keybindings, extensions, skills, prompts, and themes"),
    BuiltinSlashCommand("quit", f"Quit {APP_NAME}"),
    BuiltinSlashCommand("subagent", "Spawn a subagent directly: /subagent <mode> <task>"),
)
