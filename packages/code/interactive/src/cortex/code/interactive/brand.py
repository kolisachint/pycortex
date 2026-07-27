"""HooCode brand identity — the product mark and the shared glyph vocabulary.

Port of ``modes/interactive/brand.ts``.

The brand *colour* is the theme's ``accent`` token (see :mod:`cortex.code.interactive.theme`)
so it stays theme-aware and swappable; this module owns the non-colour identity:
the mark and the category glyphs used by the footer, the startup resource
summary, and anywhere a capability class is labelled. Keeping them here means a
future rebrand touches one file, not twenty.

Glyphs are chosen to be single terminal cell, widely supported, and distinct from
the task-panel's own status/owner glyphs (◐ ◆ ◇ ▸ ⧉ ✓ ✗ ○) so the two
vocabularies never read as the same signal.
"""

from __future__ import annotations

from typing import Final, Literal

__all__ = [
    "BRAND_MARK",
    "BRAND_NAME",
    "CATEGORY_GLYPH",
    "GIT_BRANCH_GLYPH",
    "GIT_DIRTY_MARK",
    "SEGMENT_SEP",
    "CategoryKey",
]

#: The HooCode mark — a filled hexagon, rendered in the accent colour.
BRAND_MARK: Final = "⬢"

#: Product name, for splashes and headers.
BRAND_NAME: Final = "HooCode"

CategoryKey = Literal[
    "skills", "commands", "agents", "mcp", "plugins", "themes", "context", "extensions"
]

#: Glyphs for the capability classes a session can load. Used by the startup
#: "resources ready" summary and reusable anywhere a class needs a label. Each is
#: a single cell so counts and columns stay aligned.
CATEGORY_GLYPH: Final[dict[CategoryKey, str]] = {
    "skills": "✦",
    "commands": "⌘",
    "agents": "◈",
    "mcp": "⧉",
    "plugins": "⬡",
    "themes": "◒",
    "context": "❯",
    "extensions": "⊹",
}

#: A soft dot separator used between footer/summary segments.
SEGMENT_SEP: Final = "·"

#: Marker appended to a git branch when the working tree is dirty.
GIT_DIRTY_MARK: Final = "*"

#: Fork glyph preceding a git branch.
GIT_BRANCH_GLYPH: Final = "⑂"
