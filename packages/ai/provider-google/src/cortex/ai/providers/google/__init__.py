"""Google Generative AI (Gemini) provider leaf.

Ports hoocode's ``providers/google-shared.ts`` and ``providers/google.ts``.
``google-vertex.ts`` lands separately in step 2.16.
"""

from cortex.ai.providers.google.google import (
    DEFAULT_BASE_URL,
    GoogleClient,
    GoogleOptions,
    build_params,
    create_client,
    stream_google,
    stream_simple_google,
)
from cortex.ai.providers.google.shared import (
    convert_messages,
    convert_tools,
    is_thinking_part,
    map_stop_reason,
    map_stop_reason_string,
    map_tool_choice,
    requires_tool_call_id,
    retain_thought_signature,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "GoogleClient",
    "GoogleOptions",
    "build_params",
    "convert_messages",
    "convert_tools",
    "create_client",
    "is_thinking_part",
    "map_stop_reason",
    "map_stop_reason_string",
    "map_tool_choice",
    "requires_tool_call_id",
    "retain_thought_signature",
    "stream_google",
    "stream_simple_google",
]
