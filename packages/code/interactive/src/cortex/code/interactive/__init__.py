"""Interactive mode: the TUI application ``pycortex`` starts.

Port of ``packages/coding-agent/src/modes/interactive/``. Step 7.2 lands the
shell — the component tree, the startup banner, the editor prompt, the footer and
the Ctrl+C exit path; 7.3 the editor and chat log, 7.4 the session bridge, 7.5
streaming, 7.6 the tool blocks, their diffs and bash mode, 7.7 the footer in
full, and 7.8 the slash commands and their autocomplete. Overlays follow in 7.9.
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
from cortex.code.interactive.changelog import (
    ChangelogEntry,
    compare_versions,
    get_new_entries,
    parse_changelog,
)
from cortex.code.interactive.command_executor import CommandContext, CommandExecutor
from cortex.code.interactive.components import (
    BashExecutionComponent,
    DynamicBorder,
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
    BuiltInSlashCommand,
    InteractiveMode,
    InteractiveModeOptions,
    build_app_root,
    format_display_path,
    resolve_fd_path,
    run_interactive_mode,
)
from cortex.code.interactive.keybindings import (
    KEYBINDINGS,
    AppKeybinding,
    KeybindingsManager,
    migrate_keybindings_config,
    order_keybindings_config,
)
from cortex.code.interactive.slash_commands import (
    BUILTIN_SLASH_COMMANDS,
    BuiltinSlashCommand,
    SlashCommandInfo,
    SlashCommandSource,
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
    "AppKeybinding",
    "BRAND_MARK",
    "BRAND_NAME",
    "BUILTIN_SLASH_COMMANDS",
    "BUILT_IN_TOOL_RENDERERS",
    "BashExecutionComponent",
    "BashExecutionController",
    "BashExecutionControllerDeps",
    "BuiltInSlashCommand",
    "BuiltinSlashCommand",
    "CATEGORY_GLYPH",
    "ChangelogEntry",
    "CommandContext",
    "CommandExecutor",
    "CompactWordmarkOptions",
    "DownloadProgress",
    "DynamicBorder",
    "ErrorProgress",
    "FooterComponent",
    "FooterDataProvider",
    "GIT_BRANCH_GLYPH",
    "GIT_DIRTY_MARK",
    "InteractiveMode",
    "InteractiveModeOptions",
    "KEYBINDINGS",
    "KeybindingsManager",
    "ReadonlyFooterDataProvider",
    "SEGMENT_SEP",
    "SlashCommandInfo",
    "SlashCommandSource",
    "StartupProgress",
    "StartupProgressStore",
    "Theme",
    "ToolExecutionComponent",
    "ToolExecutionOptions",
    "ToolExecutionResult",
    "ToolRenderContext",
    "ToolRenderResultOptions",
    "ToolRenderer",
    "WORDMARK",
    "WORDMARK_COMPACT",
    "WORDMARK_GLYPH",
    "WorkProgress",
    "build_app_root",
    "build_compact_wordmark",
    "compare_versions",
    "format_display_path",
    "get_new_entries",
    "get_theme",
    "migrate_keybindings_config",
    "order_keybindings_config",
    "parse_changelog",
    "render_diff",
    "resolve_fd_path",
    "resolve_tool_renderer",
    "run_interactive_mode",
    "startup_progress",
]
