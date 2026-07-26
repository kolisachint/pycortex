"""Google Vertex AI provider.

Mechanical port of hoocode's ``packages/ai/src/providers/google-vertex.ts``.

Vertex AI uses the same ``generateContent`` protocol as the Google Generative AI
provider but with GCP credential resolution (service-account JWT, Application
Default Credentials, ``GOOGLE_APPLICATION_CREDENTIALS``) and a different base URL
format (``https://{location}-aiplatform.googleapis.com``).

Like the Google provider (step 2.9), this talks directly to the REST API over
``httpx`` rather than pulling in the ``google-genai`` SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from cortex.ai.models import calculate_cost, clamp_thinking_level
from cortex.ai.providers._common import build_base_options
from cortex.ai.providers.google.shared import (
    convert_messages,
    convert_tools,
    is_thinking_part,
    map_stop_reason,
    map_tool_choice,
    retain_thought_signature,
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
    Usage,
)
from cortex.ai.util import sanitize_surrogates

__all__ = [
    "GoogleVertexClient",
    "GoogleVertexOptions",
    "build_params",
    "stream_google_vertex",
    "stream_simple_google_vertex",
]

# Vertex AI API version
_API_VERSION = "v1"

# Marker that indicates ADC credentials should be used instead of an API key
_GCP_VERTEX_CREDENTIALS_MARKER = "gcp-vertex-credentials"

# Regex for detecting placeholder API keys like "<authenticated>"
_PLACEHOLDER_API_KEY_RE = re.compile(r"^<[^>]+>$")

# Counter for generating unique tool call IDs (module-level, as in the TS).
_tool_call_counter = 0

_GEMMA4_RE = re.compile(r"gemma-?4")
_GEMINI3_PRO_RE = re.compile(r"gemini-3(?:\.\d+)?-pro")
_GEMINI3_FLASH_RE = re.compile(r"gemini-3(?:\.\d+)?-flash")


class GoogleVertexOptions(dict[str, Any]):
    """Options bag for :func:`stream_google_vertex`.

    A plain ``dict`` subclass: the TS type extends ``StreamOptions`` with
    ``toolChoice``, ``thinking``, ``project``, and ``location``.

    Recognised keys:
    - All ``StreamOptions`` fields
    - ``tool_choice`` ("auto" | "none" | "any")
    - ``thinking`` (``{"enabled": bool, "budget_tokens": int | None, "level": str | None}``)
    - ``project`` (GCP project ID)
    - ``location`` (GCP region, e.g. "us-central1")
    """


def _opt(options: Any, key: str, default: Any = None) -> Any:
    """Read an option from either a mapping or an attribute-style object."""
    if options is None:
        return default
    if isinstance(options, dict):
        return options.get(key, default)
    return getattr(options, key, default)


# ---------------------------------------------------------------------------
# Credential resolution (port of google-vertex.ts resolve* functions)
# ---------------------------------------------------------------------------


def _resolve_api_key(options: GoogleVertexOptions | dict[str, Any] | None) -> str | None:
    """Resolve API key from options or environment.

    Falls back to ADC when the key is a placeholder marker.
    """
    api_key = _opt(options, "api_key") or os.environ.get("GOOGLE_CLOUD_API_KEY")
    if not api_key:
        return None
    api_key = api_key.strip()
    if not api_key or api_key == _GCP_VERTEX_CREDENTIALS_MARKER or _is_placeholder_api_key(api_key):
        return None
    return api_key


def _is_placeholder_api_key(api_key: str) -> bool:
    """Check if an API key is a placeholder like ``<authenticated>``."""
    return bool(_PLACEHOLDER_API_KEY_RE.match(api_key))


def _resolve_project(options: GoogleVertexOptions | dict[str, Any] | None) -> str:
    """Resolve GCP project ID from options or environment.

    Raises ``ValueError`` if no project is found (required for ADC).
    """
    project = (
        _opt(options, "project")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    if not project:
        raise ValueError(
            "Vertex AI requires a project ID. Set GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT "
            "or pass project in options."
        )
    return project


def _resolve_location(options: GoogleVertexOptions | dict[str, Any] | None) -> str:
    """Resolve GCP location from options or environment.

    Raises ``ValueError`` if no location is found (required for ADC).
    """
    location = _opt(options, "location") or os.environ.get("GOOGLE_CLOUD_LOCATION")
    if not location:
        raise ValueError(
            "Vertex AI requires a location. Set GOOGLE_CLOUD_LOCATION or pass location in options."
        )
    return location


# ---------------------------------------------------------------------------
# Base URL handling (port of google-vertex.ts resolveCustomBaseUrl, etc.)
# ---------------------------------------------------------------------------


def _resolve_custom_base_url(base_url: str) -> str | None:
    """Resolve a custom base URL, returning None if it contains placeholders."""
    trimmed = base_url.strip()
    if not trimmed or "{location}" in trimmed:
        return None
    return trimmed


# ---------------------------------------------------------------------------
# HTTP client (port of google-vertex.ts GoogleGenAI usage)
# ---------------------------------------------------------------------------


class GoogleVertexClient:
    """Minimal Vertex AI ``streamGenerateContent`` client.

    Injectable: any object with a matching ``stream_generate_content`` is
    accepted via ``options["client"]``, which is how the tests avoid the network.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = headers or {}

    async def stream_generate_content(
        self, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        model_id = params["model"]
        url = (
            f"{self.base_url}/v1/projects/-/locations/-/publishers/google/models/"
            f"{model_id}:streamGenerateContent"
        )
        headers = {"Content-Type": "application/json", **self.headers}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        body = {k: v for k, v in params.items() if k != "model"}

        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream(
                "POST", url, params={"alt": "sse"}, headers=headers, json=body
            ) as response,
        ):
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"Vertex AI API error {response.status_code}: {detail}")
            async for chunk in _iter_sse_data(response.aiter_lines()):
                yield chunk


