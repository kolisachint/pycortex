"""High-level stream/complete entry points.

Port of hoocode's `packages/ai/src/stream.ts`.
"""

from __future__ import annotations

from typing import Any

from cortex.ai.models import ApiProvider, get_api_provider
from cortex.ai.stream.event_stream import AssistantMessageEventStream
from cortex.ai.types import AssistantMessage, Context, Model


def _resolve_api_provider(api: str) -> ApiProvider:
    provider = get_api_provider(api)
    if provider is None:
        raise ValueError(f"No API provider registered for api: {api}")
    return provider


def stream(
    model: Model,
    context: Context,
    options: dict[str, Any] | None = None,
) -> AssistantMessageEventStream:
    provider = _resolve_api_provider(model.api)
    return provider.stream(model, context, options)


async def complete(
    model: Model,
    context: Context,
    options: dict[str, Any] | None = None,
) -> AssistantMessage:
    s = stream(model, context, options)
    return await s.result()


def stream_simple(
    model: Model,
    context: Context,
    options: dict[str, Any] | None = None,
) -> AssistantMessageEventStream:
    provider = _resolve_api_provider(model.api)
    return provider.stream_simple(model, context, options)


async def complete_simple(
    model: Model,
    context: Context,
    options: dict[str, Any] | None = None,
) -> AssistantMessage:
    s = stream_simple(model, context, options)
    return await s.result()
