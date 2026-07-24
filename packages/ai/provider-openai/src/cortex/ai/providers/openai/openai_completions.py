"""OpenAI Chat Completions API provider.

Mechanical port of hoocode's ``packages/ai/src/providers/openai-completions.ts``.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from cortex.ai.env import get_env_api_key
from cortex.ai.models import calculate_cost, clamp_thinking_level
from cortex.ai.providers._common import (
    build_base_options,
    resolve_cache_retention,
)
from cortex.ai.stream import AssistantMessageEventStream, create_assistant_message_event_stream
from cortex.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Model,
    SimpleStreamOptions,
    StartEvent,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextStartEvent,
    Tool,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    Usage,
)
from cortex.ai.util import (
    parse_streaming_json,
    sanitize_surrogates,
    to_strict_json_schema,
)

from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENAI_TOOL_CALL_PROVIDERS: frozenset[str] = frozenset(["openai", "openai-codex", "opencode"])


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class OpenAICompletionsOptions(StreamOptions):
    """OpenAI Chat Completions-specific options."""

    reasoning_effort: str | None = None
    service_tier: str | None = None


# ---------------------------------------------------------------------------
# Compat detection
# ---------------------------------------------------------------------------


@dataclass
class ResolvedOpenAICompletionsCompat:
    """Resolved compatibility settings for OpenAI Completions API."""

    supportsStore: bool = True
    supportsDeveloperRole: bool = True
    supportsReasoningEffort: bool = True
    supportsUsageInStreaming: bool = True
    maxTokensField: str = "max_completion_tokens"
    requiresToolResultName: bool = False
    requiresAssistantAfterToolResult: bool = False
    requiresThinkingAsText: bool = False
    requiresReasoningContentOnAssistantMessages: bool = False
    thinkingFormat: str = "openai"
    openRouterRouting: dict[str, Any] = field(default_factory=dict)
    vercelGatewayRouting: dict[str, Any] = field(default_factory=dict)
    zaiToolStream: bool = False
    supportsStrictMode: bool = True
    toolCallConstraint: str = "none"
    cacheControlFormat: str | None = None
    sendSessionAffinityHeaders: bool = False
    supportsLongCacheRetention: bool = True
    promptSuffix: str | None = None


def _detect_compat(model: Model) -> ResolvedOpenAICompletionsCompat:
    """Detect compatibility settings from provider and baseUrl."""
    provider = model.provider
    base_url = model.base_url or ""

    is_zai = provider == "zai" or "api.z.ai" in base_url
    is_together = (
        provider == "together" or "api.together.ai" in base_url or "api.together.xyz" in base_url
    )
    is_moonshot = provider in ("moonshotai", "moonshotai-cn") or "api.moonshot." in base_url
    is_opencode_go = provider == "opencode-go" or "opencode.ai/zen/go" in base_url

    is_non_standard = (
        provider == "cerebras"
        or "cerebras.ai" in base_url
        or provider == "xai"
        or "api.x.ai" in base_url
        or is_together
        or "chutes.ai" in base_url
        or "deepseek.com" in base_url
        or is_zai
        or is_moonshot
        or provider == "opencode"
        or "opencode.ai" in base_url
    )

    use_max_tokens = "chutes.ai" in base_url or is_moonshot or is_together

    is_grok = provider == "xai" or "api.x.ai" in base_url
    is_deepseek = provider == "deepseek" or "deepseek.com" in base_url
    cache_control_format = (
        "anthropic" if provider == "openrouter" and model.id.startswith("anthropic/") else None
    )

    return ResolvedOpenAICompletionsCompat(
        supportsStore=not is_non_standard,
        supportsDeveloperRole=not is_non_standard,
        supportsReasoningEffort=not is_grok and not is_zai and not is_moonshot and not is_together,
        supportsUsageInStreaming=True,
        maxTokensField="max_tokens" if use_max_tokens else "max_completion_tokens",
        requiresToolResultName=False,
        requiresAssistantAfterToolResult=False,
        requiresThinkingAsText=False,
        requiresReasoningContentOnAssistantMessages=is_deepseek,
        thinkingFormat="deepseek"
        if is_deepseek
        else "zai"
        if is_zai
        else "together"
        if is_together
        else "openrouter"
        if provider == "openrouter" or "openrouter.ai" in base_url
        else "openai",
        openRouterRouting={},
        vercelGatewayRouting={},
        zaiToolStream=False,
        supportsStrictMode=not is_moonshot and not is_together,
        toolCallConstraint="strict"
        if provider == "openai" or "api.openai.com" in base_url
        else "none",
        cacheControlFormat=cache_control_format,
        sendSessionAffinityHeaders=False,
        supportsLongCacheRetention=not (is_together or is_opencode_go),
        promptSuffix=None,
    )


def _get_compat(model: Model) -> ResolvedOpenAICompletionsCompat:
    """Get resolved compatibility settings for a model."""
    detected = _detect_compat(model)
    if not model.compat:
        return detected

    compat = model.compat
    # Cast to dict to handle different compat types
    compat_dict: dict[str, Any] = {}
    if hasattr(compat, "model_dump"):
        compat_dict = compat.model_dump()
    elif hasattr(compat, "dict"):
        compat_dict = compat.dict()  # type: ignore[deprecated]
    elif isinstance(compat, dict):
        compat_dict = compat

    return ResolvedOpenAICompletionsCompat(
        supportsStore=compat_dict.get("supportsStore", detected.supportsStore),
        supportsDeveloperRole=compat_dict.get(
            "supportsDeveloperRole", detected.supportsDeveloperRole
        ),
        supportsReasoningEffort=compat_dict.get(
            "supportsReasoningEffort", detected.supportsReasoningEffort
        ),
        supportsUsageInStreaming=compat_dict.get(
            "supportsUsageInStreaming", detected.supportsUsageInStreaming
        ),
        maxTokensField=compat_dict.get("maxTokensField", detected.maxTokensField),
        requiresToolResultName=compat_dict.get(
            "requiresToolResultName", detected.requiresToolResultName
        ),
        requiresAssistantAfterToolResult=compat_dict.get(
            "requiresAssistantAfterToolResult", detected.requiresAssistantAfterToolResult
        ),
        requiresThinkingAsText=compat_dict.get(
            "requiresThinkingAsText", detected.requiresThinkingAsText
        ),
        requiresReasoningContentOnAssistantMessages=compat_dict.get(
            "requiresReasoningContentOnAssistantMessages",
            detected.requiresReasoningContentOnAssistantMessages,
        ),
        thinkingFormat=compat_dict.get("thinkingFormat", detected.thinkingFormat),
        openRouterRouting=compat_dict.get("openRouterRouting", {}),
        vercelGatewayRouting=compat_dict.get("vercelGatewayRouting", detected.vercelGatewayRouting),
        zaiToolStream=compat_dict.get("zaiToolStream", detected.zaiToolStream),
        supportsStrictMode=compat_dict.get("supportsStrictMode", detected.supportsStrictMode),
        toolCallConstraint=compat_dict.get("toolCallConstraint", detected.toolCallConstraint),
        cacheControlFormat=compat_dict.get("cacheControlFormat", detected.cacheControlFormat),
        sendSessionAffinityHeaders=compat_dict.get(
            "sendSessionAffinityHeaders", detected.sendSessionAffinityHeaders
        ),
        supportsLongCacheRetention=compat_dict.get(
            "supportsLongCacheRetention", detected.supportsLongCacheRetention
        ),
        promptSuffix=compat_dict.get("promptSuffix", detected.promptSuffix),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_client(
    model: Model,
    context: Context,
    api_key: str | None = None,
    options_headers: dict[str, str] | None = None,
    session_id: str | None = None,
) -> AsyncOpenAI:
    """Create an OpenAI client."""
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")

    compat = _get_compat(model)
    headers = dict(model.headers) if model.headers else {}

    if session_id:
        if compat.sendSessionAffinityHeaders:
            headers["session_id"] = session_id
        headers["x-client-request-id"] = session_id

    if options_headers:
        headers.update(options_headers)

    return AsyncOpenAI(
        api_key=api_key,
        base_url=model.base_url,
        default_headers=headers,
    )


def _build_params(
    model: Model,
    context: Context,
    options: OpenAICompletionsOptions | None = None,
) -> dict[str, Any]:
    """Build parameters for the OpenAI Chat Completions API."""
    compat = _get_compat(model)
    messages = _convert_messages(model, context, compat)
    tools = _convert_tools(
        context.tools or [], compat, options.constrain_tool_calls if options else False
    )

    params: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    if compat.supportsStore:
        params["store"] = False

    # Add max tokens
    max_tokens_field = compat.maxTokensField
    if options and options.max_tokens:
        params[max_tokens_field] = options.max_tokens

    # Add temperature
    if options and options.temperature is not None:
        params["temperature"] = options.temperature

    # Add tools
    if tools:
        params["tools"] = tools

    # Add reasoning effort
    if model.reasoning and options and options.reasoning_effort:
        thinking_level_map = model.thinking_level_map or {}
        effort = thinking_level_map.get(options.reasoning_effort, options.reasoning_effort)
        params["reasoning_effort"] = effort

    return params


def _convert_messages(
    model: Model,
    context: Context,
    compat: ResolvedOpenAICompletionsCompat,
) -> list[dict[str, Any]]:
    """Convert messages to OpenAI Chat Completions format."""
    from cortex.ai.providers._common import transform_messages

    def normalize_tool_call_id(id_str: str, target_model: Model, source: AssistantMessage) -> str:
        if model.provider not in OPENAI_TOOL_CALL_PROVIDERS:
            return id_str
        if "|" not in id_str:
            return id_str
        call_id, item_id = id_str.split("|", 1)
        return f"{call_id}|{item_id}"

    transformed = transform_messages(context.messages, model, normalize_tool_call_id)

    messages: list[dict[str, Any]] = []

    # Add system prompt
    if context.system_prompt:
        role = "developer" if compat.supportsDeveloperRole and model.reasoning else "system"
        messages.append(
            {
                "role": role,
                "content": sanitize_surrogates(context.system_prompt),
            }
        )

    for msg in transformed:
        if msg.role == "user":
            if isinstance(msg.content, str):
                messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": sanitize_surrogates(msg.content)}],
                    }
                )
            else:
                content = []
                for item in msg.content:
                    if item.type == "text":
                        content.append(
                            {
                                "type": "text",
                                "text": sanitize_surrogates(item.text),
                            }
                        )
                    elif item.type == "image":
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{item.mime_type};base64,{item.data}",
                                },
                            }
                        )
                if len(content) == 0:
                    continue
                messages.append(
                    {
                        "role": "user",
                        "content": content,
                    }
                )
        elif msg.role == "assistant":
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "" if compat.requiresAssistantAfterToolResult else None,
            }

            assistant_text_parts = []
            thinking_blocks = []

            for block in msg.content:
                if block.type == "text":
                    if block.text.strip():
                        assistant_text_parts.append(
                            {
                                "type": "text",
                                "text": sanitize_surrogates(block.text),
                            }
                        )
                elif block.type == "thinking":
                    if block.thinking.strip():
                        thinking_blocks.append(block)

            assistant_text = "".join(p["text"] for p in assistant_text_parts)

            if thinking_blocks:
                if compat.requiresThinkingAsText:
                    thinking_text = "\n\n".join(
                        sanitize_surrogates(b.thinking) for b in thinking_blocks
                    )
                    assistant_msg["content"] = [
                        {"type": "text", "text": thinking_text}
                    ] + assistant_text_parts
                else:
                    if assistant_text:
                        assistant_msg["content"] = assistant_text
                    # Add reasoning content if supported
                    if compat.requiresReasoningContentOnAssistantMessages:
                        assistant_msg["reasoning_content"] = "\n\n".join(
                            b.thinking for b in thinking_blocks
                        )
            elif assistant_text:
                assistant_msg["content"] = assistant_text

            tool_calls = [b for b in msg.content if b.type == "toolCall"]
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]

            # Skip empty assistant messages
            content = assistant_msg.get("content")
            has_content = (
                content is not None
                and content != ""
                and (not isinstance(content, list) or len(content) > 0)
            )
            if not has_content and not assistant_msg.get("tool_calls"):
                continue

            messages.append(assistant_msg)
        elif msg.role == "toolResult":
            tool_msg = msg
            text_result = "\n".join(c.text for c in tool_msg.content if c.type == "text")
            has_text = len(text_result) > 0

            tool_result_msg: dict[str, Any] = {
                "role": "tool",
                "content": sanitize_surrogates(text_result if has_text else "(see attached image)"),
                "tool_call_id": tool_msg.tool_call_id,
            }
            if compat.requiresToolResultName and tool_msg.tool_name:
                tool_result_msg["name"] = tool_msg.tool_name

            messages.append(tool_result_msg)

    return messages


def _convert_tools(
    tools: list[Tool],
    compat: ResolvedOpenAICompletionsCompat,
    constrain_tool_calls: bool | None = None,
) -> list[dict[str, Any]]:
    """Convert tools to OpenAI Chat Completions format."""
    strict = (
        constrain_tool_calls is True
        and compat.toolCallConstraint == "strict"
        and compat.supportsStrictMode
    )
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": to_strict_json_schema(tool.parameters) if strict else tool.parameters,
                **({"strict": strict} if compat.supportsStrictMode else {}),
            },
        }
        for tool in tools
    ]


def _parse_chunk_usage(
    raw_usage: dict[str, Any],
    model: Model,
) -> Usage:
    """Parse usage from a streaming chunk."""
    prompt_tokens = raw_usage.get("prompt_tokens", 0)
    reported_cached_tokens = (
        raw_usage.get("prompt_tokens_details", {}).get("cached_tokens")
        or raw_usage.get("prompt_cache_hit_tokens")
        or 0
    )
    cache_write_tokens = raw_usage.get("prompt_tokens_details", {}).get("cache_write_tokens", 0)

    # Normalize to pi-ai semantics
    cache_read_tokens = (
        max(0, reported_cached_tokens - cache_write_tokens)
        if cache_write_tokens > 0
        else reported_cached_tokens
    )

    input_tokens = max(0, prompt_tokens - cache_read_tokens - cache_write_tokens)
    output_tokens = raw_usage.get("completion_tokens", 0)

    usage = Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read_tokens,
        cache_write=cache_write_tokens,
        total_tokens=input_tokens + output_tokens + cache_read_tokens + cache_write_tokens,
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
    )
    calculate_cost(model, usage)
    return usage


def _map_stop_reason(reason: str | None) -> tuple[str, str | None]:
    """Map OpenAI finish_reason to StopReason."""
    if reason is None:
        return ("stop", None)
    if reason in ("stop", "end"):
        return ("stop", None)
    if reason == "length":
        return ("length", None)
    if reason in ("function_call", "tool_calls"):
        return ("toolUse", None)
    if reason == "content_filter":
        return ("error", "Provider finish_reason: content_filter")
    if reason == "network_error":
        return ("error", "Provider finish_reason: network_error")
    return ("error", f"Provider finish_reason: {reason}")


# ---------------------------------------------------------------------------
# Stream functions
# ---------------------------------------------------------------------------


def stream_openai_completions(
    model: Model,
    context: Context,
    options: OpenAICompletionsOptions | None = None,
) -> AssistantMessageEventStream:
    """Generate function for OpenAI Chat Completions API."""
    stream = create_assistant_message_event_stream()

    async def _process():
        output = AssistantMessage(
            role="assistant",
            content=[],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(
                input=0,
                output=0,
                cache_read=0,
                cache_write=0,
                total_tokens=0,
                cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
            ),
            stop_reason="stop",
            timestamp=0,
        )

        try:
            api_key = (
                (options.api_key if options else None) or get_env_api_key(model.provider) or ""
            )
            cache_retention = resolve_cache_retention(options.cache_retention if options else None)
            cache_session_id = (
                None if cache_retention == "none" else (options.session_id if options else None)
            )
            client = _create_client(
                model, context, api_key, options.headers if options else None, cache_session_id
            )
            params = _build_params(model, context, options)

            # Build request options
            request_options: dict[str, Any] = {}
            if options and options.signal:
                request_options["signal"] = options.signal
            if options and options.timeout_ms is not None:
                request_options["timeout"] = options.timeout_ms
            if options and options.max_retries is not None:
                request_options["max_retries"] = options.max_retries

            openai_stream = await client.chat.completions.create(**params)
            stream.push(StartEvent(partial=output))

            # block_map: index -> scratch buffer dict for streaming tool calls
            block_map: dict[int, dict[str, Any]] = {}
            has_tool_calls = False

            # Process stream events
            async for chunk in openai_stream:
                if not chunk.choices and chunk.usage:
                    # Final usage chunk
                    output.usage = _parse_chunk_usage(
                        {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                            "prompt_tokens_details": {
                                "cached_tokens": getattr(
                                    chunk.usage.prompt_tokens_details, "cached_tokens", 0
                                )
                                if chunk.usage.prompt_tokens_details
                                else 0,
                            },
                        },
                        model,
                    )
                    calculate_cost(model, output.usage)
                    continue

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta
                finish_reason = choice.finish_reason

                # Process delta content
                if delta and delta.content:
                    # Check if last block is text
                    last_is_text = output.content and isinstance(output.content[-1], TextContent)
                    if not last_is_text:
                        output.content.append(TextContent(text=""))
                        ci = len(output.content) - 1
                        stream.push(TextStartEvent(content_index=ci, partial=output))
                    ci = len(output.content) - 1
                    current = output.content[ci]
                    assert isinstance(current, TextContent)
                    output.content[ci] = TextContent(text=current.text + delta.content)
                    stream.push(
                        TextDeltaEvent(
                            content_index=ci,
                            delta=delta.content,
                            partial=output,
                        )
                    )

                # Process tool calls
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        if tc_delta.index is not None:
                            idx = tc_delta.index
                            # Ensure we have enough tool call slots
                            while len(output.content) <= idx:
                                block_map[len(output.content)] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": {},
                                    "partial_json": "",
                                }
                                output.content.append(ToolCall(id="", name="", arguments={}))
                                ci = len(output.content) - 1
                                stream.push(ToolCallStartEvent(content_index=ci, partial=output))

                            bd = block_map.get(idx, {})
                            if tc_delta.id:
                                bd["id"] = tc_delta.id
                                output.content[idx] = ToolCall(
                                    id=tc_delta.id,
                                    name=bd.get("name", ""),
                                    arguments=bd.get("arguments", {}),
                                )
                            if tc_delta.function and tc_delta.function.name:
                                bd["name"] = tc_delta.function.name
                                output.content[idx] = ToolCall(
                                    id=bd.get("id", ""),
                                    name=tc_delta.function.name,
                                    arguments=bd.get("arguments", {}),
                                )
                            if tc_delta.function and tc_delta.function.arguments:
                                partial_json = bd.get("partial_json", "")
                                bd["partial_json"] = partial_json + tc_delta.function.arguments
                                bd["arguments"] = parse_streaming_json(bd["partial_json"])
                                output.content[idx] = ToolCall(
                                    id=bd.get("id", ""),
                                    name=bd.get("name", ""),
                                    arguments=bd["arguments"],
                                )
                                stream.push(
                                    ToolCallDeltaEvent(
                                        content_index=idx,
                                        delta=tc_delta.function.arguments,
                                        partial=output,
                                    )
                                )

                # Handle finish reason
                if finish_reason:
                    stop_reason, error_msg = _map_stop_reason(finish_reason)
                    output.stop_reason = stop_reason  # type: ignore[assignment]
                    if error_msg:
                        output.error_message = error_msg

                    # Finalize tool calls: strip scratch buffers
                    for i, _block in enumerate(output.content):
                        if isinstance(_block, ToolCall):
                            has_tool_calls = True
                            bd = block_map.get(i)
                            if bd:
                                output.content[i] = ToolCall(
                                    id=bd.get("id", ""),
                                    name=bd.get("name", ""),
                                    arguments=bd.get("arguments", {}),
                                )
                            stream.push(
                                ToolCallEndEvent(
                                    content_index=i,
                                    tool_call=output.content[i],  # type: ignore[arg-type]
                                    partial=output,
                                )
                            )

                    break

            # If we have tool calls and stop reason is stop, change to toolUse
            if has_tool_calls and output.stop_reason == "stop":
                output.stop_reason = "toolUse"  # type: ignore[assignment]

            stream.push(DoneEvent(reason=output.stop_reason, message=output))  # type: ignore[arg-type]
            stream.end()
        except Exception as error:
            output.stop_reason = "error"  # type: ignore[assignment]
            output.error_message = str(error)
            stream.push(ErrorEvent(reason=output.stop_reason, error=output))  # type: ignore[arg-type]
            stream.end()

    asyncio.ensure_future(_process())

    return stream


def stream_simple_openai_completions(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    """Simple stream function for OpenAI Chat Completions API."""
    api_key = (options.api_key if options else None) or get_env_api_key(model.provider)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    reasoning_effort = None
    if options and options.reasoning:
        clamped = clamp_thinking_level(model, options.reasoning)
        if clamped != "off":
            reasoning_effort = clamped

    return stream_openai_completions(
        model,
        context,
        OpenAICompletionsOptions(
            **base.__dict__,
            reasoning_effort=reasoning_effort,
        ),
    )