async def _iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded ``data:`` payloads from an SSE line stream."""
    async for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        yield json.loads(payload)


def _create_client(
    model: Model,
    project: str,
    location: str,
    options_headers: dict[str, str] | None = None,
) -> GoogleVertexClient:
    """Create a Vertex client using ADC (Application Default Credentials)."""
    headers: dict[str, str] = {}
    if model.headers or options_headers:
        headers = {**(model.headers or {}), **(options_headers or {})}

    base_url = _resolve_custom_base_url(model.base_url or "")
    if base_url:
        # Custom base URL - use it directly
        return GoogleVertexClient(base_url=base_url, headers=headers)

    # Default Vertex AI endpoint
    return GoogleVertexClient(
        base_url=f"https://{location}-aiplatform.googleapis.com",
        headers=headers,
    )


def _create_client_with_api_key(
    model: Model,
    api_key: str,
    options_headers: dict[str, str] | None = None,
) -> GoogleVertexClient:
    """Create a Vertex client using an API key."""
    headers: dict[str, str] = {}
    if model.headers or options_headers:
        headers = {**(model.headers or {}), **(options_headers or {})}

    base_url = _resolve_custom_base_url(model.base_url or "")
    if base_url:
        return GoogleVertexClient(base_url=base_url, api_key=api_key, headers=headers)

    # Default Vertex AI endpoint (without location, since API key doesn't need it)
    return GoogleVertexClient(
        base_url="https://aiplatform.googleapis.com",
        api_key=api_key,
        headers=headers,
    )


def create_client(
    model: Model,
    api_key: str | None = None,
    options_headers: dict[str, str] | None = None,
    options: GoogleVertexOptions | dict[str, Any] | None = None,
) -> GoogleVertexClient:
    """Create a Vertex client, choosing API key or ADC based on resolution."""
    resolved_key = _resolve_api_key(options)
    # Extract headers from options if not explicitly provided
    resolved_headers = options_headers or _opt(options, "headers")
    if resolved_key or api_key:
        return _create_client_with_api_key(model, resolved_key or api_key or "", resolved_headers)

    # Use ADC with project/location
    project = _resolve_project(options)
    location = _resolve_location(options)
    return _create_client(model, project, location, resolved_headers)


# ---------------------------------------------------------------------------
# Parameter building (port of google-vertex.ts buildParams)
# ---------------------------------------------------------------------------


def build_params(
    model: Model,
    context: Context,
    options: GoogleVertexOptions | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ``generateContent`` request payload."""
    contents = convert_messages(model, context)

    generation_config: dict[str, Any] = {}
    temperature = _opt(options, "temperature")
    if temperature is not None:
        generation_config["temperature"] = temperature
    max_tokens = _opt(options, "max_tokens")
    if max_tokens is not None:
        generation_config["maxOutputTokens"] = max_tokens

    config: dict[str, Any] = dict(generation_config)
    if context.system_prompt:
        config["systemInstruction"] = sanitize_surrogates(context.system_prompt)
    if context.tools:
        config["tools"] = convert_tools(context.tools)

    tool_choice = _opt(options, "tool_choice")
    if context.tools and tool_choice:
        config["toolConfig"] = {"functionCallingConfig": {"mode": map_tool_choice(tool_choice)}}
    else:
        config["toolConfig"] = None

    thinking = _opt(options, "thinking")
    thinking_enabled = _opt(thinking, "enabled", False) if thinking is not None else False
    if thinking_enabled and model.reasoning:
        thinking_config: dict[str, Any] = {"includeThoughts": True}
        level = _opt(thinking, "level")
        budget_tokens = _opt(thinking, "budget_tokens")
        if level is not None:
            thinking_config["thinkingLevel"] = level
        elif budget_tokens is not None:
            thinking_config["thinkingBudget"] = budget_tokens
        config["thinkingConfig"] = thinking_config
    elif model.reasoning and thinking is not None and not thinking_enabled:
        config["thinkingConfig"] = _disabled_thinking_config(model)

    signal = _opt(options, "signal")
    if signal is not None and getattr(signal, "aborted", False):
        raise RuntimeError("Request aborted")

    return {"model": model.id, "contents": contents, "config": config}


