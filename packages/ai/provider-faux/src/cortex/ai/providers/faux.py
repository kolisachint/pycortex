"""Faux AI provider for testing.

Mechanical port of hoocode's ``packages/ai/src/providers/faux.ts``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import random
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, cast, overload

from cortex.ai.models import ApiProvider, register_api_provider, unregister_api_providers
from cortex.ai.stream import AssistantMessageEventStream, create_assistant_message_event_stream
from cortex.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_API = "faux"
DEFAULT_PROVIDER = "faux"
DEFAULT_MODEL_ID = "faux-1"
DEFAULT_MODEL_NAME = "Faux Model"
DEFAULT_BASE_URL = "http://localhost:0"
DEFAULT_MIN_TOKEN_SIZE = 3
DEFAULT_MAX_TOKEN_SIZE = 5


def _make_default_usage() -> Usage:
    """Return a fresh default Usage object (zero counts, zero costs)."""
    return Usage(
        input=0,
        output=0,
        cache_read=0,
        cache_write=0,
        total_tokens=0,
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "total": 0.0},
    )


DEFAULT_USAGE: Usage = _make_default_usage()


# ---------------------------------------------------------------------------
# Public type definitions
# ---------------------------------------------------------------------------


@dataclass
class FauxModelDefinition:
    """Minimal model specification for register_faux_provider().

    Port of TS ``FauxModelDefinition`` interface.
    """

    id: str
    name: str | None = None
    reasoning: bool | None = None
    input: list[Literal["text", "image"]] | None = None
    cost: dict[str, float] | None = None
    context_window: int | None = None
    max_tokens: int | None = None


# Union type alias: a content block that faux helpers can produce.
FauxContentBlock = TextContent | ThinkingContent | ToolCall

# Callable type alias for response factories.
# Signature: (context, options, state, model) -> AssistantMessage | coroutine
FauxResponseFactory = Callable[..., "AssistantMessage"]

# A response can be a pre-built message OR a factory callable.
FauxResponseStep = "AssistantMessage | FauxResponseFactory"


@dataclass
class RegisterFauxProviderOptions:
    """Options accepted by register_faux_provider().

    Port of TS ``RegisterFauxProviderOptions`` interface.
    """

    api: str | None = None
    provider: str | None = None
    models: list[FauxModelDefinition] | None = None
    tokens_per_second: float | None = None
    token_size: dict[str, int] | None = None


class _ProviderState:
    """Mutable call-count state shared by all stream invocations.

    Tests access ``registration.state.call_count``.
    """

    def __init__(self) -> None:
        self.call_count: int = 0


class FauxProviderRegistration:
    """Return value of :func:`register_faux_provider`.

    Port of TS ``FauxProviderRegistration`` interface.
    """

    def __init__(
        self,
        api: str,
        models: list[Model],
        get_model_fn: Callable[[str | None], Model | None],
        state: _ProviderState,
        set_responses: Callable[[list[Any]], None],
        append_responses: Callable[[list[Any]], None],
        get_pending_response_count: Callable[[], int],
        unregister: Callable[[], None],
    ) -> None:
        self.api = api
        self.models = models
        self._get_model_fn = get_model_fn
        self.state = state
        self.set_responses = set_responses
        self.append_responses = append_responses
        self.get_pending_response_count = get_pending_response_count
        self.unregister = unregister

    @overload
    def get_model(self) -> Model: ...
    @overload
    def get_model(self, model_id: str) -> Model | None: ...
    def get_model(self, model_id: str | None = None) -> Model | None:
        """Return the model matching *model_id*, or the first model when omitted."""
        return self._get_model_fn(model_id)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: one token per 4 characters (mirrors the TS port)."""
    return math.ceil(len(text) / 4)


def _random_id(prefix: str) -> str:
    """Generate a random ID in the style of the TS helper.

    JS: ``prefix:Date.now():Math.random().toString(36).slice(2)``
    """
    rand_chars = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{prefix}:{int(time.time() * 1000)}:{rand_chars}"


def _get_opt(options: Any, key: str, default: Any = None) -> Any:
    """Extract an option value from a dict *or* a StreamOptions-like object."""
    if options is None:
        return default
    if isinstance(options, dict):
        return options.get(key, default)
    return getattr(options, key, default)


