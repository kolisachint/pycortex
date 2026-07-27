"""Interactive mode: the TUI application ``pycortex`` starts.

Port of ``packages/coding-agent/src/modes/interactive/``. Step 7.2 lands the
shell — the component tree, the startup banner, the editor prompt, the footer and
the Ctrl+C exit path. Chat, streaming, tools, commands and overlays follow in
7.3–7.9.
"""

from cortex.code.interactive.brand import (
    BRAND_MARK,
    BRAND_NAME,
    CATEGORY_GLYPH,
    GIT_BRANCH_GLYPH,
    GIT_DIRTY_MARK,
    SEGMENT_SEP,
)
from cortex.code.interactive.components import FooterComponent, FooterState
from cortex.code.interactive.interactive_mode import (
    InteractiveMode,
    InteractiveModeOptions,
    build_app_root,
    format_display_path,
    run_interactive_mode,
)
from cortex.code.interactive.theme import Theme, get_theme
from cortex.code.interactive.wordmark import (
    WORDMARK,
    WORDMARK_COMPACT,
    WORDMARK_GLYPH,
    CompactWordmarkOptions,
    build_compact_wordmark,
)

__all__ = [
    "BRAND_MARK",
    "BRAND_NAME",
    "CATEGORY_GLYPH",
    "GIT_BRANCH_GLYPH",
    "GIT_DIRTY_MARK",
    "SEGMENT_SEP",
    "WORDMARK",
    "WORDMARK_COMPACT",
    "WORDMARK_GLYPH",
    "CompactWordmarkOptions",
    "FooterComponent",
    "FooterState",
    "InteractiveMode",
    "InteractiveModeOptions",
    "Theme",
    "build_app_root",
    "build_compact_wordmark",
    "format_display_path",
    "get_theme",
    "run_interactive_mode",
]
