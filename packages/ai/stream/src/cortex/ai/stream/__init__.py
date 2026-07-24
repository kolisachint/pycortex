"""AI streaming API — port of hoocode's `packages/ai/src/stream.ts`."""

from cortex.ai.env import get_env_api_key
from cortex.ai.stream.event_stream import (
    AssistantMessageEventStream,
    EventStream,
    create_assistant_message_event_stream,
)
from cortex.ai.stream.stream import complete, complete_simple, stream, stream_simple

__all__ = [
    "AssistantMessageEventStream",
    "EventStream",
    "complete",
    "complete_simple",
    "create_assistant_message_event_stream",
    "get_env_api_key",
    "stream",
    "stream_simple",
]
