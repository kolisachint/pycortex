"""The built-in slash-command table.

The table is user-visible text — every row is a line of the `/` menu — so what
is checked here is that it still says what ``core/slash-commands.ts`` says, in
the order it says it. A description that drifts changes the screen.
"""

from __future__ import annotations

from cortex.code.config import APP_NAME
from cortex.code.interactive import BUILTIN_SLASH_COMMANDS, BuiltinSlashCommand

#: `(name, description)` for every row, read off the TS.
EXPECTED: list[tuple[str, str]] = [
    ("settings", "Open settings menu"),
    ("model", "Select model (opens selector UI)"),
    ("scoped-models", "Enable/disable models for Ctrl+P cycling"),
    ("export", "Export session (HTML default, or specify path: .html/.jsonl)"),
    ("import", "Import and resume a session from a JSONL file"),
    ("share", "Share session as a secret GitHub gist"),
    ("copy", "Copy last agent message to clipboard"),
    ("name", "Set session display name"),
    ("session", "Show session info and stats"),
    ("changelog", "Show changelog entries"),
    ("hotkeys", "Show all keyboard shortcuts"),
    ("fork", "Create a new fork from a previous user message"),
    ("clone", "Duplicate the current session at the current position"),
    ("tree", "Navigate session tree (switch branches)"),
    ("login", "Configure provider authentication"),
    ("logout", "Remove provider authentication"),
    ("new", "Start a new session"),
    ("compact", "Manually compact the session context"),
    ("resume", "Resume a different session"),
    ("reload", "Reload keybindings, extensions, skills, prompts, and themes"),
    ("quit", f"Quit {APP_NAME}"),
    ("subagent", "Spawn a subagent directly: /subagent <mode> <task>"),
]


class TestBuiltinSlashCommands:
    def test_matches_the_ts_table_row_for_row(self):
        assert [(c.name, c.description) for c in BUILTIN_SLASH_COMMANDS] == EXPECTED

    def test_every_row_is_a_builtin_slash_command(self):
        assert all(isinstance(c, BuiltinSlashCommand) for c in BUILTIN_SLASH_COMMANDS)

    def test_names_are_unique(self):
        names = [c.name for c in BUILTIN_SLASH_COMMANDS]
        assert len(set(names)) == len(names)

    def test_names_carry_no_leading_slash(self):
        # The provider prepends it; a name that carried one would offer `//quit`.
        assert not any(c.name.startswith("/") for c in BUILTIN_SLASH_COMMANDS)

    def test_quit_names_the_app(self):
        quit_command = next(c for c in BUILTIN_SLASH_COMMANDS if c.name == "quit")
        assert quit_command.description == f"Quit {APP_NAME}"

    def test_the_table_is_immutable(self):
        # A tuple rather than the TS's `ReadonlyArray`: the menu is rebuilt from
        # this on every reload, and a handler that appended to it would leak.
        assert isinstance(BUILTIN_SLASH_COMMANDS, tuple)
