"""Components specific to interactive mode.

Port of ``modes/interactive/components/``. Step 7.3 adds the custom editor
and the user message component, 7.5 the assistant message; the tool-execution
and selector components arrive with steps 7.6–7.9.
"""

from cortex.code.interactive.components.assistant_message import (
    AssistantMessageComponent,
    segment_streaming_markdown,
)
from cortex.code.interactive.components.custom_editor import CustomEditor, is_plain_text
from cortex.code.interactive.components.footer import (
    FooterComponent,
    FooterState,
    assemble_line,
    context_gauge,
    format_tokens,
)
from cortex.code.interactive.components.user_message import UserMessageComponent

__all__ = [
    "AssistantMessageComponent",
    "CustomEditor",
    "FooterComponent",
    "FooterState",
    "UserMessageComponent",
    "assemble_line",
    "context_gauge",
    "format_tokens",
    "is_plain_text",
    "segment_streaming_markdown",
]