# ---------------------------------------------------------------------------
# Thinking level helpers (shared with google.py but duplicated for independence)
# ---------------------------------------------------------------------------


def _is_gemma4(model: Model) -> bool:
    return _GEMMA4_RE.search(model.id.lower()) is not None


def _is_gemini3_pro(model: Model) -> bool:
    return _GEMINI3_PRO_RE.search(model.id.lower()) is not None


def _is_gemini3_flash(model: Model) -> bool:
    return _GEMINI3_FLASH_RE.search(model.id.lower()) is not None


def _disabled_thinking_config(model: Model) -> dict[str, Any]:
    """The closest thing to "thinking off" each family supports.

    Per Google's docs Gemini 3.1 Pro cannot disable thinking at all, and Gemini 3
    Flash / Flash-Lite cannot fully either, so those get the lowest supported
    ``thinkingLevel`` *without* ``includeThoughts`` — hidden thinking stays
    invisible. Gemini 2.x can genuinely disable it with ``thinkingBudget: 0``.
    """
    if _is_gemini3_pro(model):
        return {"thinkingLevel": "LOW"}
    if _is_gemini3_flash(model):
        return {"thinkingLevel": "MINIMAL"}
    if _is_gemma4(model):
        return {"thinkingLevel": "MINIMAL"}
    return {"thinkingBudget": 0}


_GEMINI3_PRO_LEVELS = {"minimal": "LOW", "low": "LOW", "medium": "HIGH", "high": "HIGH"}
_GEMMA4_LEVELS = {"minimal": "MINIMAL", "low": "MINIMAL", "medium": "HIGH", "high": "HIGH"}
_DEFAULT_LEVELS = {"minimal": "MINIMAL", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}


def _thinking_level(effort: str, model: Model) -> str:
    if _is_gemini3_pro(model):
        return _GEMINI3_PRO_LEVELS[effort]
    if _is_gemma4(model):
        return _GEMMA4_LEVELS[effort]
    return _DEFAULT_LEVELS[effort]


_BUDGETS_25_PRO = {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}
_BUDGETS_25_FLASH_LITE = {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576}
_BUDGETS_25_FLASH = {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}


def _google_budget(model: Model, effort: str, custom_budgets: Any = None) -> int:
    custom = _opt(custom_budgets, effort)
    if custom is not None:
        return int(custom)
    if "2.5-pro" in model.id:
        return _BUDGETS_25_PRO[effort]
    if "2.5-flash-lite" in model.id:
        return _BUDGETS_25_FLASH_LITE[effort]
    if "2.5-flash" in model.id:
        return _BUDGETS_25_FLASH[effort]
    # -1 asks Gemini for a dynamic budget.
    return -1


# ---------------------------------------------------------------------------
# Tool call ID generation (port of google-vertex.ts toolCallCounter)
# ---------------------------------------------------------------------------


