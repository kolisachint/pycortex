"""Theme tokens and the ANSI they resolve to.

Port of the colour core of ``modes/interactive/theme/theme.ts``: the colour-mode
detection, the hex -> truecolor/256-colour conversion, the :class:`Theme` value
type, and the built-in ``dark`` palette loaded from the theme JSON this package
ships (a verbatim copy of the TS ``theme/dark.json``, so the two cannot drift by
transcription).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cortex.tui.components import EditorTheme, MarkdownTheme, SelectListTheme

__all__ = [
    "ColorMode",
    "Theme",
    "detect_color_mode",
    "get_editor_theme",
    "get_markdown_theme",
    "get_select_list_theme",
    "get_theme",
    "hex_to_256",
    "hex_to_rgb",
    "load_builtin_theme",
    "set_theme_instance",
]

ColorMode = Literal["truecolor", "256color"]

_THEME_DIR = Path(__file__).parent


# ============================================================================
# Color Utilities
# ============================================================================


def detect_color_mode() -> ColorMode:
    """Which colour depth this terminal is assumed to support."""
    colorterm = os.environ.get("COLORTERM")
    if colorterm in ("truecolor", "24bit"):
        return "truecolor"
    # Windows Terminal supports truecolor
    if os.environ.get("WT_SESSION"):
        return "truecolor"
    term = os.environ.get("TERM", "")
    # Fall back to 256color for truly limited terminals
    if term in ("dumb", "", "linux"):
        return "256color"
    # Terminal.app also doesn't support truecolor
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return "256color"
    # GNU screen doesn't support truecolor unless explicitly opted in via
    # COLORTERM=truecolor. TERM under screen is typically "screen",
    # "screen-256color", or "screen.xterm-256color".
    if term == "screen" or term.startswith("screen-") or term.startswith("screen."):
        return "256color"
    # Assume truecolor for everything else -- virtually all modern terminals do.
    return "truecolor"


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    cleaned = hex_color.replace("#", "")
    if len(cleaned) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    try:
        return (
            int(cleaned[0:2], 16),
            int(cleaned[2:4], 16),
            int(cleaned[4:6], 16),
        )
    except ValueError:
        raise ValueError(f"Invalid hex color: {hex_color}") from None


# The 6x6x6 color cube channel values (indices 0-5)
_CUBE_VALUES = (0, 95, 135, 175, 215, 255)

# Grayscale ramp values (indices 232-255, 24 grays from 8 to 238)
_GRAY_VALUES = tuple(8 + i * 10 for i in range(24))


def _closest_index(values: tuple[int, ...], value: int) -> int:
    return min(range(len(values)), key=lambda i: abs(value - values[i]))


def _color_distance(r1: int, g1: int, b1: int, r2: int, g2: int, b2: int) -> float:
    # Weighted Euclidean distance (human eye is more sensitive to green)
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return dr * dr * 0.299 + dg * dg * 0.587 + db * db * 0.114


def _rgb_to_256(r: int, g: int, b: int) -> int:
    # Find closest color in the 6x6x6 cube
    r_idx = _closest_index(_CUBE_VALUES, r)
    g_idx = _closest_index(_CUBE_VALUES, g)
    b_idx = _closest_index(_CUBE_VALUES, b)
    cube_index = 16 + 36 * r_idx + 6 * g_idx + b_idx
    cube_dist = _color_distance(
        r, g, b, _CUBE_VALUES[r_idx], _CUBE_VALUES[g_idx], _CUBE_VALUES[b_idx]
    )

    # Find closest grayscale
    gray = round(0.299 * r + 0.587 * g + 0.114 * b)
    gray_idx = _closest_index(_GRAY_VALUES, gray)
    gray_value = _GRAY_VALUES[gray_idx]
    gray_dist = _color_distance(r, g, b, gray_value, gray_value, gray_value)

    # Only consider grayscale if the colour is nearly neutral (spread < 10) AND
    # grayscale is actually closer -- otherwise the cube preserves the tint.
    spread = max(r, g, b) - min(r, g, b)
    if spread < 10 and gray_dist < cube_dist:
        return 232 + gray_idx
    return cube_index


def hex_to_256(hex_color: str) -> int:
    r, g, b = hex_to_rgb(hex_color)
    return _rgb_to_256(r, g, b)


def _fg_ansi(color: str | int, mode: ColorMode) -> str:
    if color == "":
        return "\x1b[39m"
    if isinstance(color, int):
        return f"\x1b[38;5;{color}m"
    if color.startswith("#"):
        if mode == "truecolor":
            r, g, b = hex_to_rgb(color)
            return f"\x1b[38;2;{r};{g};{b}m"
        return f"\x1b[38;5;{hex_to_256(color)}m"
    raise ValueError(f"Invalid color value: {color}")


def _bg_ansi(color: str | int, mode: ColorMode) -> str:
    if color == "":
        return "\x1b[49m"
    if isinstance(color, int):
        return f"\x1b[48;5;{color}m"
    if color.startswith("#"):
        if mode == "truecolor":
            r, g, b = hex_to_rgb(color)
            return f"\x1b[48;2;{r};{g};{b}m"
        return f"\x1b[48;5;{hex_to_256(color)}m"
    raise ValueError(f"Invalid color value: {color}")


def _resolve_var_refs(
    value: str | int,
    variables: dict[str, str | int],
    visited: frozenset[str] = frozenset(),
) -> str | int:
    """Follow a ``colors`` entry through ``vars`` until it is a literal."""
    if isinstance(value, int) or value == "" or value.startswith("#"):
        return value
    if value in visited:
        raise ValueError(f"Circular variable reference detected: {value}")
    if value not in variables:
        raise ValueError(f"Unknown color variable: {value}")
    return _resolve_var_refs(variables[value], variables, visited | {value})


# ============================================================================
# Theme
# ============================================================================

#: Tokens that resolve to a background colour rather than a foreground one.
_BG_TOKENS = (
    "selectedBg",
    "userMessageBg",
    "customMessageBg",
    "toolPendingBg",
    "toolSuccessBg",
    "toolErrorBg",
)


class Theme:
    """A resolved palette: token name -> the ANSI prefix that selects it."""

    def __init__(
        self,
        fg_colors: dict[str, str | int],
        bg_colors: dict[str, str | int],
        mode: ColorMode,
        *,
        name: str | None = None,
        source_path: str | None = None,
    ) -> None:
        self.name = name
        self.source_path = source_path
        self._mode: ColorMode = mode
        self._fg = {key: _fg_ansi(value, mode) for key, value in fg_colors.items()}
        self._bg = {key: _bg_ansi(value, mode) for key, value in bg_colors.items()}

    def get_color_mode(self) -> ColorMode:
        return self._mode

    def fg(self, color: str, text: str) -> str:
        ansi = self._fg.get(color)
        if not ansi:
            raise ValueError(f"Unknown theme color: {color}")
        return f"{ansi}{text}\x1b[39m"  # Reset only foreground color

    def bg(self, color: str, text: str) -> str:
        ansi = self._bg.get(color)
        if not ansi:
            raise ValueError(f"Unknown theme background color: {color}")
        return f"{ansi}{text}\x1b[49m"  # Reset only background color

    def bold(self, text: str) -> str:
        return f"\x1b[1m{text}\x1b[22m"

    def blink(self, text: str) -> str:
        # Terminal-native blink (SGR 5). Zero overhead; terminals that support
        # blink will animate, others render it as a steady glyph.
        return f"\x1b[5m{text}\x1b[25m"

    def italic(self, text: str) -> str:
        return f"\x1b[3m{text}\x1b[23m"

    def underline(self, text: str) -> str:
        return f"\x1b[4m{text}\x1b[24m"


@dataclass(frozen=True)
class _ThemeJson:
    name: str
    variables: dict[str, str | int]
    colors: dict[str, str | int]


def _read_theme_json(path: Path) -> _ThemeJson:
    raw: dict[str, object] = json.loads(path.read_text())
    name = raw.get("name")
    variables = raw.get("vars", {})
    colors = raw.get("colors", {})
    if not isinstance(name, str) or not isinstance(variables, dict) or not isinstance(colors, dict):
        raise ValueError(f"Malformed theme file: {path}")
    return _ThemeJson(name, dict(variables), dict(colors))


def load_builtin_theme(name: str, mode: ColorMode | None = None) -> Theme:
    """Build one of the palettes shipped inside this package."""
    parsed = _read_theme_json(_THEME_DIR / f"{name}.json")
    resolved = {
        token: _resolve_var_refs(value, parsed.variables) for token, value in parsed.colors.items()
    }
    fg_colors = {k: v for k, v in resolved.items() if k not in _BG_TOKENS}
    bg_colors = {k: v for k, v in resolved.items() if k in _BG_TOKENS}
    return Theme(
        fg_colors,
        bg_colors,
        mode if mode is not None else detect_color_mode(),
        name=parsed.name,
    )


_active: Theme | None = None


def get_theme() -> Theme:
    """The active theme, built on first use.

    The TS exports a ``theme`` Proxy so every call site sees the current theme
    without re-importing. Python has no equivalent that keeps pyright happy, so
    call sites ask for it -- which is also why the shell passes colour *closures*
    into `build_compact_wordmark` rather than the theme itself.
    """
    global _active
    if _active is None:
        _active = load_builtin_theme("dark")
    return _active


def set_theme_instance(instance: Theme) -> None:
    global _active
    _active = instance


def get_select_list_theme() -> SelectListTheme:
    theme = get_theme()
    return SelectListTheme(
        selected_prefix=lambda text: theme.fg("accent", text),
        selected_text=lambda text: theme.fg("accent", text),
        description=lambda text: theme.fg("muted", text),
        scroll_info=lambda text: theme.fg("muted", text),
        no_match=lambda text: theme.fg("muted", text),
    )


def get_editor_theme() -> EditorTheme:
    theme = get_theme()
    border_color: Callable[[str], str] = lambda text: theme.fg("borderMuted", text)  # noqa: E731
    return EditorTheme(border_color=border_color, select_list=get_select_list_theme())


def get_markdown_theme() -> MarkdownTheme:
    """Build the markdown theme from the active palette.

    Port of ``getMarkdownTheme()`` from theme.ts. The syntax highlighter
    is omitted for now -- ``highlight()`` depends on cli-highlight, which
    is not ported yet. Unhighlighted code blocks are styled with the
    ``mdCodeBlock`` colour.
    """
    theme = get_theme()
    return MarkdownTheme(
        heading=lambda text: theme.fg("mdHeading", text),
        link=lambda text: theme.fg("mdLink", text),
        link_url=lambda text: theme.fg("mdLinkUrl", text),
        code=lambda text: theme.fg("mdCode", text),
        code_block=lambda text: theme.fg("mdCodeBlock", text),
        code_block_border=lambda text: theme.fg("mdCodeBlockBorder", text),
        quote=lambda text: theme.fg("mdQuote", text),
        quote_border=lambda text: theme.fg("mdQuoteBorder", text),
        hr=lambda text: theme.fg("mdHr", text),
        list_bullet=lambda text: theme.fg("mdListBullet", text),
        bold=theme.bold,
        italic=theme.italic,
        underline=theme.underline,
        strikethrough=theme.italic,  # Terminal fallback: italic for strikethrough
        highlight_code=None,  # Not ported yet
    )
