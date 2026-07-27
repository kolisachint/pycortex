"""ASCII wordmark for the startup banner.

Port of ``core/wordmark.ts``. Rendered in the terminal with accent colour on the
"hoo" portion. Source: ``design-system/assets/wordmark.txt``.

``WORDMARK_SYMBOL`` is deliberately not ported: it is a half-block owl rendered
from ``wordmark-symbol.generated.ts``, an 8 KB *generated* asset that nothing in
the TS imports — ``buildCompactWordmark`` is the only export interactive mode
uses, and it draws :data:`WORDMARK_GLYPH` instead. Porting a generated blob by
hand would give this repo a second, unregenerable copy of it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "WORDMARK",
    "WORDMARK_COMPACT",
    "WORDMARK_GLYPH",
    "CompactWordmarkOptions",
    "build_compact_wordmark",
]

WORDMARK: Final = "\n".join(
    [
        "                   __  __            ______          __",
        "                  / / / /___  ____  / ____/___  ____/ /__",
        "                 / /_/ / __ \\/ __ \\/ /   / __ \\/ __  / _ \\",
        "                / __  / /_/ / /_/ / /___/ /_/ / /_/ /  __/",
        "               /_/ /_/\\____/\\____/\\____/\\____/\\__,_/\\___/",
        "",
        "               deterministic terminal coding agent   >  hoo",
    ]
)

#: Compact one-line logo for tight spaces.
WORDMARK_COMPACT: Final = "hoo — deterministic terminal coding agent"

#: Compact three-line owl glyph rendered beside the brand text in the banner.
WORDMARK_GLYPH: Final = ["▟▀▀▀▀▀▙", "▌▟▙ ▟▙▐", "▜▄▄▄▄▄▛"]

_GLYPH_INDENT: Final = " " * 3
_GLYPH_GAP: Final = " " * 2


@dataclass
class CompactWordmarkOptions:
    """Inputs to :func:`build_compact_wordmark`.

    The colour functions are injected rather than imported so the wordmark stays
    free of the theme — exactly as in the TS, where the caller passes
    ``theme.fg("accent", …)`` closures in.
    """

    app_name: str
    version: str
    cwd: str
    #: Colorize the brand "hoo" portion / glyph.
    accent: Callable[[str], str]
    #: Colorize secondary text (tagline, version, cwd).
    dim: Callable[[str], str]
    #: Colorize separators and the glyph outline.
    muted: Callable[[str], str]
    #: Tagline shown next to the version.
    tagline: str | None = None
    #: Render the trailing blinking cursor (e.g. blink + accent). Optional.
    cursor: Callable[[str], str] | None = None
    #: Optional note appended to the cwd line (e.g. a keybinding hint).
    note: Callable[[], str] | None = None


def build_compact_wordmark(options: CompactWordmarkOptions) -> str:
    """Build the compact startup banner.

    A small owl glyph beside the brand name, tagline + version, and the working
    directory::

        ▟▀▀▀▀▀▙  hoocode
        ▌▟▙ ▟▙▐  agentic coding agent · v0.1.0
        ▜▄▄▄▄▄▛  ~/project
    """
    tagline = options.tagline if options.tagline is not None else "agentic coding agent"

    # Highlight the "hoo" prefix when present, otherwise accent the whole name.
    app_name = options.app_name
    if app_name.startswith("hoo"):
        name = options.accent("hoo") + options.muted("│") + app_name[3:]
    else:
        name = options.accent(app_name)
    brand = name + options.cursor("_") if options.cursor else name

    cwd_line = (
        f"{options.dim(options.cwd)}{options.note()}" if options.note else options.dim(options.cwd)
    )
    right = [
        brand,
        f"{options.dim(tagline)} {options.muted('·')} {options.dim(f'v{options.version}')}",
        cwd_line,
    ]

    lines: list[str] = []
    for index, glyph_line in enumerate(WORDMARK_GLYPH):
        text = right[index] if index < len(right) else ""
        lines.append(f"{_GLYPH_INDENT}{options.accent(glyph_line)}{_GLYPH_GAP}{text}")
    return "\n".join(lines)
