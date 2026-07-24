"""Anthropic Messages API provider.

Mechanical port of hoocode's ``packages/ai/src/providers/anthropic.ts``.
Uses httpx directly instead of the ``@anthropic-ai/sdk`` npm package.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import httpx
from cortex.ai.env import get_env_api_key
from cortex.ai.models import calculate_cost
from cortex.ai.providers._common import (
    adjust_max_tokens_for_thinking,
    build_base_options,
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
    resolve_cache_retention,
    transform_messages,
)
from cortex.ai.stream import AssistantMessageEventStream, create_assistant_message_event_stream
from cortex.ai.types import (
    AnthropicMessagesCompat,
    AssistantMessage,
    CacheRetention,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    SimpleStreamOptions,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
)
from cortex.ai.util import (
    headers_to_record,
    parse_json_with_repair,
    parse_streaming_json,
    sanitize_surrogates,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ANTHROPIC_VERSION = "2023-06-01"
FINE_GRAINED_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
CLAUDE_CODE_VERSION = "2.1.75"

ANTHROPIC_MESSAGE_EVENTS: frozenset[str] = frozenset(
    [
        "message_start",
        "message_delta",
        "message_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
    ]
)

CLAUDE_CODE_TOOLS: list[str] = [
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Grep",
    "Glob",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "KillShell",
    "NotebookEdit",
    "Skill",
    "Task",
    "TaskOutput",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
]

_CC_TOOL_LOOKUP: dict[str, str] = {t.lower(): t for t in CLAUDE_CODE_TOOLS}

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]
AnthropicThinkingDisplay = Literal["summarized", "omitted"]

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class AnthropicOptions:
    """Extended options for ``stream_anthropic``."""

    temperature: float | None = None
    max_tokens: int | None = None
    signal: Any | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    on_payload: Any | None = None
    on_response: Any | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    cache_retention: CacheRetention | None = None
    metadata: dict[str, Any] | None = None
    # Anthropic-specific
    thinking_enabled: bool | None = None
    thinking_budget_tokens: int | None = None
    effort: AnthropicEffort | None = None
    thinking_display: AnthropicThinkingDisplay | None = None
    interleaved_thinking: bool | None = None
    tool_choice: str | dict[str, Any] | None = None
    client: Any | None = None  # AnthropicClient or mock


# ---------------------------------------------------------------------------
# SSE types
# ---------------------------------------------------------------------------

_ANTHROPIC_MEDIA_TYPE = "application/json"


@dataclass
class _SseEvent:
    event: str | None
    data: str
    raw: list[str]


@dataclass
class _SseDecoderState:
    event: str | None = None
    data: list[str] = field(default_factory=list)
    raw: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


def _flush_sse_event(state: _SseDecoderState) -> _SseEvent | None:
    if state.event is None and len(state.data) == 0:
        return None
    evt = _SseEvent(
        event=state.event,
        data="\n".join(state.data),
        raw=list(state.raw),
    )
    state.event = None
    state.data = []
    state.raw = []
    return evt


def _decode_sse_line(line: str, state: _SseDecoderState) -> _SseEvent | None:
    if line == "":
        return _flush_sse_event(state)

    state.raw.append(line)
    if line.startswith(":"):
        return None

    colon_idx = line.find(":")
    if colon_idx == -1:
        field_name = line
        value = ""
    else:
        field_name = line[:colon_idx]
        value = line[colon_idx + 1 :]
        if value.startswith(" "):
            value = value[1:]

    if field_name == "event":
        state.event = value
    elif field_name == "data":
        state.data.append(value)

    return None


def _next_line_break_index(text: str) -> int:
    cr = text.find("\r")
    lf = text.find("\n")
    if cr == -1:
        return lf
    if lf == -1:
        return cr
    return min(cr, lf)


def _consume_line(text: str) -> tuple[str, str] | None:
    idx = _next_line_break_index(text)
    if idx == -1:
        return None
    next_idx = idx + 1
    if idx < len(text) - 1 and text[idx] == "\r" and text[next_idx] == "\n":
        next_idx += 1
    return (text[:idx], text[next_idx:])


async def _iterate_sse_messages(
    body: AsyncIterator[bytes],
    signal: Any | None = None,
) -> AsyncIterator[_SseEvent]:
    """Parse SSE events from an async byte stream (mirrors TS iterateSseMessages)."""
    decoder = _IncrementalSSEDecoder()
    async for chunk in body:
        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("Request was aborted")
        decoder.feed(chunk)
        for event in decoder.drain():
            yield event
    # Flush remaining
    for event in decoder.flush():
        yield event


class _IncrementalSSEDecoder:
    """Buffered SSE decoder that yields complete events from byte chunks."""

    def __init__(self) -> None:
        self._state = _SseDecoderState()
        self._buffer = ""
        self._decoder = _Utf8Decoder()

    def feed(self, chunk: bytes) -> None:
        self._buffer += self._decoder.decode(chunk)

    def drain(self) -> list[_SseEvent]:
        events: list[_SseEvent] = []
        while True:
            result = _consume_line(self._buffer)
            if result is None:
                break
            line, rest = result
            self._buffer = rest
            event = _decode_sse_line(line, self._state)
            if event is not None:
                events.append(event)
        return events

    def flush(self) -> list[_SseEvent]:
        events: list[_SseEvent] = []
        if self._buffer:
            event = _decode_sse_line(self._buffer, self._state)
            if event is not None:
                events.append(event)
            self._buffer = ""
        trailing = _flush_sse_event(self._state)
        if trailing is not None:
            events.append(trailing)
        return events


class _Utf8Decoder:
    """Incremental UTF-8 decoder."""

    def __init__(self) -> None:
        self._buffer = b""

    def decode(self, chunk: bytes) -> str:
        self._buffer += chunk
        try:
            text = self._buffer.decode("utf-8")
            self._buffer = b""
            return text
        except UnicodeDecodeError:
            # Try to decode as much as possible
            try:
                text = self._buffer.decode("utf-8", errors="ignore")
                self._buffer = b""
                return text
            except Exception:
                self._buffer = b""
                return ""


async def _iterate_anthropic_events(
    response: httpx.Response,
    signal: Any | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed Anthropic SSE event dicts from an httpx Response."""
    if response.status_code != 200:
        raise RuntimeError(f"Anthropic API returned status {response.status_code}")

    saw_message_start = False
    saw_message_end = False

    async for sse in _iterate_sse_messages(response.aiter_bytes(), signal):
        if sse.event == "error":
            raise RuntimeError(sse.data)

        if sse.event not in ANTHROPIC_MESSAGE_EVENTS:
            continue

        try:
            event: dict[str, Any] = parse_json_with_repair(sse.data)
        except Exception as exc:
            raw_str = chr(10).join(sse.raw)
            raise RuntimeError(
                f"Could not parse Anthropic SSE event {sse.event}: {exc}; "
                f"data={sse.data}; raw={raw_str}"
            ) from exc

        if event.get("type") == "message_start":
            saw_message_start = True
        elif event.get("type") == "message_stop":
            saw_message_end = True

        yield event

    if saw_message_start and not saw_message_end:
        raise RuntimeError("Anthropic stream ended before message_stop")


