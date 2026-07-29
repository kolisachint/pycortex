"""Interactive mode: the TUI application ``pycortex`` starts.

Port of ``packages/coding-agent/src/modes/interactive/``. Step 7.2 lands the
shell — the component tree, the startup banner, the editor prompt, the footer and
the Ctrl+C exit path; 7.3 the editor and chat log, 7.4 the session bridge, 7.5
streaming, and 7.6 the tool blocks, their diffs and bash mode. Commands and
overlays follow in 7.7–7.9.
"""

from cortex.code.interactive.bash_execution_controller import (
    BashExecutionController,
    BashExecutionControllerDeps,
)
from cortex.code.interactive.brand import (
    BRAND_MARK,
    BRAND_NAME,
    CATEGORY_GLYPH,
    GIT_BRANCH_GLYPH,
    GIT_DIRTY_MARK,
    SEGMENT_SEP,
)
from cortex.code.interactive.components import (
    BashExecutionComponent,
    FooterComponent,
    ToolExecutionComponent,
    ToolExecutionOptions,
    ToolExecutionResult,
    render_diff,
)
from cortex.code.interactive.footer_data_provider import (
    FooterDataProvider,
    ReadonlyFooterDataProvider,
)
from cortex.code.interactive.interactive_mode import (
    InteractiveMode,
    InteractiveModeOptions,
    build_app_root,
    format_display_path,
    run_interactive_mode,
)
from cortex.code.interactive.keybindings import (
    KEYBINDINGS,
    AppKeybinding,
    KeybindingsManager,
    migrate_keybindings_config,
    order_keybindings_config,
)
from cortex.code.interactive.startup_progress import (
    DownloadProgress,
    ErrorProgress,
    StartupProgress,
    StartupProgressStore,
    WorkProgress,
    startup_progress,
)
from cortex.code.interactive.theme import Theme, get_theme
from cortex.code.interactive.tool_renderers import (
    BUILT_IN_TOOL_RENDERERS,
    ToolRenderContext,
    ToolRenderer,
    ToolRenderResultOptions,
    resolve_tool_renderer,
)
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
    "BUILT_IN_TOOL_RENDERERS",
    "CATEGORY_GLYPH",
    "GIT_BRANCH_GLYPH",
    "GIT_DIRTY_MARK",
    "KEYBINDINGS",
    "SEGMENT_SEP",
    "WORDMARK",
    "WORDMARK_COMPACT",
    "WORDMARK_GLYPH",
    "AppKeybinding",
    "BashExecutionComponent",
    "BashExecutionController",
    "BashExecutionControllerDeps",
    "CompactWordmarkOptions",
    "DownloadProgress",
    "ErrorProgress",
    "FooterComponent",
    "FooterDataProvider",
    "InteractiveMode",
    "InteractiveModeOptions",
    "KeybindingsManager",
    "ReadonlyFooterDataProvider",
    "StartupProgress",
    "StartupProgressStore",
    "Theme",
    "ToolExecutionComponent",
    "ToolExecutionOptions",
    "ToolExecutionResult",
    "ToolRenderContext",
    "ToolRenderResultOptions",
    "ToolRenderer",
    "WorkProgress",
    "build_app_root",
    "build_compact_wordmark",
    "format_display_path",
    "get_theme",
    "migrate_keybindings_config",
    "order_keybindings_config",
    "render_diff",
    "resolve_tool_renderer",
    "run_interactive_mode",
    "startup_progress",
]
