"""Google Generative AI (Gemini) provider leaf.

Ports hoocode's ``providers/google-shared.ts``, ``providers/google.ts``,
and ``providers/google-vertex.ts``.
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
from cortex.ai.providers.google.vertex import (
    GoogleVertexClient,
    GoogleVertexOptions,
    stream_google_vertex,
    stream_simple_google_vertex,
)
from cortex.ai.providers.google.vertex import (
    build_params as build_vertex_params,
)
from cortex.ai.providers.google.vertex import (
    create_client as create_vertex_client,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "GoogleClient",
    "GoogleOptions",
    "GoogleVertexClient",
    "GoogleVertexOptions",
    "build_params",
    "build_vertex_params",
    "convert_messages",
    "convert_tools",
    "create_client",
    "create_vertex_client",
    "is_thinking_part",
    "map_stop_reason",
    "map_stop_reason_string",
    "map_tool_choice",
    "requires_tool_call_id",
    "retain_thought_signature",
    "stream_google",
    "stream_google_vertex",
    "stream_simple_google",
    "stream_simple_google_vertex",
]
