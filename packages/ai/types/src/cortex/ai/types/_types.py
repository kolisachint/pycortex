"""Shared AI types.

Mechanical port of hoocode's `packages/ai/src/types.ts`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

KnownApi = Literal[
    "openai-completions",
    "openai-responses",
    "azure-openai-responses",
    "openai-codex-responses",
    "anthropic-messages",
    "google-generative-ai",
    "google-vertex",
]
KnownImagesApi = Literal["openrouter-images"]
KnownProvider = Literal[
    "anthropic",
    "google",
    "google-vertex",
    "openai",
    "azure-openai-responses",
    "openai-codex",
    "deepseek",
    "github-copilot",
    "xai",
    "groq",
    "cerebras",
    "openrouter",
    "vercel-ai-gateway",
    "zai",
    "minimax",
    "minimax-cn",
    "moonshotai",
    "moonshotai-cn",
    "huggingface",
    "fireworks",
    "together",
    "opencode",
    "opencode-go",
    "kimi-coding",
    "xiaomi",
    "xiaomi-token-plan-cn",
    "xiaomi-token-plan-ams",
    "xiaomi-token-plan-sgp",
    "nvidia",
]
KnownImagesProvider = Literal["openrouter"]

Api = str
ImagesApi = str
Provider = str
ImagesProvider = str

ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]
ModelThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
CacheRetention = Literal["none", "short", "long"]
Transport = Literal["sse", "websocket", "websocket-cached", "auto"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]
ImagesStopReason = Literal["stop", "error", "aborted"]


class ThinkingBudgets(BaseModel):
    minimal: int | None = None
    low: int | None = None
    medium: int | None = None
    high: int | None = None


class ProviderResponse(BaseModel):
    status: int
    headers: dict[str, str]


class StreamOptions(BaseModel):
    temperature: float | None = None
    max_tokens: int | None = None
    signal: Any | None = None
    api_key: str | None = None
    transport: Transport | None = None
    cache_retention: CacheRetention | None = None
    session_id: str | None = None


class ImagesOptions(BaseModel):
    pass


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None


class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = None
    redacted: bool | None = None


class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str
    mime_type: str


class ToolCall(BaseModel):
    type: Literal["toolCall"] = "toolCall"
    id: str
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = None


class Usage(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_write: int
    total_tokens: int

    class Cost(BaseModel):
        input: float
        output: float
        cache_read: float
        cache_write: float
        total: float

    cost: dict[str, float]


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[TextContent | ImageContent]
    timestamp: int


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[TextContent | ThinkingContent | ToolCall]
    api: Api
    provider: Provider
    model: str
    response_model: str | None = None
    response_id: str | None = None
    diagnostics: list[Any] | None = None
    usage: Usage
    stop_reason: StopReason
    error_message: str | None = None
    timestamp: int


class ToolResultMessage(BaseModel):
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    details: Any | None = None
    is_error: bool
    timestamp: int


Message = UserMessage | AssistantMessage | ToolResultMessage


class ImagesContext(BaseModel):
    input: list[TextContent | ImageContent]


class AssistantImages(BaseModel):
    api: ImagesApi
    provider: ImagesProvider
    model: str
    output: list[TextContent | ImageContent]
    response_id: str | None = None
    usage: Usage | None = None
    stop_reason: ImagesStopReason
    error_message: str | None = None
    timestamp: int


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class Context(BaseModel):
    system_prompt: str | None = None
    messages: list[Message]
    tools: list[Tool] | None = None


class StartEvent(BaseModel):
    type: Literal["start"] = "start"
    partial: AssistantMessage


class TextStartEvent(BaseModel):
    type: Literal["text_start"] = "text_start"
    content_index: int
    partial: AssistantMessage


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class TextEndEvent(BaseModel):
    type: Literal["text_end"] = "text_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ThinkingStartEvent(BaseModel):
    type: Literal["thinking_start"] = "thinking_start"
    content_index: int
    partial: AssistantMessage


class ThinkingDeltaEvent(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ThinkingEndEvent(BaseModel):
    type: Literal["thinking_end"] = "thinking_end"
    content_index: int
    content: str
    partial: AssistantMessage


class ToolCallStartEvent(BaseModel):
    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: int
    partial: AssistantMessage


class ToolCallDeltaEvent(BaseModel):
    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: int
    delta: str
    partial: AssistantMessage


class ToolCallEndEvent(BaseModel):
    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"]
    error: AssistantMessage


AssistantMessageEvent = (
    StartEvent
    | TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ThinkingStartEvent
    | ThinkingDeltaEvent
    | ThinkingEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | DoneEvent
    | ErrorEvent
)


class OpenAICompletionsCompat(BaseModel):
    supports_store: bool | None = None
    supports_developer_role: bool | None = None
    supports_reasoning_effort: bool | None = None
    supports_usage_in_streaming: bool | None = None
    max_tokens_field: Literal["max_completion_tokens", "max_tokens"] | None = None
    requires_tool_result_name: bool | None = None
    requires_assistant_after_tool_result: bool | None = None
    requires_thinking_as_text: bool | None = None
    requires_reasoning_content_on_assistant_messages: bool | None = None
    thinking_format: (
        Literal[
            "openai",
            "openrouter",
            "deepseek",
            "together",
            "zai",
            "qwen",
            "qwen-chat-template",
        ]
        | None
    ) = None
    open_router_routing: dict[str, Any] | None = None
    vercel_gateway_routing: dict[str, Any] | None = None
    zai_tool_stream: bool | None = None
    supports_strict_mode: bool | None = None
    tool_call_constraint: Literal["strict", "none"] | None = None
    cache_control_format: Literal["anthropic"] | None = None
    send_session_affinity_headers: bool | None = None
    supports_long_cache_retention: bool | None = None
    prompt_suffix: str | None = None


class OpenAIResponsesCompat(BaseModel):
    send_session_id_header: bool | None = None
    supports_long_cache_retention: bool | None = None


class AnthropicMessagesCompat(BaseModel):
    supports_eager_tool_input_streaming: bool | None = None
    supports_long_cache_retention: bool | None = None


class Model(BaseModel):
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool
    thinking_level_map: dict[str, str | None] | None = None
    input: list[Literal["text", "image"]]
    cost: dict[str, float]
    context_window: int
    max_tokens: int
    headers: dict[str, str] | None = None
    compat: OpenAICompletionsCompat | OpenAIResponsesCompat | AnthropicMessagesCompat | None = None


class ImagesModel(BaseModel):
    id: str
    name: str
    api: ImagesApi
    provider: ImagesProvider
    base_url: str
    reasoning: bool
    thinking_level_map: dict[str, str | None] | None = None
    input: list[Literal["text", "image"]]
    cost: dict[str, float]
    output: list[Literal["text", "image"]]
    headers: dict[str, str] | None = None


class SimpleStreamOptions(BaseModel):
    model: str
    api: str
    provider: str
    api_key: str | None = None
    base_url: str | None = None
