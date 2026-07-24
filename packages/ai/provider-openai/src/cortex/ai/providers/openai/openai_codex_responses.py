"""OpenAI Codex Responses API provider.

Mechanical port of hoocode's ``packages/ai/src/providers/openai-codex-responses.ts``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from cortex.ai.env import get_env_api_key
from cortex.ai.providers._common import resolve_cache_retention
from cortex.ai.providers.openai.openai_responses import (
    OpenAIResponsesOptions,
    apply_service_tier_pricing,
    get_compat,
    get_prompt_cache_retention,
)
from cortex.ai.providers.openai.openai_responses_shared import (
    OpenAIResponsesStreamOptions,
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from cortex.ai.stream import AssistantMessageEventStream, create_assistant_message_event_stream
from cortex.ai.types import (
    AssistantMessage,
    Context,
    Model,
    Usage,
)

from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_codex_client(
    model: Model,
    context: Context,
    api_key: str | None = None,
    options_headers: dict[str, str] | None = None,
    session_id: str | None = None,
) -> AsyncOpenAI:
    """Create an OpenAI client for Codex."""
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OpenAI API key is required. Set OPENAI_API_KEY environment variable.")

    headers = dict(model.headers) if model.headers else {}

    if session_id:
        headers["x-client-request-id"] = session_id

    if options_headers:
        headers.update(options_headers)

    return AsyncOpenAI(
        api_key=api_key,
        base_url=model.base_url,
        default_headers=headers,
    )


# ---------------------------------------------------------------------------
# Stream functions
# ---------------------------------------------------------------------------


def stream_openai_codex_responses(
    model: Model,
    context: Context,
    options: OpenAIResponsesOptions | None = None,
) -> AssistantMessageEventStream:
    """Generate function for OpenAI Codex Responses API."""
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
            client = _create_codex_client(
                model, context, api_key, options.headers if options else None, cache_session_id
            )

            # Build params
            messages = convert_responses_messages(
                model, context, frozenset(["openai", "openai-codex", "opencode"])
            )
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
            if context.tools and len(context.tools) > 0:
                params["tools"] = convert_responses_tools(context.tools)

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

            stream.push({"type": "done", "reason": output.stop_reason, "message": output})  # type: ignore[arg-type]
            stream.end()
        except Exception as error:
            output.stop_reason = "error"
            output.error_message = str(error)
            stream.push({"type": "error", "reason": output.stop_reason, "error": output})  # type: ignore[arg-type]
            stream.end()

    asyncio.ensure_future(_process())

    return stream
