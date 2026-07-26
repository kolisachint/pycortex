"""Agent types.

Mechanical port of hoocode's ``packages/agent/src/types.ts``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from cortex.ai.types import (
    AssistantMessage,
    ImageContent,
    Message,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
)

# Type variables for generic types
T = TypeVar("T")
TParameters = TypeVar("TParameters")
TDetails = TypeVar("TDetails")

__all__ = [
    "AgentContext",
    "AgentEvent",
    "AgentMessage",
    "AgentState",
    "AgentTool",
    "AgentToolCall",
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "AfterToolCallContext",
    "AfterToolCallResult",
    "BackgroundToolResult",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "PrepareNextTurnContext",
    "ShouldStopAfterTurnContext",
    "StreamFn",
    "ThinkingLevel",
    "ToolExecutionMode",
]


# ---------------------------------------------------------------------------
# Stream function type
# ---------------------------------------------------------------------------

StreamFn = Callable[..., Any]
"""Stream function used by the agent loop.

Contract:
- Must not throw or return a rejected promise for request/model/runtime failures.
- Must return an AssistantMessageEventStream.
- Failures must be encoded in the returned stream via protocol events and a
  final AssistantMessage with stopReason "error" or "aborted" and errorMessage.
"""


# ---------------------------------------------------------------------------
# Tool execution mode
# ---------------------------------------------------------------------------

ToolExecutionMode = Literal["sequential", "parallel"]
"""Configuration for how tool calls from a single assistant message are executed.

- "sequential": each tool call is prepared, executed, and finalized before the next one starts.
- "parallel": tool calls are prepared sequentially, then allowed tools execute concurrently.
"""


# ---------------------------------------------------------------------------
# Agent tool call
# ---------------------------------------------------------------------------

AgentToolCall = Tool
"""A single tool call content block emitted by an assistant message."""


# ---------------------------------------------------------------------------
# Background tool result
# ---------------------------------------------------------------------------


@dataclass
class BackgroundToolResult:
    """A finished background tool call, passed to `create_background_result_message`."""

    tool_call: AgentToolCall
    """The originating tool call block from the assistant message."""

    result: AgentToolResult[Any]
    """The executed tool result (after any `after_tool_call` overrides)."""

    is_error: bool
    """Whether the executed result is treated as an error."""


# ---------------------------------------------------------------------------
# Before tool call result
# ---------------------------------------------------------------------------


@dataclass
class BeforeToolCallResult:
    """Result returned from `before_tool_call`.

    Returning `{ block: True }` prevents the tool from executing.
    """

    block: bool = False
    reason: str | None = None


# ---------------------------------------------------------------------------
# After tool call result
# ---------------------------------------------------------------------------


@dataclass
class AfterToolCallResult:
    """Partial override returned from `after_tool_call`.

    Merge semantics are field-by-field:
    - `content`: if provided, replaces the tool result content array in full
    - `details`: if provided, replaces the tool result details value in full
    - `is_error`: if provided, replaces the tool result error flag
    - `terminate`: if provided, replaces the early-termination hint
    """

    content: list[TextContent | ImageContent] | None = None
    details: Any = None
    is_error: bool | None = None
    terminate: bool | None = None


# ---------------------------------------------------------------------------
# Before tool call context
# ---------------------------------------------------------------------------


@dataclass
class BeforeToolCallContext:
    """Context passed to `before_tool_call`."""

    assistant_message: AssistantMessage
    """The assistant message that requested the tool call."""

    tool_call: AgentToolCall
    """The raw tool call block from `assistant_message.content`."""

    args: Any
    """Validated tool arguments for the target tool schema."""

    context: AgentContext
    """Current agent context at the time the tool call is prepared."""


# ---------------------------------------------------------------------------
# After tool call context
# ---------------------------------------------------------------------------


@dataclass
class AfterToolCallContext:
    """Context passed to `after_tool_call`."""

    assistant_message: AssistantMessage
    """The assistant message that requested the tool call."""

    tool_call: AgentToolCall
    """The raw tool call block from `assistant_message.content`."""

    args: Any
    """Validated tool arguments for the target tool schema."""

    result: AgentToolResult[Any]
    """The executed tool result before any `after_tool_call` overrides are applied."""

    is_error: bool
    """Whether the executed tool result is currently treated as an error."""

    context: AgentContext
    """Current agent context at the time the tool call is finalized."""


# ---------------------------------------------------------------------------
# Should stop after turn context
# ---------------------------------------------------------------------------


@dataclass
class ShouldStopAfterTurnContext:
    """Context passed to `should_stop_after_turn`."""

    message: AssistantMessage
    """The assistant message that completed the turn."""

    tool_results: list[ToolResultMessage]
    """Tool result messages passed to the preceding `turn_end` event."""

    context: AgentContext
    """Current agent context after the turn's assistant message
    and tool results have been appended."""

    new_messages: list[AgentMessage]
    """Messages that this loop invocation will return if it exits at this point."""


# ---------------------------------------------------------------------------
# Prepare next turn context
# ---------------------------------------------------------------------------


@dataclass
class PrepareNextTurnContext(ShouldStopAfterTurnContext):
    """Context passed to `prepare_next_turn`."""


# ---------------------------------------------------------------------------
# Agent loop turn update
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopTurnUpdate:
    """Replacement runtime state used by the agent loop before starting another provider request."""

    context: AgentContext | None = None
    model: Model[Any] | None = None
    thinking_level: ThinkingLevel | None = None


# ---------------------------------------------------------------------------
# Thinking level
# ---------------------------------------------------------------------------

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
"""Thinking/reasoning level for models that support it."""


# ---------------------------------------------------------------------------
# Agent message
# ---------------------------------------------------------------------------

AgentMessage = Message | Any
"""AgentMessage: Union of LLM messages + custom messages."""


# ---------------------------------------------------------------------------
# Agent tool result
# ---------------------------------------------------------------------------


@dataclass
class AgentToolResult(Generic[T]):
    """Final or partial result produced by a tool."""

    content: list[TextContent | ImageContent]
    """Text or image content returned to the model."""

    details: T
    """Arbitrary structured details for logs or UI rendering."""

    terminate: bool | None = None
    """Hint that the agent should stop after the current tool batch."""


# ---------------------------------------------------------------------------
# Agent tool update callback
# ---------------------------------------------------------------------------

AgentToolUpdateCallback = Callable[[AgentToolResult[Any]], None]
"""Callback used by tools to stream partial execution updates."""


# ---------------------------------------------------------------------------
# Agent tool
# ---------------------------------------------------------------------------


@dataclass
class AgentTool(Generic[TParameters, TDetails]):
    """Tool definition used by the agent runtime."""

    name: str
    """Tool name."""

    description: str
    """Tool description."""

    parameters: TParameters | None = None
    """Tool parameters schema."""

    label: str = ""
    """Human-readable label for UI display."""

    prepare_arguments: Callable[[Any], Any] | None = None
    """Optional compatibility shim for raw tool-call arguments before schema validation."""

    execute: Callable[..., Any] | None = None
    """Execute the tool call. Throw on failure instead of encoding errors in `content`."""

    execution_mode: ToolExecutionMode | None = None
    """Per-tool execution mode override."""

    background: bool | Callable[[AgentToolCall], bool] | None = None
    """When true, the agent loop treats this tool as non-blocking."""


# ---------------------------------------------------------------------------
# Agent context
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Context snapshot passed into the low-level agent loop."""

    system_prompt: str
    """System prompt included with the request."""

    messages: list[AgentMessage]
    """Transcript visible to the model."""

    tools: list[AgentTool[Any, Any]] | None = None
    """Tools available for this run."""


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------