def _content_to_text(content: str | list[TextContent | ImageContent]) -> str:
    """Serialise user/tool-result content to plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if block.type == "text":
            parts.append(block.text)
        else:  # image
            parts.append(f"[image:{block.mime_type}:{len(block.data)}]")
    return "\n".join(parts)


def _assistant_content_to_text(content: list[TextContent | ThinkingContent | ToolCall]) -> str:
    """Serialise assistant content blocks to plain text."""
    parts: list[str] = []
    for block in content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type == "thinking":
            parts.append(block.thinking)
        else:  # toolCall
            args = json.dumps(block.arguments, separators=(",", ":"), ensure_ascii=False)
            parts.append(f"{block.name}:{args}")
    return "\n".join(parts)


def _tool_result_to_text(message: ToolResultMessage) -> str:
    """Serialise a tool-result message to plain text."""
    parts = [message.tool_name]
    for block in message.content:
        parts.append(_content_to_text([block]))
    return "\n".join(parts)


def _message_to_text(message: Message) -> str:
    """Dispatch to the correct serialiser for each message role."""
    if message.role == "user":
        return _content_to_text(message.content)
    if message.role == "assistant":
        return _assistant_content_to_text(message.content)
    # toolResult
    return _tool_result_to_text(message)  # type: ignore[arg-type]


def _serialize_context(context: Context) -> str:
    """Serialise a full context to a single string for token estimation."""
    parts: list[str] = []
    if context.system_prompt:
        parts.append(f"system:{context.system_prompt}")
    for message in context.messages:
        parts.append(f"{message.role}:{_message_to_text(message)}")
    if context.tools:
        tools_data = [t.model_dump() for t in context.tools]
        parts.append(f"tools:{json.dumps(tools_data, separators=(',', ':'), ensure_ascii=False)}")
    return "\n\n".join(parts)


def _common_prefix_length(a: str, b: str) -> int:
    """Return the length of the common prefix of *a* and *b*."""
    length = min(len(a), len(b))
    index = 0
    while index < length and a[index] == b[index]:
        index += 1
    return index


def _with_usage_estimate(
    message: AssistantMessage,
    context: Context,
    options: Any,
    prompt_cache: dict[str, str],
) -> AssistantMessage:
    """Return a copy of *message* with realistic token-usage estimates."""
    prompt_text = _serialize_context(context)
    prompt_tokens = _estimate_tokens(prompt_text)
    output_tokens = _estimate_tokens(_assistant_content_to_text(message.content))
    input_tokens = prompt_tokens
    cache_read = 0
    cache_write = 0

    session_id: str | None = _get_opt(options, "session_id")
    cache_retention: str | None = _get_opt(options, "cache_retention")

    if session_id and cache_retention != "none":
        previous_prompt = prompt_cache.get(session_id)
        if previous_prompt is not None:
            cached_chars = _common_prefix_length(previous_prompt, prompt_text)
            cache_read = _estimate_tokens(previous_prompt[:cached_chars])
            cache_write = _estimate_tokens(prompt_text[cached_chars:])
            input_tokens = max(0, prompt_tokens - cache_read)
        else:
            cache_write = prompt_tokens
        prompt_cache[session_id] = prompt_text

    return message.model_copy(
        update={
            "usage": Usage(
                input=input_tokens,
                output=output_tokens,
                cache_read=cache_read,
                cache_write=cache_write,
                total_tokens=input_tokens + output_tokens + cache_read + cache_write,
                cost={
                    "input": 0.0,
                    "output": 0.0,
                    "cache_read": 0.0,
                    "cache_write": 0.0,
                    "total": 0.0,
                },
            )
        }
    )


def _split_string_by_token_size(
    text: str,
    min_token_size: int,
    max_token_size: int,
) -> list[str]:
    """Split *text* into chunks whose sizes vary randomly between token bounds."""
    chunks: list[str] = []
    index = 0
    while index < len(text):
        token_size = min_token_size + math.floor(
            random.random() * (max_token_size - min_token_size + 1)
        )
        char_size = max(1, token_size * 4)
        chunks.append(text[index : index + char_size])
        index += char_size
    return chunks if chunks else [""]


def _clone_message(
    message: AssistantMessage,
    api: str,
    provider: str,
    model_id: str,
) -> AssistantMessage:
    """Deep-clone *message* and rewrite api/provider/model fields."""
    cloned = message.model_copy(deep=True)
    return cloned.model_copy(
        update={
            "api": api,
            "provider": provider,
            "model": model_id,
            "timestamp": cloned.timestamp,
            "usage": cloned.usage,
        }
    )


def _create_error_message(
    error: Any,
    api: str,
    provider: str,
    model_id: str,
) -> AssistantMessage:
    """Build an error AssistantMessage from an exception or arbitrary value."""
    error_msg = str(error) if isinstance(error, BaseException) else str(error)
    return AssistantMessage(
        role="assistant",
        content=[],
        api=api,
        provider=provider,
        model=model_id,
        usage=_make_default_usage(),
        stop_reason="error",
        error_message=error_msg,
        timestamp=int(time.time() * 1000),
    )


def _create_aborted_message(partial: AssistantMessage) -> AssistantMessage:
    """Return a copy of *partial* marked as aborted."""
    return partial.model_copy(
        update={
            "stop_reason": "aborted",
            "error_message": "Request was aborted",
            "timestamp": int(time.time() * 1000),
        }
    )


async def _schedule_chunk(chunk: str, tokens_per_second: float | None) -> None:
    """Yield the event loop (or sleep proportionally to simulated token rate)."""
    if not tokens_per_second or tokens_per_second <= 0:
        await asyncio.sleep(0)
    else:
        delay_s = _estimate_tokens(chunk) / tokens_per_second
        await asyncio.sleep(delay_s)


async def _stream_with_deltas(
    stream_obj: AssistantMessageEventStream,
    message: AssistantMessage,
    min_token_size: int,
    max_token_size: int,
    tokens_per_second: float | None,
    signal: Any,
) -> None:
    """Emit streaming events for every content block in *message*."""
    # Accumulate content blocks as they are streamed.
    partial_content: list[TextContent | ThinkingContent | ToolCall] = []

    def make_partial() -> AssistantMessage:
        """Snapshot partial as a fresh model (shallow-copy of list)."""
        return message.model_copy(update={"content": list(partial_content)})

    # Pre-start abort check.
    if signal is not None and getattr(signal, "aborted", False):
        aborted = _create_aborted_message(make_partial())
        stream_obj.push(ErrorEvent(reason="aborted", error=aborted))
        stream_obj.end(aborted)
        return

    stream_obj.push(StartEvent(partial=make_partial()))

    for index, block in enumerate(message.content):
        # Per-block abort check.
        if signal is not None and getattr(signal, "aborted", False):
            aborted = _create_aborted_message(make_partial())
            stream_obj.push(ErrorEvent(reason="aborted", error=aborted))
            stream_obj.end(aborted)
            return

        # --- thinking block ---
        if block.type == "thinking":
            accumulated = ""
            partial_content.append(ThinkingContent(thinking=""))
            stream_obj.push(ThinkingStartEvent(content_index=index, partial=make_partial()))

            for chunk in _split_string_by_token_size(
                block.thinking, min_token_size, max_token_size
            ):
                await _schedule_chunk(chunk, tokens_per_second)
                if signal is not None and getattr(signal, "aborted", False):
                    aborted = _create_aborted_message(make_partial())
                    stream_obj.push(ErrorEvent(reason="aborted", error=aborted))
                    stream_obj.end(aborted)
                    return
                accumulated += chunk
                partial_content[index] = ThinkingContent(thinking=accumulated)
                stream_obj.push(
                    ThinkingDeltaEvent(content_index=index, delta=chunk, partial=make_partial())
                )

            stream_obj.push(
                ThinkingEndEvent(
                    content_index=index, content=block.thinking, partial=make_partial()
                )
            )
            continue

        # --- text block ---
        if block.type == "text":
            accumulated = ""
            partial_content.append(TextContent(text=""))
            stream_obj.push(TextStartEvent(content_index=index, partial=make_partial()))

            for chunk in _split_string_by_token_size(block.text, min_token_size, max_token_size):
                await _schedule_chunk(chunk, tokens_per_second)
                if signal is not None and getattr(signal, "aborted", False):
                    aborted = _create_aborted_message(make_partial())
                    stream_obj.push(ErrorEvent(reason="aborted", error=aborted))
                    stream_obj.end(aborted)
                    return
                accumulated += chunk
                partial_content[index] = TextContent(text=accumulated)
                stream_obj.push(
                    TextDeltaEvent(content_index=index, delta=chunk, partial=make_partial())
                )

            stream_obj.push(
                TextEndEvent(content_index=index, content=block.text, partial=make_partial())
            )
            continue

        # --- toolCall block ---
        partial_content.append(ToolCall(id=block.id, name=block.name, arguments={}))
        stream_obj.push(ToolCallStartEvent(content_index=index, partial=make_partial()))

        args_json = json.dumps(block.arguments, separators=(",", ":"), ensure_ascii=False)
        for chunk in _split_string_by_token_size(args_json, min_token_size, max_token_size):
            await _schedule_chunk(chunk, tokens_per_second)
            if signal is not None and getattr(signal, "aborted", False):
                aborted = _create_aborted_message(make_partial())
                stream_obj.push(ErrorEvent(reason="aborted", error=aborted))
                stream_obj.end(aborted)
                return
            stream_obj.push(
                ToolCallDeltaEvent(content_index=index, delta=chunk, partial=make_partial())
            )

        # Set final arguments and emit toolcall_end.
        partial_content[index] = ToolCall(id=block.id, name=block.name, arguments=block.arguments)
        stream_obj.push(
            ToolCallEndEvent(content_index=index, tool_call=block, partial=make_partial())
        )

    # Terminal event.
    if message.stop_reason == "error":
        stream_obj.push(ErrorEvent(reason="error", error=message))
        stream_obj.end(message)
        return
    if message.stop_reason == "aborted":
        stream_obj.push(ErrorEvent(reason="aborted", error=message))
        stream_obj.end(message)
        return

    stream_obj.push(DoneEvent(reason=message.stop_reason, message=message))  # type: ignore[arg-type]
    stream_obj.end(message)


# ---------------------------------------------------------------------------
# Public helper constructors
# ---------------------------------------------------------------------------


def faux_text(text: str) -> TextContent:
    """Create a :class:`TextContent` block.  Port of TS ``fauxText``."""
    return TextContent(type="text", text=text)


def faux_thinking(thinking: str) -> ThinkingContent:
    """Create a :class:`ThinkingContent` block.  Port of TS ``fauxThinking``."""
    return ThinkingContent(type="thinking", thinking=thinking)


def faux_tool_call(
    name: str,
    arguments: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> ToolCall:
    """Create a :class:`ToolCall` block.  Port of TS ``fauxToolCall``."""
    opts = options or {}
    return ToolCall(
        type="toolCall",
        id=opts.get("id") or _random_id("tool"),
        name=name,
        arguments=arguments,
    )


def _normalize_faux_assistant_content(
    content: str | TextContent | ThinkingContent | ToolCall | list[Any],
) -> list[TextContent | ThinkingContent | ToolCall]:
    if isinstance(content, str):
        return [faux_text(content)]
    if isinstance(content, list):
        return content
    return [content]


def faux_assistant_message(
    content: str | TextContent | ThinkingContent | ToolCall | list[Any],
    *,
    stop_reason: Any = None,
    error_message: str | None = None,
    response_id: str | None = None,
    timestamp: int | None = None,
) -> AssistantMessage:
    """Create a minimal :class:`AssistantMessage`.  Port of TS ``fauxAssistantMessage``."""
    return AssistantMessage(
        role="assistant",
        content=_normalize_faux_assistant_content(content),
        api=DEFAULT_API,
        provider=DEFAULT_PROVIDER,
        model=DEFAULT_MODEL_ID,
        usage=_make_default_usage(),
        stop_reason=stop_reason if stop_reason is not None else "stop",
        error_message=error_message,
        response_id=response_id,
        timestamp=timestamp if timestamp is not None else int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------


def register_faux_provider(
    *,
    api: str | None = None,
    provider: str | None = None,
    models: list[FauxModelDefinition | dict[str, Any]] | None = None,
    tokens_per_second: float | None = None,
    token_size: dict[str, int] | None = None,
) -> FauxProviderRegistration:
    """Register a faux (test) AI provider and return a handle.

    Port of TS ``registerFauxProvider``.

    Example::

        reg = register_faux_provider()
        reg.set_responses([faux_assistant_message("hello")])
        response = await complete(reg.get_model(), context)
    """
    api = api if api is not None else _random_id(DEFAULT_API)
    provider_name = provider if provider is not None else DEFAULT_PROVIDER
    source_id = _random_id("faux-provider")

    token_size = token_size or {}
    raw_min = token_size.get("min", DEFAULT_MIN_TOKEN_SIZE)
    raw_max = token_size.get("max", DEFAULT_MAX_TOKEN_SIZE)
    min_token_size = max(1, min(raw_min, raw_max))
    max_token_size = max(min_token_size, raw_max)

    pending_responses: list[Any] = []
    state = _ProviderState()
    prompt_cache: dict[str, str] = {}

    def _coerce_definition(defn: FauxModelDefinition | dict[str, Any]) -> FauxModelDefinition:
        if isinstance(defn, FauxModelDefinition):
            return defn
        return FauxModelDefinition(**defn)

    model_definitions = (
        [_coerce_definition(d) for d in models]
        if models
        else [
            FauxModelDefinition(
                id=DEFAULT_MODEL_ID,
                name=DEFAULT_MODEL_NAME,
                reasoning=False,
                input=["text", "image"],
                cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
                context_window=128000,
                max_tokens=16384,
            )
        ]
    )

    resolved_models: list[Model] = [
        Model(
            id=defn.id,
            name=defn.name if defn.name is not None else defn.id,
            api=api,
            provider=provider_name,
            base_url=DEFAULT_BASE_URL,
            reasoning=defn.reasoning if defn.reasoning is not None else False,
            input=defn.input if defn.input is not None else ["text", "image"],
            cost=(
                defn.cost
                if defn.cost is not None
                else {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}
            ),
            context_window=defn.context_window if defn.context_window is not None else 128000,
            max_tokens=defn.max_tokens if defn.max_tokens is not None else 16384,
        )
        for defn in model_definitions
    ]

    def _stream_fn(  # type: ignore[return]
        request_model: Model,
        context: Context,
        stream_options: Any,
    ) -> AssistantMessageEventStream:
        """Synchronous stream factory — schedules async work via ensure_future."""
        outer = create_assistant_message_event_stream()
        step = pending_responses.pop(0) if pending_responses else None
        state.call_count += 1

        async def _run() -> None:
            try:
                if step is None:
                    msg = _create_error_message(
                        Exception("No more faux responses queued"),
                        api,
                        provider_name,
                        request_model.id,
                    )
                    msg = _with_usage_estimate(msg, context, stream_options, prompt_cache)
                    outer.push(ErrorEvent(reason="error", error=msg))
                    outer.end(msg)
                    return

                resolved: AssistantMessage
                if callable(step):
                    result = step(context, stream_options, state, request_model)
                    if inspect.iscoroutine(result):
                        resolved = await result
                    else:
                        resolved = cast(AssistantMessage, result)
                else:
                    resolved = step

                msg = _clone_message(resolved, api, provider_name, request_model.id)
                msg = _with_usage_estimate(msg, context, stream_options, prompt_cache)
                sig = _get_opt(stream_options, "signal")
                await _stream_with_deltas(
                    outer, msg, min_token_size, max_token_size, tokens_per_second, sig
                )
            except Exception as exc:
                error_msg = _create_error_message(exc, api, provider_name, request_model.id)
                outer.push(ErrorEvent(reason="error", error=error_msg))
                outer.end(error_msg)

        asyncio.ensure_future(_run())
        return outer

    def _stream_simple_fn(
        model: Model,
        context: Context,
        stream_options: Any,
    ) -> AssistantMessageEventStream:
        return _stream_fn(model, context, stream_options)

    register_api_provider(
        ApiProvider(
            api=api,
            stream=_stream_fn,  # type: ignore[arg-type]
            stream_simple=_stream_simple_fn,  # type: ignore[arg-type]
        ),
        source_id,
    )

    def get_model_fn(model_id: str | None = None) -> Model | None:
        if model_id is None:
            return resolved_models[0] if resolved_models else None
        return next((m for m in resolved_models if m.id == model_id), None)

    def set_responses(responses: list[Any]) -> None:
        nonlocal pending_responses
        pending_responses = list(responses)

    def append_responses(responses: list[Any]) -> None:
        pending_responses.extend(responses)

    def get_pending_response_count() -> int:
        return len(pending_responses)

    def unregister() -> None:
        unregister_api_providers(source_id)

    return FauxProviderRegistration(
        api=api,
        models=resolved_models,
        get_model_fn=get_model_fn,
        state=state,
        set_responses=set_responses,
        append_responses=append_responses,
        get_pending_response_count=get_pending_response_count,
        unregister=unregister,
    )
