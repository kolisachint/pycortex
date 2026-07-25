"""Keyboard input handling for terminal applications.

Minimal faithful port of hoocode's `packages/tui/src/keys.ts` and
`packages/tui/src/keybindings.ts`. Full Kitty keyboard protocol parsing is
stubbed for the initial migration; common keys and keybindings are supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TypeVar

_kitty_protocol_active = False


# Modifier bitmask values (matches TS original)
MODIFIERS = {
    "shift": 1,
    "alt": 2,
    "ctrl": 4,
    "super": 8,
    "hyper": 16,
    "meta": 32,
    "caps_lock": 64,
    "num_lock": 128,
}

LOCK_MASK = MODIFIERS["caps_lock"] | MODIFIERS["num_lock"]

KITTY_CSI_U_REGEX = re.compile(r"^\x1b\[(\d+)(?::(\d*))?(?::(\d+))?(?:;(\d+))?(?::(\d+))?u$")
# The other three shapes `parseKittySequence` accepts. Legacy terminals send
# these for modified arrows / functional keys, and they are how every word-motion
# binding (`alt+left`, `ctrl+right`) actually arrives.
KITTY_ARROW_REGEX = re.compile(r"^\x1b\[1;(\d+)(?::(\d+))?([ABCD])$")
KITTY_FUNCTIONAL_REGEX = re.compile(r"^\x1b\[(\d+)(?:;(\d+))?(?::(\d+))?~$")
KITTY_HOME_END_REGEX = re.compile(r"^\x1b\[1;(\d+)(?::(\d+))?([HF])$")

# Common special-key codepoints used by formatParsedKey / decodeKittyPrintable.
CODEPOINTS = {
    "escape": 27,
    "tab": 9,
    "enter": 13,
    "space": 32,
    "backspace": 127,
    "kpEnter": 57414,  # Numpad Enter (Kitty protocol)
}

# Negative sentinels, exactly as in the TS: these keys have no Unicode codepoint,
# and the real kitty numbers reach them through
# `KITTY_FUNCTIONAL_KEY_EQUIVALENTS` below.
FUNCTIONAL_CODEPOINTS = {
    "delete": -10,
    "insert": -11,
    "pageUp": -12,
    "pageDown": -13,
    "home": -14,
    "end": -15,
}

ARROW_CODEPOINTS = {
    "up": -1,
    "down": -2,
    "right": -3,
    "left": -4,
}

KITTY_FUNCTIONAL_KEY_EQUIVALENTS: dict[int, int] = {
    57399: 48,  # KP_0 -> 0
    57400: 49,  # KP_1 -> 1
    57401: 50,  # KP_2 -> 2
    57402: 51,  # KP_3 -> 3
    57403: 52,  # KP_4 -> 4
    57404: 53,  # KP_5 -> 5
    57405: 54,  # KP_6 -> 6
    57406: 55,  # KP_7 -> 7
    57407: 56,  # KP_8 -> 8
    57408: 57,  # KP_9 -> 9
    57409: 46,  # KP_DECIMAL -> .
    57410: 47,  # KP_DIVIDE -> /
    57411: 42,  # KP_MULTIPLY -> *
    57412: 45,  # KP_SUBTRACT -> -
    57413: 43,  # KP_ADD -> +
    57415: 61,  # KP_EQUAL -> =
    57416: 44,  # KP_SEPARATOR -> ,
    57417: ARROW_CODEPOINTS["left"],
    57418: ARROW_CODEPOINTS["right"],
    57419: ARROW_CODEPOINTS["up"],
    57420: ARROW_CODEPOINTS["down"],
    57421: FUNCTIONAL_CODEPOINTS["pageUp"],
    57422: FUNCTIONAL_CODEPOINTS["pageDown"],
    57423: FUNCTIONAL_CODEPOINTS["home"],
    57424: FUNCTIONAL_CODEPOINTS["end"],
    57425: FUNCTIONAL_CODEPOINTS["insert"],
    57426: FUNCTIONAL_CODEPOINTS["delete"],
}

# `\x1b[<n>;<mod>~` numbers, as `parseKittySequence` maps them.
KITTY_FUNCTIONAL_KEY_NUMBERS: dict[int, int] = {
    2: FUNCTIONAL_CODEPOINTS["insert"],
    3: FUNCTIONAL_CODEPOINTS["delete"],
    5: FUNCTIONAL_CODEPOINTS["pageUp"],
    6: FUNCTIONAL_CODEPOINTS["pageDown"],
    7: FUNCTIONAL_CODEPOINTS["home"],
    8: FUNCTIONAL_CODEPOINTS["end"],
}

KITTY_ARROW_LETTERS: dict[str, int] = {
    "A": ARROW_CODEPOINTS["up"],
    "B": ARROW_CODEPOINTS["down"],
    "C": ARROW_CODEPOINTS["right"],
    "D": ARROW_CODEPOINTS["left"],
}

SYMBOL_KEYS = set("`~!@#$%^&*()-_=+[]{}\\|;:'\",<.>/?")

# Common legacy escape sequences
LEGACY_SEQUENCE_KEY_IDS: dict[str, str] = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1b[3~": "delete",
    "\x1b[5~": "pageUp",
    "\x1b[6~": "pageDown",
    "\x1b[Z": "shift+tab",
    "\x1bOH": "home",
    "\x1bOF": "end",
    "\x1bOM": "enter",
    # Emacs word-motion aliases. These must stay in the table rather than fall
    # through to the generic `ESC <letter>` rule below, which would yield
    # "alt+b"/"alt+f" and miss the alt+left/alt+right keybindings entirely.
    "\x1bb": "alt+left",
    "\x1bf": "alt+right",
    "\x1bp": "alt+up",
    "\x1bn": "alt+down",
}


def set_kitty_protocol_active(active: bool) -> None:
    global _kitty_protocol_active
    _kitty_protocol_active = active


def is_kitty_protocol_active() -> bool:
    return _kitty_protocol_active


def is_key_release(key: str) -> bool:
    return key.endswith("+release")


def is_key_repeat(key: str) -> bool:
    return key.endswith("+repeat")


class Key:
    """Namespace of key identifier constants (snake_case of TS `Key`)."""

    escape = "escape"
    tab = "tab"
    enter = "enter"
    space = "space"
    backspace = "backspace"
    delete = "delete"
    insert = "insert"
    home = "home"
    end = "end"
    pageUp = "pageUp"
    pageDown = "pageDown"
    up = "up"
    down = "down"
    left = "left"
    right = "right"
    f1 = "f1"
    f2 = "f2"
    f3 = "f3"
    f4 = "f4"
    f5 = "f5"
    f6 = "f6"
    f7 = "f7"
    f8 = "f8"
    f9 = "f9"
    f10 = "f10"
    f11 = "f11"
    f12 = "f12"


KeyId = str
Keybinding = str
KeybindingDefinition = dict[str, str | list[str]]
KeybindingDefinitions = dict[str, KeybindingDefinition]
KeybindingConfig = dict[str, KeyId | list[KeyId]]

T = TypeVar("T")


def matches_key(data: str, key_id: str) -> bool:
    """Return True if raw terminal data matches the given key identifier."""
    parsed = parse_key(data)
    if parsed == key_id:
        return True
    # Allow release/repeat forms to match base key when requested without suffix
    if parsed and (parsed + "+release" == key_id or parsed + "+repeat" == key_id):
        return True
    if parsed and parsed.endswith("+release") and parsed.removesuffix("+release") == key_id:
        return True
    if parsed and parsed.endswith("+repeat") and parsed.removesuffix("+repeat") == key_id:
        return True
    return False


def _format_key_name_with_modifiers(key_name: str, modifier: int) -> str | None:
    """The TS `formatKeyNameWithModifiers`: fixed order, and no invented names.

    Caps/Num Lock are masked off (they are state, not a chord), and a chord
    carrying a modifier hoocode has no name for — hyper, meta — has no key id at
    all rather than a made-up one.
    """
    effective_mod = modifier & ~LOCK_MASK
    supported = MODIFIERS["shift"] | MODIFIERS["ctrl"] | MODIFIERS["alt"] | MODIFIERS["super"]
    if effective_mod & ~supported:
        return None
    mods: list[str] = []
    if effective_mod & MODIFIERS["shift"]:
        mods.append("shift")
    if effective_mod & MODIFIERS["ctrl"]:
        mods.append("ctrl")
    if effective_mod & MODIFIERS["alt"]:
        mods.append("alt")
    if effective_mod & MODIFIERS["super"]:
        mods.append("super")
    return f"{'+'.join(mods)}+{key_name}" if mods else key_name


def _normalize_kitty_functional_codepoint(codepoint: int) -> int:
    """Map kitty's functional codepoints onto hoocode's sentinels."""
    return KITTY_FUNCTIONAL_KEY_EQUIVALENTS.get(codepoint, codepoint)


def _format_parsed_key(codepoint: int, modifier: int) -> str | None:
    cp = _normalize_kitty_functional_codepoint(codepoint)
    if cp == CODEPOINTS["escape"]:
        key_name = "escape"
    elif cp == CODEPOINTS["tab"]:
        key_name = "tab"
    elif cp == CODEPOINTS["enter"] or cp == CODEPOINTS["kpEnter"]:
        key_name = "enter"
    elif cp == CODEPOINTS["space"]:
        key_name = "space"
    elif cp == CODEPOINTS["backspace"]:
        key_name = "backspace"
    elif cp == FUNCTIONAL_CODEPOINTS["delete"]:
        key_name = "delete"
    elif cp == FUNCTIONAL_CODEPOINTS["insert"]:
        key_name = "insert"
    elif cp == FUNCTIONAL_CODEPOINTS["home"]:
        key_name = "home"
    elif cp == FUNCTIONAL_CODEPOINTS["end"]:
        key_name = "end"
    elif cp == FUNCTIONAL_CODEPOINTS["pageUp"]:
        key_name = "pageUp"
    elif cp == FUNCTIONAL_CODEPOINTS["pageDown"]:
        key_name = "pageDown"
    elif cp == ARROW_CODEPOINTS["up"]:
        key_name = "up"
    elif cp == ARROW_CODEPOINTS["down"]:
        key_name = "down"
    elif cp == ARROW_CODEPOINTS["left"]:
        key_name = "left"
    elif cp == ARROW_CODEPOINTS["right"]:
        key_name = "right"
    elif 48 <= cp <= 57:
        key_name = chr(cp)
    elif 97 <= cp <= 122:
        key_name = chr(cp)
    elif cp >= 0 and chr(cp) in SYMBOL_KEYS:
        key_name = chr(cp)
    else:
        return None
    return _format_key_name_with_modifiers(key_name, modifier)


def _parse_kitty_sequence(data: str) -> tuple[int, int, int | None] | None:
    """Port of `parseKittySequence` — all four sequence shapes it accepts."""
    m = KITTY_CSI_U_REGEX.match(data)
    if m:
        codepoint = int(m.group(1))
        mod_value = int(m.group(4)) if m.group(4) else 1
        base_layout = int(m.group(3)) if m.group(3) else None
        return codepoint, mod_value - 1, base_layout

    # Arrow keys with modifier: \x1b[1;<mod>A/B/C/D (optionally :<event>)
    arrow = KITTY_ARROW_REGEX.match(data)
    if arrow:
        return KITTY_ARROW_LETTERS[arrow.group(3)], int(arrow.group(1)) - 1, None

    # Functional keys: \x1b[<num>~ or \x1b[<num>;<mod>~ (optionally :<event>)
    func = KITTY_FUNCTIONAL_REGEX.match(data)
    if func:
        codepoint = KITTY_FUNCTIONAL_KEY_NUMBERS.get(int(func.group(1)))
        if codepoint is not None:
            mod_value = int(func.group(2)) if func.group(2) else 1
            return codepoint, mod_value - 1, None

    # Home/End with modifier: \x1b[1;<mod>H/F (optionally :<event>)
    home_end = KITTY_HOME_END_REGEX.match(data)
    if home_end:
        codepoint = (
            FUNCTIONAL_CODEPOINTS["home"]
            if home_end.group(3) == "H"
            else FUNCTIONAL_CODEPOINTS["end"]
        )
        return codepoint, int(home_end.group(1)) - 1, None

    return None


def _parse_modify_other_keys_sequence(data: str) -> tuple[int, int] | None:
    # CSI 27 ; mod ; code ~
    if not data.startswith("\x1b[27;"):
        return None
    body = data[4:-1]
    parts = body.split(";")
    if len(parts) < 2:
        return None
    try:
        mod = int(parts[1]) - 1
        code = int(parts[-1])
        return code, mod
    except ValueError:
        return None


def parse_key(data: str) -> str | None:
    """Parse raw terminal data into a key identifier string."""
    kitty = _parse_kitty_sequence(data)
    if kitty:
        result = _format_parsed_key(kitty[0], kitty[1])
        if result:
            return result

    mok = _parse_modify_other_keys_sequence(data)
    if mok:
        result = _format_parsed_key(mok[0], mok[1])
        if result:
            return result

    if data in LEGACY_SEQUENCE_KEY_IDS:
        return LEGACY_SEQUENCE_KEY_IDS[data]

    if data == "\x1b":
        return "escape"
    if data == "\x1c":
        return "ctrl+\\"
    if data == "\x1d":
        return "ctrl+]"
    if data == "\x1f":
        return "ctrl+-"
    if data == "\x1b\x1b":
        return "ctrl+alt+["
    if data == "\x1b\x1c":
        return "ctrl+alt+\\"
    if data == "\x1b\x1d":
        return "ctrl+alt+]"
    if data == "\x1b\x1f":
        return "ctrl+alt+-"
    if data == "\t":
        return "tab"
    if data == "\r" or data == "\n":
        return "enter"
    if data == " ":
        return "space"
    if data == "\x7f":
        return "backspace"
    if data == "\x08":
        return "backspace"
    if data == "\x00":
        return "ctrl+space"
    if data == "\x1b[Z":
        return "shift+tab"
    if data == "\x1b\r":
        return "alt+enter"
    if data == "\x1b ":
        return "alt+space"
    if data == "\x1b\x7f" or data == "\x1b\b":
        return "alt+backspace"
    if data == "\x1bB":
        return "alt+left"
    if data == "\x1bF":
        return "alt+right"
    if len(data) == 2 and data[0] == "\x1b":
        # ESC-prefixed legacy alt combinations.
        code = ord(data[1])
        if 1 <= code <= 26:
            return f"ctrl+alt+{chr(code + 96)}"
        if (97 <= code <= 122) or (48 <= code <= 57):
            return f"alt+{chr(code)}"
    if data == "\x1b[A":
        return "up"
    if data == "\x1b[B":
        return "down"
    if data == "\x1b[C":
        return "right"
    if data == "\x1b[D":
        return "left"
    if data == "\x1b[H" or data == "\x1bOH":
        return "home"
    if data == "\x1b[F" or data == "\x1bOF":
        return "end"
    if data == "\x1b[3~":
        return "delete"
    if data == "\x1b[5~":
        return "pageUp"
    if data == "\x1b[6~":
        return "pageDown"

    if len(data) == 1:
        code = ord(data)
        if 1 <= code <= 26:
            return f"ctrl+{chr(code + 96)}"
        if 32 <= code <= 126:
            return data

    return None


def decode_kitty_printable(data: str) -> str | None:
    m = KITTY_CSI_U_REGEX.match(data)
    if not m:
        return None
    codepoint = int(m.group(1))
    shifted = m.group(2)
    mod_value = int(m.group(4)) if m.group(4) else 1
    modifier = mod_value - 1
    if modifier & (MODIFIERS["alt"] | MODIFIERS["ctrl"]):
        return None
    effective = codepoint
    if modifier & MODIFIERS["shift"] and shifted and shifted.isdigit():
        effective = int(shifted)
    effective = _normalize_kitty_functional_codepoint(effective)
    if not (32 <= effective <= 0x10FFFF):
        return None
    try:
        return chr(effective)
    except ValueError:
        return None


def _decode_modify_other_keys_printable(data: str) -> str | None:
    parsed = _parse_modify_other_keys_sequence(data)
    if not parsed:
        return None
    code, mod = parsed
    if mod & ~MODIFIERS["shift"]:
        return None
    if code < 32:
        return None
    try:
        return chr(code)
    except ValueError:
        return None


def decode_printable_key(data: str) -> str | None:
    """Decode a Kitty CSI-u or modifyOtherKeys sequence into a printable char."""
    return decode_kitty_printable(data) or _decode_modify_other_keys_printable(data)


# Keybindings


def _normalize_keys(keys: KeyId | list[KeyId] | None) -> list[KeyId]:
    if keys is None:
        return []
    key_list = [keys] if isinstance(keys, str) else list(keys)
    seen: set[str] = set()
    result: list[str] = []
    for key in key_list:
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


@dataclass
class KeybindingConflict:
    key: KeyId
    keybindings: list[str]


@dataclass
class KeybindingsManager:
    definitions: KeybindingDefinitions
    user_bindings: KeybindingConfig = field(default_factory=dict)
    keys_by_id: dict[str, list[str]] = field(default_factory=dict)
    conflicts: list[KeybindingConflict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.rebuild()

    def rebuild(self) -> None:
        self.keys_by_id.clear()
        self.conflicts.clear()
        user_claims: dict[str, set[str]] = {}
        for keybinding, keys in self.user_bindings.items():
            if keybinding not in self.definitions:
                continue
            for key in _normalize_keys(keys):
                claimants = user_claims.setdefault(key, set())
                claimants.add(keybinding)
        for key, keybindings in user_claims.items():
            if len(keybindings) > 1:
                self.conflicts.append(KeybindingConflict(key, sorted(keybindings)))
        for keybinding, definition in self.definitions.items():
            user_keys = self.user_bindings.get(keybinding)
            if user_keys is None:
                keys = _normalize_keys(definition.get("defaultKeys"))
            else:
                keys = _normalize_keys(user_keys)
            self.keys_by_id[keybinding] = keys

    def matches(self, data: str, keybinding: Keybinding) -> bool:
        for key in self.keys_by_id.get(keybinding, []):
            if matches_key(data, key):
                return True
        return False

    def get_keys(self, keybinding: Keybinding) -> list[KeyId]:
        return list(self.keys_by_id.get(keybinding, []))

    def get_definition(self, keybinding: Keybinding) -> KeybindingDefinition:
        return self.definitions[keybinding]

    def get_conflicts(self) -> list[KeybindingConflict]:
        return [KeybindingConflict(c.key, list(c.keybindings)) for c in self.conflicts]

    def set_user_bindings(self, user_bindings: KeybindingConfig) -> None:
        self.user_bindings = user_bindings
        self.rebuild()

    def get_user_bindings(self) -> KeybindingConfig:
        return dict(self.user_bindings)

    def get_resolved_bindings(self) -> KeybindingConfig:
        resolved: KeybindingConfig = {}
        for keybinding in self.definitions:
            keys = self.keys_by_id.get(keybinding, [])
            resolved[keybinding] = keys[0] if len(keys) == 1 else list(keys)
        return resolved


_global_keybindings: KeybindingsManager | None = None


def set_keybindings(keybindings: KeybindingsManager) -> None:
    global _global_keybindings
    _global_keybindings = keybindings


def get_keybindings() -> KeybindingsManager:
    global _global_keybindings
    if _global_keybindings is None:
        _global_keybindings = KeybindingsManager(TUI_KEYBINDINGS)
    return _global_keybindings


# Full port of `TUI_KEYBINDINGS` in keybindings.ts — id, default keys and
# description, in TS order. Step 1.3 shipped a 15-id subset with truncated key
# lists; the missing Emacs alternates (ctrl+b/f/a/e, alt+b/f/d/y) are not
# decoration, they are how the editor components are driven.
TUI_KEYBINDINGS: KeybindingDefinitions = {
    "tui.editor.cursorUp": {"defaultKeys": "up", "description": "Move cursor up"},
    "tui.editor.cursorDown": {"defaultKeys": "down", "description": "Move cursor down"},
    "tui.editor.cursorLeft": {
        "defaultKeys": ["left", "ctrl+b"],
        "description": "Move cursor left",
    },
    "tui.editor.cursorRight": {
        "defaultKeys": ["right", "ctrl+f"],
        "description": "Move cursor right",
    },
    "tui.editor.cursorWordLeft": {
        "defaultKeys": ["alt+left", "ctrl+left", "alt+b"],
        "description": "Move cursor word left",
    },
    "tui.editor.cursorWordRight": {
        "defaultKeys": ["alt+right", "ctrl+right", "alt+f"],
        "description": "Move cursor word right",
    },
    "tui.editor.cursorLineStart": {
        "defaultKeys": ["home", "ctrl+a"],
        "description": "Move to line start",
    },
    "tui.editor.cursorLineEnd": {
        "defaultKeys": ["end", "ctrl+e"],
        "description": "Move to line end",
    },
    "tui.editor.jumpForward": {
        "defaultKeys": "ctrl+]",
        "description": "Jump forward to character",
    },
    "tui.editor.jumpBackward": {
        "defaultKeys": "ctrl+alt+]",
        "description": "Jump backward to character",
    },
    "tui.editor.pageUp": {"defaultKeys": "pageUp", "description": "Page up"},
    "tui.editor.pageDown": {"defaultKeys": "pageDown", "description": "Page down"},
    "tui.editor.deleteCharBackward": {
        "defaultKeys": "backspace",
        "description": "Delete character backward",
    },
    "tui.editor.deleteCharForward": {
        "defaultKeys": ["delete", "ctrl+d"],
        "description": "Delete character forward",
    },
    "tui.editor.deleteWordBackward": {
        "defaultKeys": ["ctrl+w", "alt+backspace"],
        "description": "Delete word backward",
    },
    "tui.editor.deleteWordForward": {
        "defaultKeys": ["alt+d", "alt+delete"],
        "description": "Delete word forward",
    },
    "tui.editor.deleteToLineStart": {
        "defaultKeys": "ctrl+u",
        "description": "Delete to line start",
    },
    "tui.editor.deleteToLineEnd": {
        "defaultKeys": "ctrl+k",
        "description": "Delete to line end",
    },
    "tui.editor.yank": {"defaultKeys": "ctrl+y", "description": "Yank"},
    "tui.editor.yankPop": {"defaultKeys": "alt+y", "description": "Yank pop"},
    "tui.editor.undo": {"defaultKeys": "ctrl+-", "description": "Undo"},
    "tui.input.newLine": {"defaultKeys": "shift+enter", "description": "Insert newline"},
    "tui.input.submit": {"defaultKeys": "enter", "description": "Submit input"},
    "tui.input.tab": {"defaultKeys": "tab", "description": "Tab / autocomplete"},
    "tui.input.copy": {"defaultKeys": "ctrl+c", "description": "Copy selection"},
    "tui.select.up": {"defaultKeys": "up", "description": "Move selection up"},
    "tui.select.down": {"defaultKeys": "down", "description": "Move selection down"},
    "tui.select.pageUp": {"defaultKeys": "pageUp", "description": "Selection page up"},
    "tui.select.pageDown": {"defaultKeys": "pageDown", "description": "Selection page down"},
    "tui.select.confirm": {"defaultKeys": "enter", "description": "Confirm selection"},
    "tui.select.cancel": {"defaultKeys": ["escape", "ctrl+c"], "description": "Cancel selection"},
}
