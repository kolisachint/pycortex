"""Components specific to interactive mode.

Port of ``modes/interactive/components/``. Step 7.3 adds the custom editor and
the user message component, 7.5 the assistant message, 7.6 the tool-execution
block with its diff colouring and the bash-mode block, and 7.8 the rule the
command output is framed between, 7.9 the overlays — the model picker, the
cycling-scope picker, the settings screen and the theme list — and 7.10 the three
that load a different point in the session: the session list, the fork picker and
the session tree.
"""

from cortex.code.interactive.components.assistant_message import (
    AssistantMessageComponent,
    segment_streaming_markdown,
)
from cortex.code.interactive.components.bash_execution import (
    PREVIEW_LINES,
    BashExecutionComponent,
)
from cortex.code.interactive.components.custom_editor import CustomEditor, is_plain_text
from cortex.code.interactive.components.diff import parse_diff_line, render_diff
from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.components.footer import (
    FooterComponent,
    assemble_line,
    context_gauge,
    format_tokens,
)
from cortex.code.interactive.components.keybinding_hints import (
    app_key_label,
    format_key_text,
    key_display_text,
    key_hint,
    key_text,
    matches_app_key,
    raw_key_hint,
)
from cortex.code.interactive.components.model_selector import (
    ModelItem,
    ModelSelectorComponent,
)
from cortex.code.interactive.components.scoped_models_selector import (
    ModelsCallbacks,
    ModelsConfig,
    ScopedModelsSelectorComponent,
)
from cortex.code.interactive.components.session_selector import (
    SessionSelectorComponent,
    SessionsLoader,
    canonicalize_path,
    format_session_date,
)
from cortex.code.interactive.components.session_selector_search import (
    MatchResult,
    NameFilter,
    ParsedSearchQuery,
    SortMode,
    filter_and_sort_sessions,
    has_session_name,
    match_session,
    parse_search_query,
)
from cortex.code.interactive.components.settings_selector import (
    FlagInfo,
    SettingsCallbacks,
    SettingsConfig,
    SettingsSelectorComponent,
    ToolGroupInfo,
    ToolToggleInfo,
)
from cortex.code.interactive.components.theme_selector import ThemeSelectorComponent
from cortex.code.interactive.components.tool_execution import (
    PrefixFirstLine,
    ToolExecutionComponent,
    ToolExecutionOptions,
    ToolExecutionResult,
)
from cortex.code.interactive.components.tree_selector import (
    FilterMode,
    TreeSelectorComponent,
)
from cortex.code.interactive.components.user_message import UserMessageComponent
from cortex.code.interactive.components.user_message_selector import (
    UserMessageItem,
    UserMessageSelectorComponent,
)
from cortex.code.interactive.components.visual_truncate import (
    VisualTruncateResult,
    truncate_to_visual_lines,
)

__all__ = [
    "AssistantMessageComponent",
    "BashExecutionComponent",
    "CustomEditor",
    "DynamicBorder",
    "FilterMode",
    "FlagInfo",
    "FooterComponent",
    "MatchResult",
    "ModelItem",
    "ModelSelectorComponent",
    "ModelsCallbacks",
    "ModelsConfig",
    "NameFilter",
    "PREVIEW_LINES",
    "ParsedSearchQuery",
    "PrefixFirstLine",
    "ScopedModelsSelectorComponent",
    "SessionSelectorComponent",
    "SessionsLoader",
    "SettingsCallbacks",
    "SettingsConfig",
    "SettingsSelectorComponent",
    "SortMode",
    "ThemeSelectorComponent",
    "ToolExecutionComponent",
    "ToolExecutionOptions",
    "ToolExecutionResult",
    "ToolGroupInfo",
    "ToolToggleInfo",
    "TreeSelectorComponent",
    "UserMessageComponent",
    "UserMessageItem",
    "UserMessageSelectorComponent",
    "VisualTruncateResult",
    "app_key_label",
    "assemble_line",
    "canonicalize_path",
    "context_gauge",
    "filter_and_sort_sessions",
    "format_key_text",
    "format_session_date",
    "format_tokens",
    "has_session_name",
    "is_plain_text",
    "key_display_text",
    "key_hint",
    "key_text",
    "match_session",
    "matches_app_key",
    "parse_diff_line",
    "parse_search_query",
    "raw_key_hint",
    "render_diff",
    "segment_streaming_markdown",
    "truncate_to_visual_lines",
]
