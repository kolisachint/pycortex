"""OpenAI Responses API provider.

Mechanical port of hoocode's ``packages/ai/src/providers/openai-responses.ts``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from cortex.ai.env import get_env_api_key
from cortex.ai.models import clamp_thinking_level
from cortex.ai.providers._common import (
    build_base_options,
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
    resolve_cache_retention,
)
from cortex.ai.providers.openai.openai_responses_shared import (
    ConvertResponsesToolsOptions,
    OpenAIResponsesStreamOptions,
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from cortex.ai.stream import AssistantMessageEventStream, create_assistant_message_event_stream
from cortex.ai.types import (
    AssistantMessage,
    CacheRetention,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    Usage,
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
class OpenAIResponsesOptions(StreamOptions):
    """OpenAI Responses-specific options."""

    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    service_tier: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_compat(model: Model) -> dict[str, Any]:
    """Get compatibility settings for the model."""
    compat = model.compat
    if compat is None:
        return {"sendSessionIdHeader": True, "supportsLongCacheRetention": True}
    # Convert Pydantic model to dict
    if hasattr(compat, "model_dump"):
        compat_dict = compat.model_dump()
    elif hasattr(compat, "dict"):
        compat_dict = compat.dict()  # type: ignore[deprecated]
    elif isinstance(compat, dict):
        compat_dict = compat
    else:
        compat_dict = {}
    return {
        "sendSessionIdHeader": compat_dict.get("sendSessionIdHeader", True),
        "supportsLongCacheRetention": compat_dict.get("supportsLongCacheRetention", True),
    }


def get_prompt_cache_retention(
    compat: dict[str, Any],
    cache_retention: CacheRetention,
) -> str | None:
    """Get prompt cache retention value."""
    return "24h" if cache_retention == "long" and compat.get("supportsLongCacheRetention") else None


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
            raise ValueError(
                "OpenAI API key is required. "
                "Set OPENAI_API_KEY environment variable or pass it as an argument."
            )

    compat = get_compat(model)
    headers = dict(model.headers) if model.headers else {}

    if model.provider == "github-copilot":
        has_images = has_copilot_vision_input(context.messages)
        copilot_headers = build_copilot_dynamic_headers(
            messages=context.messages,
            has_images=has_images,
        )
        headers.update(copilot_headers)

    if session_id:
        if compat.get("sendSessionIdHeader", True):
            headers["session_id"] = session_id
        headers["x-client-request-id"] = session_id

    # Merge options headers last so they can override defaults
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
    options: OpenAIResponsesOptions | None = None,
) -> dict[str, Any]:
    """Build parameters for the OpenAI Responses API."""
    messages = convert_responses_messages(model, context, OPENAI_TOOL_CALL_PROVIDERS)

    cache_retention = resolve_cache_retention(options.cache_retention if options else None)
    compat = get_compat(model)
    params: dict[str, Any] = {
        "model": model.id,
        "input": messages,
        "stream": True,
        "prompt_cache_key": None
        if cache_retention == "none"
        else (options.session_id if options else None),
        "prompt_cache_retention": get_prompt_cache_retention(compat, cache_retention),
        "store": False,
    }

    if options and options.max_tokens:
        params["max_output_tokens"] = options.max_tokens

    if options and options.temperature is not None:
        params["temperature"] = options.temperature

    if options and options.service_tier is not None:
        params["service_tier"] = options.service_tier

    if context.tools and len(context.tools) > 0:
        params["tools"] = convert_responses_tools(
            context.tools,
            ConvertResponsesToolsOptions(
                constrainToolCalls=options.constrain_tool_calls if options else None
            ),
        )

    if model.reasoning:
        if (options and options.reasoning_effort) or (options and options.reasoning_summary):
            effort = "medium"
            if options and options.reasoning_effort:
                thinking_level_map = model.thinking_level_map if model.thinking_level_map else {}
                effort = thinking_level_map.get(options.reasoning_effort, options.reasoning_effort)
            params["reasoning"] = {
                "effort": effort,
                "summary": (options.reasoning_summary if options else None) or "auto",
            }
            params["include"] = ["reasoning.encrypted_content"]
        elif model.provider != "github-copilot":
            thinking_level_map = model.thinking_level_map if model.thinking_level_map else {}
            off_value = thinking_level_map.get("off")
            if off_value != "null":
                params["reasoning"] = {
                    "effort": off_value or "none",
                }

    return params


def _get_service_tier_cost_multiplier(
    model: Model,
    service_tier: str | None,
) -> float:
    """Get the cost multiplier for a service tier."""
    if service_tier == "flex":
        return 0.5
    elif service_tier == "priority":
        return 2.5 if model.id == "gpt-5.5" else 2
    return 1


def apply_service_tier_pricing(
    usage: Usage,
    service_tier: str | None,
    model: Model,
) -> None:
    """Apply service tier pricing to usage."""
    multiplier = _get_service_tier_cost_multiplier(model, service_tier)
    if multiplier == 1:
        return

    usage.cost["input"] *= multiplier
    usage.cost["output"] *= multiplier
    usage.cost["cacheRead"] *= multiplier
    usage.cost["cacheWrite"] *= multiplier
    usage.cost["total"] = (
        usage.cost["input"]
        + usage.cost["output"]
        + usage.cost["cacheRead"]
        + usage.cost["cacheWrite"]
    )


# ---------------------------------------------------------------------------
# Stream functions
# ---------------------------------------------------------------------------


def stream_openai_responses(
    model: Model,
    context: Context,
    options: OpenAIResponsesOptions | None = None,
) -> AssistantMessageEventStream:
    """Generate function for OpenAI Responses API."""
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
            # Create OpenAI client
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

            # Create the stream
            openai_stream = await client.responses.create(**params)
            stream.push({"type": "start", "partial": output})  # type: ignore[arg-type]

            await process_responses_stream(
                openai_stream,
                output,
                stream,
                model,
                OpenAIResponsesStreamOptions(
                    serviceTier=options.service_tier if options else None,
                    applyServiceTierPricing=lambda usage, tier: apply_service_tier_pricing(
                        usage, tier, model
                    ),
                ),
            )

            if options and options.signal and getattr(options.signal, "aborted", False):
                raise Exception("Request was aborted")

            if output.stop_reason in ("aborted", "error"):
                raise Exception("An unknown error occurred")

            stream.push({"type": "done", "reason": output.stop_reason, "message": output})  # type: ignore[arg-type]
            stream.end()
        except Exception as error:
            for block in output.content:
                block.pop("index", None)  # type: ignore[union-attr]
                # partialJson is only a streaming scratch buffer; never persist it.
                block.pop("partialJson", None)  # type: ignore[union-attr]
            output.stop_reason = (
                "aborted"
                if (options and options.signal and getattr(options.signal, "aborted", False))
                else "error"
            )
            output.error_message = str(error)
            stream.push({"type": "error", "reason": output.stop_reason, "error": output})  # type: ignore[arg-type]
            stream.end()

    asyncio.ensure_future(_process())

    return stream


def stream_simple_openai_responses(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | None = None,
) -> AssistantMessageEventStream:
    """Simple stream function for OpenAI Responses API."""
    api_key = (options.api_key if options else None) or get_env_api_key(model.provider)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    reasoning_effort = None
    if options and options.reasoning:
        clamped = clamp_thinking_level(model, options.reasoning)
        if clamped != "off":
            reasoning_effort = clamped

    return stream_openai_responses(
        model,
        context,
        OpenAIResponsesOptions(
            **base.__dict__,
            reasoning_effort=reasoning_effort,
        ),
    )
