"""Components specific to interactive mode.

Port of ``modes/interactive/components/``. Step 7.3 adds the custom editor and
the user message component, 7.5 the assistant message, 7.6 the tool-execution
block with its diff colouring and the bash-mode block; the selector components
arrive with 7.9.
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
from cortex.code.interactive.components.tool_execution import (
    PrefixFirstLine,
    ToolExecutionComponent,
    ToolExecutionOptions,
    ToolExecutionResult,
)
from cortex.code.interactive.components.user_message import UserMessageComponent
from cortex.code.interactive.components.visual_truncate import (
    VisualTruncateResult,
    truncate_to_visual_lines,
)

__all__ = [
    "PREVIEW_LINES",
    "AssistantMessageComponent",
    "BashExecutionComponent",
    "CustomEditor",
    "FooterComponent",
    "PrefixFirstLine",
    "ToolExecutionComponent",
    "ToolExecutionOptions",
    "ToolExecutionResult",
    "UserMessageComponent",
    "VisualTruncateResult",
    "app_key_label",
    "assemble_line",
    "context_gauge",
    "format_key_text",
    "format_tokens",
    "is_plain_text",
    "key_display_text",
    "key_hint",
    "key_text",
    "matches_app_key",
    "parse_diff_line",
    "raw_key_hint",
    "render_diff",
    "segment_streaming_markdown",
    "truncate_to_visual_lines",
]
