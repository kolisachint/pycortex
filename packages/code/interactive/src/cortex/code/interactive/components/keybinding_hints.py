"""Formatting for the "ctrl+o to expand" hints components print.

Port of ``components/keybinding-hints.ts``. A hint has to name the key the user
actually has, not the key the component was written against, so every function
here goes through the process-wide
:class:`~cortex.tui.keys.KeybindingsManager` — which interactive mode replaces
with the app's on construction, so a ``keybindings.json`` override shows up in
the hint text too.

:func:`matches_app_key` and :func:`app_key_label` carry the TS's fallback: a
component may be constructed before the app has installed its bindings (in a
test, or early in boot), and the bare TUI manager does not know the app-level
ids. Rather than going dead, they fall back to the id's own default keys from
:data:`~cortex.code.interactive.keybindings.KEYBINDINGS`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cortex.code.interactive.theme import get_theme
from cortex.tui.keys import get_keybindings, matches_key

if TYPE_CHECKING:
    from cortex.code.interactive.keybindings import AppKeybinding

__all__ = [
    "KeyTextFormatOptions",
    "app_key_label",
    "format_key_text",
    "key_display_text",
    "key_hint",
    "key_text",
    "matches_app_key",
    "raw_key_hint",
]


@dataclass(frozen=True)
class KeyTextFormatOptions:
    capitalize: bool = False


def _format_key_part(part: str, options: KeyTextFormatOptions) -> str:
    display_part = "option" if sys.platform == "darwin" and part.lower() == "alt" else part
    return display_part[:1].upper() + display_part[1:] if options.capitalize else display_part


def format_key_text(key: str, options: KeyTextFormatOptions | None = None) -> str:
    """Render a key id (``"ctrl+o"``, ``"up/ctrl+p"``) as display text."""
    resolved = options if options is not None else KeyTextFormatOptions()
    return "/".join(
        "+".join(_format_key_part(part, resolved) for part in alternative.split("+"))
        for alternative in key.split("/")
    )


def _format_keys(keys: list[str], options: KeyTextFormatOptions | None = None) -> str:
    if not keys:
        return ""
    return format_key_text("/".join(keys), options)


def key_text(keybinding: str) -> str:
    """The keys bound to *keybinding*, as display text."""
    return _format_keys(get_keybindings().get_keys(keybinding))


def key_display_text(keybinding: str) -> str:
    """:func:`key_text`, capitalised for a prose hint."""
    return _format_keys(
        get_keybindings().get_keys(keybinding), KeyTextFormatOptions(capitalize=True)
    )


def key_hint(keybinding: str, description: str) -> str:
    """``"ctrl+o to expand"`` — the key dim, the description muted."""
    theme = get_theme()
    return theme.fg("dim", key_text(keybinding)) + theme.fg("muted", f" {description}")


def raw_key_hint(key: str, description: str) -> str:
    """:func:`key_hint` for a literal key id rather than a bound action."""
    theme = get_theme()
    return theme.fg("dim", format_key_text(key)) + theme.fg("muted", f" {description}")


def _app_default_keys(keybinding: AppKeybinding) -> list[str]:
    """Default keys of an app-level binding, from its definition."""
    from cortex.code.interactive.keybindings import KEYBINDINGS

    defaults = KEYBINDINGS[keybinding].get("defaultKeys")
    if isinstance(defaults, list):
        return list(defaults)
    return [defaults] if defaults else []


def matches_app_key(data: str, keybinding: AppKeybinding) -> bool:
    """Whether *data* is the app-level *keybinding*, defaults included."""
    keybindings = get_keybindings()
    if keybindings.definitions.get(keybinding) is not None:
        return keybindings.matches(data, keybinding)
    return any(matches_key(data, key) for key in _app_default_keys(keybinding))


def app_key_label(keybinding: AppKeybinding) -> str:
    """First configured key for an app-level binding, for hint lines."""
    keybindings = get_keybindings()
    if keybindings.definitions.get(keybinding) is not None:
        keys = keybindings.get_keys(keybinding)
        if keys:
            return keys[0]
    defaults = _app_default_keys(keybinding)
    return defaults[0] if defaults else ""