@dataclass
class AgentState:
    """Public agent state."""

    system_prompt: str = ""
    """System prompt sent with each model request."""

    model: Model[Any] | None = None
    """Active model used for future turns."""

    thinking_level: ThinkingLevel = "off"
    """Requested reasoning level for future turns."""

    tools: list[AgentTool[Any, Any]] = field(default_factory=list)
    """Available tools."""

    messages: list[AgentMessage] = field(default_factory=list)
    """Conversation transcript."""

    is_streaming: bool = False
    """True while the agent is processing a prompt or continuation."""

    streaming_message: AgentMessage | None = None
    """Partial assistant message for the current streamed response, if any."""

    pending_tool_calls: frozenset[str] = field(default_factory=frozenset)
    """Tool call ids currently executing."""

    error_message: str | None = None
    """Error message from the most recent failed or aborted assistant turn, if any."""


# ---------------------------------------------------------------------------
# Agent event
# ---------------------------------------------------------------------------

AgentEvent = Any  # type: ignore[assignment]
"""Events emitted by the Agent for UI updates.

In practice, this is a union of event dataclasses:
- AgentStartEvent
- AgentEndEvent
- TurnStartEvent
- TurnEndEvent
- MessageStartEvent
- MessageUpdateEvent
- MessageEndEvent
- ToolExecutionStartEvent
- ToolExecutionUpdateEvent
- ToolExecutionEndEvent
"""


# ---------------------------------------------------------------------------
# Agent loop config (referenced by other modules)
# ---------------------------------------------------------------------------


@dataclass
class AgentLoopConfig:
    """Configuration for the agent loop."""

    model: Model[Any] | None = None
    convert_to_llm: Callable[..., Any] | None = None
    transform_context: Callable[..., Any] | None = None
    get_api_key: Callable[..., Any] | None = None
    should_stop_after_turn: Callable[..., Any] | None = None
    prepare_next_turn: Callable[..., Any] | None = None
    get_steering_messages: Callable[..., Any] | None = None
    get_follow_up_messages: Callable[..., Any] | None = None
    create_background_result_message: Callable[..., Any] | None = None
    create_background_placeholder: Callable[..., Any] | None = None
    on_background_task_count_change: Callable[..., Any] | None = None
    tool_execution: ToolExecutionMode = "parallel"
    before_tool_call: Callable[..., Any] | None = None
    after_tool_call: Callable[..., Any] | None = None