# ---------------------------------------------------------------------------
# Client abstraction
# ---------------------------------------------------------------------------


class AnthropicClient:
    """HTTP client for the Anthropic Messages API (replaces ``@anthropic-ai/sdk``)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        auth_token: str | None = None,
        default_headers: dict[str, str | None] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._auth_token = auth_token
        self._default_headers: dict[str, str | None] = default_headers or {}

    async def create_message(
        self,
        params: dict[str, Any],
        *,
        signal: Any | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
    ) -> httpx.Response:
        """POST to ``/v1/messages`` and return the raw ``httpx.Response``."""
        headers: dict[str, str] = {}
        for k, v in self._default_headers.items():
            if v is not None:
                headers[k] = v
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        elif self._api_key:
            headers["x-api-key"] = self._api_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        headers["content-type"] = "application/json"

        url = f"{self._base_url}/v1/messages"
        timeout_s = (timeout / 1000) if timeout else 300.0

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            return await client.post(url, json=params, headers=headers)


def create_client(
    model: Model,
    api_key: str,
    interleaved_thinking: bool,
    use_fine_grained_tool_streaming_beta: bool,
    options_headers: dict[str, str] | None = None,
    dynamic_headers: dict[str, str] | None = None,
) -> tuple[AnthropicClient, bool]:
    """Build an ``AnthropicClient`` and return ``(client, is_oauth_token)``."""
    needs_interleaved_beta = interleaved_thinking and not _supports_adaptive_thinking(model.id)
    beta_features: list[str] = []
    if use_fine_grained_tool_streaming_beta:
        beta_features.append(FINE_GRAINED_TOOL_STREAMING_BETA)
    if needs_interleaved_beta:
        beta_features.append(INTERLEAVED_THINKING_BETA)

    beta_header = ",".join(beta_features) if beta_features else None

    # Copilot: Bearer auth
    if model.provider == "github-copilot":
        default_headers = _merge_headers(
            {
                "accept": "application/json",
                "anthropic-dangerous-direct-browser-access": "true",
                **({"anthropic-beta": beta_header} if beta_header else {}),
            },
            model.headers,
            dynamic_headers,
            options_headers,
        )
        client = AnthropicClient(
            base_url=model.base_url,
            auth_token=api_key,
            default_headers=default_headers,
        )
        return client, False

    # OAuth token
    if _is_oauth_token(api_key):
        default_headers = _merge_headers(
            {
                "accept": "application/json",
                "anthropic-dangerous-direct-browser-access": "true",
                "anthropic-beta": (
                    ",".join(["claude-code-20250219", "oauth-2025-04-20"] + beta_features)
                    if beta_features
                    else "claude-code-20250219,oauth-2025-04-20"
                ),
                "user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}",
                "x-app": "cli",
            },
            model.headers,
            options_headers,
        )
        client = AnthropicClient(
            base_url=model.base_url,
            auth_token=api_key,
            default_headers=default_headers,
        )
        return client, True

    # API key auth
    default_headers = _merge_headers(
        {
            "accept": "application/json",
            "anthropic-dangerous-direct-browser-access": "true",
            **({"anthropic-beta": beta_header} if beta_header else {}),
        },
        model.headers,
        options_headers,
    )
    client = AnthropicClient(
        base_url=model.base_url,
        api_key=api_key,
        default_headers=default_headers,
    )
    return client, False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_cache_control(
    model: Model,
    cache_retention: CacheRetention | None = None,
) -> tuple[CacheRetention, dict[str, Any] | None]:
    retention = resolve_cache_retention(cache_retention)
    if retention == "none":
        return retention, None
    compat = _get_anthropic_compat(model)
    ttl = "1h" if retention == "long" and compat.supports_long_cache_retention else None
    cc: dict[str, Any] = {"type": "ephemeral"}
    if ttl:
        cc["ttl"] = ttl
    return retention, cc


def _is_oauth_token(api_key: str) -> bool:
    return "sk-ant-oat" in api_key


def _to_claude_code_name(name: str) -> str:
    return _CC_TOOL_LOOKUP.get(name.lower(), name)


def _from_claude_code_name(name: str, tools: list[Tool] | None = None) -> str:
    if tools:
        lower_name = name.lower()
        for tool in tools:
            if tool.name.lower() == lower_name:
                return tool.name
    return name


def _convert_content_blocks(
    content: list[TextContent | ImageContent],
) -> str | list[dict[str, Any]]:
    has_images = any(c.type == "image" for c in content)
    if not has_images:
        return sanitize_surrogates("\n".join(cast(TextContent, c).text for c in content))
    blocks: list[dict[str, Any]] = []
    for block in content:
        if block.type == "text":
            blocks.append({"type": "text", "text": sanitize_surrogates(block.text)})
        else:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    has_text = any(b["type"] == "text" for b in blocks)
    if not has_text:
        blocks.insert(0, {"type": "text", "text": "(see attached image)"})
    return blocks


def _get_anthropic_compat(model: Model) -> AnthropicMessagesCompat:
    compat = model.compat
    if isinstance(compat, AnthropicMessagesCompat):
        return compat
    return AnthropicMessagesCompat(
        supports_eager_tool_input_streaming=True,
        supports_long_cache_retention=True,
    )


def _supports_adaptive_thinking(model_id: str) -> bool:
    return any(
        token in model_id
        for token in (
            "opus-4-6",
            "opus-4.6",
            "opus-4-7",
            "opus-4.7",
            "opus-4-8",
            "opus-4.8",
            "sonnet-4-6",
            "sonnet-4.6",
        )
    )


def _default_thinking_display(model_id: str) -> AnthropicThinkingDisplay:
    if "opus-4-8" in model_id or "opus-4.8" in model_id:
        return "omitted"
    return "summarized"


def _map_thinking_level_to_effort(
    model: Model,
    level: Literal["minimal", "low", "medium", "high", "xhigh"] | None,
) -> AnthropicEffort:
    mapped = model.thinking_level_map.get(level) if level and model.thinking_level_map else None
    if isinstance(mapped, str):
        return cast(AnthropicEffort, mapped)
    effort_map: dict[str, AnthropicEffort] = {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
    }
    return effort_map.get(level, "high") if level else "high"  # type: ignore[return-value]


def _should_use_fine_grained_tool_streaming_beta(
    model: Model,
    context: Context,
) -> bool:
    compat = _get_anthropic_compat(model)
    return bool(context.tools) and not compat.supports_eager_tool_input_streaming


def _normalize_tool_call_id(tool_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_id)[:64]


_STOP_REASON_MAP: dict[str, StopReason] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "toolUse",
    "refusal": "error",
    "pause_turn": "stop",
    "stop_sequence": "stop",
    "sensitive": "error",
}


def _map_stop_reason(reason: str) -> StopReason:
    mapped = _STOP_REASON_MAP.get(reason)
    if mapped is None:
        raise ValueError(f"Unhandled stop reason: {reason}")
    return mapped


def _merge_headers(
    *header_sources: Any,
) -> dict[str, str | None]:
    merged: dict[str, str | None] = {}
    for source in header_sources:
        if source:
            merged.update(source)
    return merged


def _make_default_usage() -> Usage:
    return Usage(
        input=0,
        output=0,
        cache_read=0,
        cache_write=0,
        total_tokens=0,
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
    )


# ---------------------------------------------------------------------------
# Message / Tool conversion
# ---------------------------------------------------------------------------


def build_params(
    model: Model,
    context: Context,
    is_oauth: bool,
    options: AnthropicOptions | None = None,
) -> dict[str, Any]:
    """Build the ``MessageCreateParamsStreaming`` dict for the Anthropic API."""
    cache_ret = options.cache_retention if options else None
    _retention, cache_control = _get_cache_control(model, cache_ret)
    max_tokens = options.max_tokens if options and options.max_tokens else (model.max_tokens // 3)
    params: dict[str, Any] = {
        "model": model.id,
        "messages": _convert_messages(context.messages, model, is_oauth, cache_control),
        "max_tokens": max_tokens,
        "stream": True,
    }

    # System prompt
    if is_oauth:
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": "You are Claude Code, Anthropic's official CLI for Claude.",
                **({"cache_control": cache_control} if cache_control else {}),
            }
        ]
        if context.system_prompt:
            system_blocks.append(
                {
                    "type": "text",
                    "text": sanitize_surrogates(context.system_prompt),
                    **({"cache_control": cache_control} if cache_control else {}),
                }
            )
        params["system"] = system_blocks
    elif context.system_prompt:
        params["system"] = [
            {
                "type": "text",
                "text": sanitize_surrogates(context.system_prompt),
                **({"cache_control": cache_control} if cache_control else {}),
            }
        ]

    # Temperature (incompatible with thinking)
    if options and options.temperature is not None and not options.thinking_enabled:
        params["temperature"] = options.temperature

    # Tools
    if context.tools:
        params["tools"] = _convert_tools(
            context.tools,
            is_oauth,
            bool(_get_anthropic_compat(model).supports_eager_tool_input_streaming),
            cache_control,
        )

    # Thinking
    if model.reasoning:
        if options and options.thinking_enabled:
            display: AnthropicThinkingDisplay = (
                options.thinking_display or _default_thinking_display(model.id)
            )
            if _supports_adaptive_thinking(model.id):
                params["thinking"] = {"type": "adaptive", "display": display}
                if options.effort:
                    # xhigh needs a cast because SDK types can lag
                    params["output_config"] = {"effort": options.effort}
            else:
                params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": options.thinking_budget_tokens or 1024,
                    "display": display,
                }
        elif options and options.thinking_enabled is False:
            params["thinking"] = {"type": "disabled"}

    # Metadata
    if options and options.metadata:
        user_id = options.metadata.get("user_id")
        if isinstance(user_id, str):
            params["metadata"] = {"user_id": user_id}

    # Tool choice
    if options and options.tool_choice:
        if isinstance(options.tool_choice, str):
            params["tool_choice"] = {"type": options.tool_choice}
        else:
            params["tool_choice"] = options.tool_choice

    return params


def _convert_messages(
    messages: list[Message],
    model: Model,
    is_oauth: bool,
    cache_control: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    params: list[dict[str, Any]] = []

    def _normalize_id(tool_id: str, _model: Model, _msg: AssistantMessage) -> str:
        return _normalize_tool_call_id(tool_id)

    transformed = transform_messages(messages, model, _normalize_id)

    i = 0
    while i < len(transformed):
        msg = transformed[i]

        if msg.role == "user":
            if isinstance(msg.content, str):
                if msg.content.strip():
                    params.append({"role": "user", "content": sanitize_surrogates(msg.content)})
            else:
                blocks: list[dict[str, Any]] = []
                for item in msg.content:
                    if item.type == "text":
                        blocks.append({"type": "text", "text": sanitize_surrogates(item.text)})
                    else:
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": item.mime_type,
                                    "data": item.data,
                                },
                            }
                        )
                filtered = [b for b in blocks if b["type"] != "text" or b["text"].strip()]
                if filtered:
                    params.append({"role": "user", "content": filtered})

        elif msg.role == "assistant":
            blocks = []
            for block in msg.content:
                if block.type == "text":
                    if not block.text.strip():
                        continue
                    blocks.append({"type": "text", "text": sanitize_surrogates(block.text)})
                elif block.type == "thinking":
                    if block.redacted:
                        data = block.thinking_signature or ""
                        blocks.append({"type": "redacted_thinking", "data": data})
                        continue
                    if not block.thinking.strip():
                        continue
                    if not block.thinking_signature or not block.thinking_signature.strip():
                        blocks.append({"type": "text", "text": sanitize_surrogates(block.thinking)})
                    else:
                        blocks.append(
                            {
                                "type": "thinking",
                                "thinking": sanitize_surrogates(block.thinking),
                                "signature": block.thinking_signature,
                            }
                        )
                elif block.type == "toolCall":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": _to_claude_code_name(block.name) if is_oauth else block.name,
                            "input": block.arguments or {},
                        }
                    )
            if blocks:
                params.append({"role": "assistant", "content": blocks})

        elif msg.role == "toolResult":
            tool_results: list[dict[str, Any]] = []
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": _convert_content_blocks(msg.content),
                    "is_error": msg.is_error,
                }
            )
            j = i + 1
            while j < len(transformed) and transformed[j].role == "toolResult":
                next_msg = cast(ToolResultMessage, transformed[j])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": next_msg.tool_call_id,
                        "content": _convert_content_blocks(next_msg.content),
                        "is_error": next_msg.is_error,
                    }
                )
                j += 1
            i = j - 1
            params.append({"role": "user", "content": tool_results})

        i += 1

    # Add cache_control to the last user message
    if cache_control and params:
        last = params[-1]
        if last["role"] == "user":
            content = last["content"]
            if isinstance(content, list):
                last_block = content[-1] if content else None
                if last_block and last_block["type"] in ("text", "image", "tool_result"):
                    last_block["cache_control"] = cache_control
            elif isinstance(content, str):
                last["content"] = [
                    {"type": "text", "text": content, "cache_control": cache_control}
                ]

    return params


def _convert_tools(
    tools: list[Tool],
    is_oauth: bool,
    supports_eager_tool_streaming: bool,
    cache_control: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        schema = tool.parameters or {}
        entry: dict[str, Any] = {
            "name": _to_claude_code_name(tool.name) if is_oauth else tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        }
        if supports_eager_tool_streaming:
            entry["eager_input_streaming"] = True
        if cache_control and index == len(tools) - 1:
            entry["cache_control"] = cache_control
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Main streaming function
# ---------------------------------------------------------------------------


def stream_anthropic(
    model: Model,
    context: Context,
    options: AnthropicOptions | None = None,
) -> AssistantMessageEventStream:
    """Stream an Anthropic Messages API response.

    Mirrors TS ``streamAnthropic``.
    """
    stream = create_assistant_message_event_stream()

    async def _run() -> None:
        output = AssistantMessage(
            role="assistant",
            content=[],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=_make_default_usage(),
            stop_reason="stop",
            timestamp=int(time.time() * 1000),
        )

        try:
            # Client
            if options and options.client:
                client = options.client
                is_oauth = False
            else:
                opt_key = options.api_key if options else None
                api_key = opt_key or get_env_api_key(model.provider) or ""
                copilot_dynamic_headers: dict[str, str] | None = None
                if model.provider == "github-copilot":
                    has_images = has_copilot_vision_input(context.messages)
                    copilot_dynamic_headers = build_copilot_dynamic_headers(
                        messages=context.messages,
                        has_images=has_images,
                    )
                client, is_oauth = create_client(
                    model,
                    api_key,
                    options.interleaved_thinking if options else True,  # type: ignore[union-attr]
                    _should_use_fine_grained_tool_streaming_beta(model, context),
                    options.headers if options else None,
                    copilot_dynamic_headers,
                )

            params = build_params(model, context, is_oauth, options)

            # onPayload callback
            if options and options.on_payload:
                next_params = options.on_payload(params, model)
                if next_params is not None:
                    params = next_params

            # Build request kwargs
            request_kwargs: dict[str, Any] = {}
            if options and options.signal:
                request_kwargs["signal"] = options.signal
            if options and options.timeout_ms is not None:
                request_kwargs["timeout"] = options.timeout_ms
            if options and options.max_retries is not None:
                request_kwargs["max_retries"] = options.max_retries

            response = await client.create_message(params, **request_kwargs)

            # onResponse callback
            if options and options.on_response:
                options.on_response(
                    {
                        "status": response.status_code,
                        "headers": headers_to_record(response.headers),
                    },
                    model,
                )

            stream.push(StartEvent(partial=output))

            # Block tracking: index → {"type": ..., "text": ..., "thinking": ..., etc.}
            block_map: dict[int, dict[str, Any]] = {}

            signal = options.signal if options else None
            async for event in _iterate_anthropic_events(response, signal):
                etype = event.get("type")

                if etype == "message_start":
                    msg = event.get("message", {})
                    output.response_id = msg.get("id")
                    usage = msg.get("usage", {})
                    output.usage.input = usage.get("input_tokens", 0) or 0
                    output.usage.output = usage.get("output_tokens", 0) or 0
                    output.usage.cache_read = usage.get("cache_read_input_tokens", 0) or 0
                    output.usage.cache_write = usage.get("cache_creation_input_tokens", 0) or 0
                    output.usage.total_tokens = (
                        output.usage.input
                        + output.usage.output
                        + output.usage.cache_read
                        + output.usage.cache_write
                    )
                    output.usage.cost = calculate_cost(model, output.usage)

                elif etype == "content_block_start":
                    cb = event.get("content_block", {})
                    index = event.get("index", 0)
                    if cb.get("type") == "text":
                        block_data = {"type": "text", "text": ""}
                        block_map[index] = block_data
                        output.content.append(TextContent(text=""))
                        ci = len(output.content) - 1
                        stream.push(TextStartEvent(content_index=ci, partial=output))
                    elif cb.get("type") == "thinking":
                        block_data = {"type": "thinking", "thinking": "", "thinking_signature": ""}
                        block_map[index] = block_data
                        output.content.append(ThinkingContent(thinking=""))
                        ci = len(output.content) - 1
                        stream.push(ThinkingStartEvent(content_index=ci, partial=output))
                    elif cb.get("type") == "redacted_thinking":
                        block_data = {
                            "type": "thinking",
                            "thinking": "[Reasoning redacted]",
                            "thinking_signature": cb.get("data", ""),
                            "redacted": True,
                        }
                        block_map[index] = block_data
                        output.content.append(
                            ThinkingContent(
                                thinking="[Reasoning redacted]",
                                thinking_signature=cb.get("data", ""),
                                redacted=True,
                            )
                        )
                        ci = len(output.content) - 1
                        stream.push(ThinkingStartEvent(content_index=ci, partial=output))
                    elif cb.get("type") == "tool_use":
                        block_data = {
                            "type": "toolCall",
                            "id": cb.get("id", ""),
                            "name": (
                                _from_claude_code_name(cb.get("name", ""), context.tools)
                                if is_oauth
                                else cb.get("name", "")
                            ),
                            "arguments": cb.get("input") or {},
                            "partial_json": "",
                        }
                        block_map[index] = block_data
                        tc_name = str(block_data["name"])
                        raw_args = block_data["arguments"]
                        tc_args = raw_args if isinstance(raw_args, dict) else {}
                        output.content.append(
                            ToolCall(
                                id=str(cb.get("id", "")),
                                name=tc_name,
                                arguments=tc_args,
                            )
                        )
                        ci = len(output.content) - 1
                        stream.push(ToolCallStartEvent(content_index=ci, partial=output))

                elif etype == "content_block_delta":
                    delta = event.get("delta", {})
                    index = event.get("index", 0)
                    bd = block_map.get(index)
                    if bd is None:
                        continue

                    if delta.get("type") == "text_delta" and bd["type"] == "text":
                        text_delta = delta.get("text", "")
                        bd["text"] += text_delta
                        # Update the content block
                        cb_idx = _find_block_index(output.content, index, block_map)
                        if cb_idx is not None:
                            output.content[cb_idx] = TextContent(text=bd["text"])
                            stream.push(
                                TextDeltaEvent(
                                    content_index=cb_idx,
                                    delta=text_delta,
                                    partial=output,
                                )
                            )

                    elif delta.get("type") == "thinking_delta" and bd["type"] == "thinking":
                        thinking_delta = delta.get("thinking", "")
                        bd["thinking"] += thinking_delta
                        cb_idx = _find_block_index(output.content, index, block_map)
                        if cb_idx is not None:
                            output.content[cb_idx] = ThinkingContent(
                                thinking=bd["thinking"],
                                thinking_signature=bd.get("thinking_signature", ""),
                                redacted=bd.get("redacted"),
                            )
                            stream.push(
                                ThinkingDeltaEvent(
                                    content_index=cb_idx,
                                    delta=thinking_delta,
                                    partial=output,
                                )
                            )

                    elif delta.get("type") == "input_json_delta" and bd["type"] == "toolCall":
                        partial_json = delta.get("partial_json", "")
                        bd["partial_json"] += partial_json
                        bd["arguments"] = parse_streaming_json(bd["partial_json"])
                        cb_idx = _find_block_index(output.content, index, block_map)
                        if cb_idx is not None:
                            tc_id: str = str(bd["id"])
                            tc_name: str = str(bd["name"])
                            tc_args = bd["arguments"]
                            output.content[cb_idx] = ToolCall(
                                id=tc_id,
                                name=tc_name,
                                arguments=tc_args,
                            )
                            stream.push(
                                ToolCallDeltaEvent(
                                    content_index=cb_idx,
                                    delta=partial_json,
                                    partial=output,
                                )
                            )

                    elif delta.get("type") == "signature_delta" and bd["type"] == "thinking":
                        sig = bd.get("thinking_signature", "") or ""
                        bd["thinking_signature"] = sig + delta.get("signature", "")

                elif etype == "content_block_stop":
                    index = event.get("index", 0)
                    bd = block_map.get(index)
                    if bd is None:
                        continue

                    cb_idx = _find_block_index(output.content, index, block_map)
                    if cb_idx is None:
                        continue

                    if bd["type"] == "text":
                        stream.push(
                            TextEndEvent(
                                content_index=cb_idx,
                                content=bd["text"],
                                partial=output,
                            )
                        )
                    elif bd["type"] == "thinking":
                        stream.push(
                            ThinkingEndEvent(
                                content_index=cb_idx,
                                content=bd["thinking"],
                                partial=output,
                            )
                        )
                    elif bd["type"] == "toolCall":
                        # Finalize tool call
                        final_args = parse_streaming_json(bd["partial_json"])
                        final_tc = ToolCall(
                            id=str(bd["id"]),
                            name=str(bd["name"]),
                            arguments=final_args,
                        )
                        output.content[cb_idx] = final_tc
                        stream.push(
                            ToolCallEndEvent(
                                content_index=cb_idx,
                                tool_call=final_tc,
                                partial=output,
                            )
                        )

                elif etype == "message_delta":
                    delta = event.get("delta", {})
                    usage = event.get("usage", {})
                    if delta.get("stop_reason"):
                        output.stop_reason = _map_stop_reason(delta["stop_reason"])
                    if usage.get("input_tokens") is not None:
                        output.usage.input = usage["input_tokens"]
                    if usage.get("output_tokens") is not None:
                        output.usage.output = usage["output_tokens"]
                    if usage.get("cache_read_input_tokens") is not None:
                        output.usage.cache_read = usage["cache_read_input_tokens"]
                    if usage.get("cache_creation_input_tokens") is not None:
                        output.usage.cache_write = usage["cache_creation_input_tokens"]
                    output.usage.total_tokens = (
                        output.usage.input
                        + output.usage.output
                        + output.usage.cache_read
                        + output.usage.cache_write
                    )
                    output.usage.cost = calculate_cost(model, output.usage)

            # Check abort
            if options and options.signal and getattr(options.signal, "aborted", False):
                raise RuntimeError("Request was aborted")

            if output.stop_reason in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            stream.push(DoneEvent(reason=output.stop_reason, message=output))  # type: ignore[arg-type]
            stream.end(output)

        except Exception as exc:
            output.stop_reason = (
                "aborted"
                if (options and options.signal and getattr(options.signal, "aborted", False))
                else "error"
            )
            output.error_message = str(exc)
            stream.push(ErrorEvent(reason=output.stop_reason, error=output))  # type: ignore[arg-type]
            stream.end(output)

    asyncio.ensure_future(_run())
    return stream


def _find_block_index(
    content: list[Any],
    event_index: int,
    block_map: dict[int, dict[str, Any]],
) -> int | None:
    """Find the position in output.content that corresponds to event_index.

    We track block_map entries in insertion order. The Nth entry in block_map
    (sorted by key) corresponds to the Nth content block.
    """
    sorted_keys = sorted(block_map.keys())
    try:
        position = sorted_keys.index(event_index)
    except ValueError:
        return None
    if position < len(content):
        return position
    return None


# ---------------------------------------------------------------------------
# stream_simple_anthropic
# ---------------------------------------------------------------------------


def stream_simple_anthropic(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    """Simplified streaming interface for Anthropic models.

    Mirrors TS ``streamSimpleAnthropic``.
    """
    api_key = (options.api_key if options else None) or get_env_api_key(model.provider)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)

    if not (options and options.reasoning):
        return stream_anthropic(
            model,
            context,
            AnthropicOptions(
                temperature=base.temperature,
                max_tokens=base.max_tokens,
                signal=base.signal,
                api_key=base.api_key,
                headers=base.headers,
                on_payload=base.on_payload,
                on_response=base.on_response,
                timeout_ms=base.timeout_ms,
                max_retries=base.max_retries,
                cache_retention=base.cache_retention,
                metadata=base.metadata,
                thinking_enabled=False,
            ),
        )

    if _supports_adaptive_thinking(model.id):
        effort = _map_thinking_level_to_effort(model, options.reasoning)
        return stream_anthropic(
            model,
            context,
            AnthropicOptions(
                temperature=base.temperature,
                max_tokens=base.max_tokens,
                signal=base.signal,
                api_key=base.api_key,
                headers=base.headers,
                on_payload=base.on_payload,
                on_response=base.on_response,
                timeout_ms=base.timeout_ms,
                max_retries=base.max_retries,
                cache_retention=base.cache_retention,
                metadata=base.metadata,
                thinking_enabled=True,
                effort=effort,
                thinking_display=options.thinking_display,
            ),
        )

    adjusted = adjust_max_tokens_for_thinking(
        base.max_tokens or 0,
        model.max_tokens,
        options.reasoning,
        options.thinking_budgets,
    )
    return stream_anthropic(
        model,
        context,
        AnthropicOptions(
            temperature=base.temperature,
            max_tokens=adjusted["max_tokens"],
            signal=base.signal,
            api_key=base.api_key,
            headers=base.headers,
            on_payload=base.on_payload,
            on_response=base.on_response,
            timeout_ms=base.timeout_ms,
            max_retries=base.max_retries,
            cache_retention=base.cache_retention,
            metadata=base.metadata,
            thinking_enabled=True,
            thinking_budget_tokens=adjusted["thinking_budget"],
            thinking_display=options.thinking_display,
        ),
    )