def _build_tool_call(
    part: dict[str, Any], function_call: dict[str, Any], blocks: list[Any]
) -> ToolCall:
    global _tool_call_counter
    provided_id = function_call.get("id")
    is_duplicate = any(b.type == "toolCall" and b.id == provided_id for b in blocks)
    if not provided_id or is_duplicate:
        _tool_call_counter += 1
        name = function_call.get("name") or ""
        tool_call_id = f"{name}_{int(time.time() * 1000)}_{_tool_call_counter}"
    else:
        tool_call_id = provided_id

    return ToolCall(
        id=tool_call_id,
        name=function_call.get("name") or "",
        arguments=function_call.get("args") or {},
        thought_signature=part.get("thoughtSignature"),
    )


# ---------------------------------------------------------------------------
# Main stream function (port of google-vertex.ts streamGoogleVertex)
# ---------------------------------------------------------------------------


def stream_google_vertex(
    model: Model,
    context: Context,
    options: GoogleVertexOptions | dict[str, Any] | None = None,
) -> AssistantMessageEventStream:
    """Stream a Vertex AI completion as ``AssistantMessage`` events."""
    stream = create_assistant_message_event_stream()

    async def run() -> None:
        output = AssistantMessage(
            role="assistant",
            content=[],
            api="google-vertex",
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
            timestamp=int(time.time() * 1000),
        )

        try:
            client = _opt(options, "client") or create_client(
                model, options_headers=_opt(options, "headers"), options=options
            )
            params = build_params(model, context, options)
            on_payload = _opt(options, "on_payload")
            if on_payload is not None:
                next_params = on_payload(params, model)
                if hasattr(next_params, "__await__"):
                    next_params = await next_params
                if next_params is not None:
                    params = next_params

            google_stream = client.stream_generate_content(params)

            stream.push(StartEvent(partial=output))
            current_block: TextContent | ThinkingContent | None = None
            blocks = output.content

            def block_index() -> int:
                return len(blocks) - 1

            def close_current(block: TextContent | ThinkingContent) -> None:
                if block.type == "text":
                    stream.push(
                        TextEndEvent(
                            content_index=block_index(),
                            content=block.text,
                            partial=output,
                        )
                    )
                else:
                    stream.push(
                        ThinkingEndEvent(
                            content_index=block_index(),
                            content=block.thinking,
                            partial=output,
                        )
                    )

            async for chunk in google_stream:
                # responseId is output-only; keep the first non-empty one.
                if not output.response_id:
                    output.response_id = chunk.get("responseId")
                candidates = chunk.get("candidates") or []
                candidate = candidates[0] if candidates else None
                parts = ((candidate or {}).get("content") or {}).get("parts") or []

                for part in parts:
                    text = part.get("text")
                    if text is not None:
                        thinking = is_thinking_part(part)
                        needs_new_block = (
                            current_block is None
                            or (thinking and current_block.type != "thinking")
                            or (not thinking and current_block.type != "text")
                        )
                        if needs_new_block:
                            if current_block is not None:
                                close_current(current_block)
                            if thinking:
                                current_block = ThinkingContent(
                                    thinking="", thinking_signature=None
                                )
                                blocks.append(current_block)
                                stream.push(
                                    ThinkingStartEvent(
                                        content_index=block_index(),
                                        partial=output,
                                    )
                                )
                            else:
                                current_block = TextContent(text="")
                                blocks.append(current_block)
                                stream.push(
                                    TextStartEvent(
                                        content_index=block_index(),
                                        partial=output,
                                    )
                                )

                        signature = part.get("thoughtSignature")
                        if current_block is not None and current_block.type == "thinking":
                            current_block.thinking += text
                            current_block.thinking_signature = retain_thought_signature(
                                current_block.thinking_signature, signature
                            )
                            stream.push(
                                ThinkingDeltaEvent(
                                    content_index=block_index(),
                                    delta=text,
                                    partial=output,
                                )
                            )
                        elif current_block is not None:
                            current_block.text += text
                            current_block.text_signature = retain_thought_signature(
                                current_block.text_signature, signature
                            )
                            stream.push(
                                TextDeltaEvent(
                                    content_index=block_index(),
                                    delta=text,
                                    partial=output,
                                )
                            )

                    function_call = part.get("functionCall")
                    if function_call:
                        if current_block is not None:
                            close_current(current_block)
                            current_block = None

                        tool_call = _build_tool_call(part, function_call, blocks)
                        blocks.append(tool_call)
                        stream.push(
                            ToolCallStartEvent(
                                content_index=block_index(),
                                partial=output,
                            )
                        )
                        stream.push(
                            ToolCallDeltaEvent(
                                content_index=block_index(),
                                delta=json.dumps(tool_call.arguments),
                                partial=output,
                            )
                        )
                        stream.push(
                            ToolCallEndEvent(
                                content_index=block_index(),
                                tool_call=tool_call,
                                partial=output,
                            )
                        )

                finish_reason = (candidate or {}).get("finishReason")
                if finish_reason:
                    output.stop_reason = map_stop_reason(finish_reason)
                    if any(b.type == "toolCall" for b in blocks):
                        output.stop_reason = "toolUse"

                usage_metadata = chunk.get("usageMetadata")
                if usage_metadata:
                    cached = usage_metadata.get("cachedContentTokenCount") or 0
                    output.usage = Usage(
                        input=(usage_metadata.get("promptTokenCount") or 0) - cached,
                        output=(usage_metadata.get("candidatesTokenCount") or 0)
                        + (usage_metadata.get("thoughtsTokenCount") or 0),
                        cache_read=cached,
                        cache_write=0,
                        total_tokens=usage_metadata.get("totalTokenCount") or 0,
                        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
                    )
                    output.usage.cost = calculate_cost(model, output.usage)

            if current_block is not None:
                close_current(current_block)

            signal = _opt(options, "signal")
            if getattr(signal, "aborted", False):
                raise RuntimeError("Request was aborted")

            if output.stop_reason in ("aborted", "error"):
                raise RuntimeError("An unknown error occurred")

            stream.push(DoneEvent(reason=output.stop_reason, message=output))
            stream.end(output)
        except Exception as error:  # noqa: BLE001 — mirrors the TS catch-all
            signal = _opt(options, "signal")
            output.stop_reason = "aborted" if getattr(signal, "aborted", False) else "error"
            output.error_message = str(error)
            stream.push(ErrorEvent(reason=output.stop_reason, error=output))
            stream.end(output)

    asyncio.ensure_future(run())
    return stream


