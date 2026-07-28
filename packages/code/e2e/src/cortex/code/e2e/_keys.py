"""Named keys → the bytes a terminal actually sends.

Scenarios are written in terms of what the user pressed (`h.key("ctrl+c")`), not
in terms of escape sequences. This table is the translation.

It is not taken on trust: ``test_keys.py`` feeds every sequence here back through
`cortex.tui.keys.parse_key` and asserts it round-trips to the same key id. A
harness that sends bytes the app cannot parse would make scenarios fail for a
reason that has nothing to do with the app, so the table is held to the same
parser the app uses.
"""

from __future__ import annotations

__all__ = ["KEY_SEQUENCES", "resolve_key"]

KEY_SEQUENCES: dict[str, str] = {
    # Control characters.
    "enter": "\r",
    "tab": "\t",
    "escape": "\x1b",
    "backspace": "\x7f",
    "space": " ",
    # Ctrl chords the interactive surfaces bind (exit, abort, line editing).
    "ctrl+a": "\x01",
    "ctrl+b": "\x02",
    "ctrl+c": "\x03",
    "ctrl+d": "\x04",
    "ctrl+e": "\x05",
    "ctrl+k": "\x0b",
    "ctrl+l": "\x0c",
    "ctrl+n": "\x0e",
    "ctrl+p": "\x10",
    "ctrl+r": "\x12",
    "ctrl+u": "\x15",
    "ctrl+w": "\x17",
    # Arrows and navigation (legacy CSI forms — what a bare xterm sends).
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "pageUp": "\x1b[5~",
    "pageDown": "\x1b[6~",
    "delete": "\x1b[3~",
    "insert": "\x1b[2~",
    # Enter with a modifier — the editor's "open a line instead of submitting".
    # The CSI-u form is what a terminal with the Kitty protocol sends; the legacy
    # `\x1b\r` an unmodified terminal falls back to parses as `alt+enter`, so it
    # cannot stand in for this one.
    "shift+enter": "\x1b[13;2u",
    # Modified arrows — word motion in the editor.
    "shift+up": "\x1b[1;2A",
    "shift+down": "\x1b[1;2B",
    "shift+tab": "\x1b[Z",
    "alt+left": "\x1b[1;3D",
    "alt+right": "\x1b[1;3C",
    "ctrl+left": "\x1b[1;5D",
    "ctrl+right": "\x1b[1;5C",
}


def resolve_key(name: str) -> str:
    """The byte sequence for a named key.

    Raises rather than falling back to sending the name as literal text: a typo
    in a scenario should fail loudly, not silently type `ctrl+v` into the editor.
    """
    try:
        return KEY_SEQUENCES[name]
    except KeyError:
        known = ", ".join(sorted(KEY_SEQUENCES))
        raise KeyError(f"unknown key {name!r}; known keys: {known}") from None