# ---------------------------------------------------------------------------
# Simple stream wrapper (port of google-vertex.ts streamSimpleGoogleVertex)
# ---------------------------------------------------------------------------


def _split_simple_options(
    options: SimpleStreamOptions | dict[str, Any] | None,
) -> tuple[SimpleStreamOptions | None, dict[str, Any]]:
    """Split a caller's options into the modelled part and everything else."""
    if options is None:
        return None, {}
    if not isinstance(options, dict):
        return options, {}
    known = set(SimpleStreamOptions.model_fields)
    modelled = {k: v for k, v in options.items() if k in known}
    extras = {k: v for k, v in options.items() if k not in known}
    return SimpleStreamOptions(**modelled), extras


def stream_simple_google_vertex(
    model: Model,
    context: Context,
    options: SimpleStreamOptions | dict[str, Any] | None = None,
) -> AssistantMessageEventStream:
    """``stream_google_vertex`` with reasoning resolved from a thinking level."""
    simple, extras = _split_simple_options(options)
    # For Vertex, we need to check if we have ADC credentials or an API key
    # The env module returns "<authenticated>" when ADC is available
    base = build_base_options(model, simple, None).model_dump()
    # Keys `SimpleStreamOptions` does not model (notably `client`) survive the
    # round-trip, which is what lets tests inject a fake transport.
    base.update(extras)

    reasoning = _opt(options, "reasoning")
    if not reasoning:
        return stream_google_vertex(model, context, {**base, "thinking": {"enabled": False}})

    clamped = clamp_thinking_level(model, reasoning)
    # "off" is impossible here (reasoning was truthy) but the TS maps it to
    # "high" defensively; keep that.
    effort = "high" if clamped == "off" else clamped

    if _is_gemini3_pro(model) or _is_gemini3_flash(model) or _is_gemma4(model):
        return stream_google_vertex(
            model,
            context,
            {**base, "thinking": {"enabled": True, "level": _thinking_level(effort, model)}},
        )

    return stream_google_vertex(
        model,
        context,
        {
            **base,
            "thinking": {
                "enabled": True,
                "budget_tokens": _google_budget(model, effort, _opt(options, "thinking_budgets")),
            },
        },
    )
