"""OpenRouter images provider.

Mechanical port of hoocode's ``packages/ai/src/providers/images/openrouter.ts``.

NOTE: This is a simplified implementation. The TS version uses the OpenAI SDK
which we're not using in this port.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from cortex.ai.env import get_env_api_key
from cortex.ai.images.types import (
    AssistantImages,
    ImageContent,
    ImagesContext,
    ImagesModel,
    ImagesOptions,
    ImagesUsage,
    TextContent,
)

__all__ = [
    "generate_images_openrouter",
]


async def generate_images_openrouter(
    model: ImagesModel,
    context: ImagesContext,
    options: ImagesOptions | None = None,
) -> AssistantImages:
    """Generate images using OpenRouter API."""
    output = AssistantImages(
        api=model.api,
        provider=model.provider,
        model=model.id,
        output=[],
        stop_reason="stop",
        timestamp=int(time.time() * 1000),
    )

    try:
        api_key = (options.api_key if options else None) or get_env_api_key(model.provider)
        if not api_key:
            raise ValueError(f"No API key available for provider: {model.provider}")

        params = _build_params(model, context)

        # Call OpenRouter API
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **(model.headers or {}),
                **((options.headers if options else None) or {}),
            }

            response = await client.post(
                f"{model.base_url}/chat/completions",
                json=params,
                headers=headers,
            )

            if response.status_code >= 400:
                raise RuntimeError(f"OpenRouter API error {response.status_code}: {response.text}")

            image_response = response.json()
            output.response_id = image_response.get("id")

            # Parse usage
            if "usage" in image_response:
                output.usage = _parse_usage(image_response["usage"], model)

            # Parse choices
            choices = image_response.get("choices", [])
            if choices:
                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content")

                if isinstance(content, str) and content:
                    output.output.append(TextContent(text=content))

                # Parse images from content (base64 data URLs)
                if isinstance(content, str):
                    matches = re.findall(
                        r"data:([^;]+);base64,([A-Za-z0-9+/=]+)",
                        content,
                    )
                    for mime_type, data in matches:
                        output.output.append(ImageContent(mime_type=mime_type, data=data))

        return output

    except Exception as error:
        output.stop_reason = "aborted" if (options and options.signal) else "error"
        output.error_message = str(error)
        return output


def _build_params(model: ImagesModel, context: ImagesContext) -> dict[str, Any]:
    """Build the request parameters."""
    content: list[dict[str, Any]] = []

    for item in context.input:
        if isinstance(item, TextContent):
            content.append({"type": "text", "text": item.text})
        elif type(item).__name__ == "ImageContent":
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{item.mime_type};base64,{item.data}",
                    },
                }
            )

    # Determine modalities based on model output
    modalities = ["image", "text"] if "text" in model.output else ["image"]

    return {
        "model": model.id,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "modalities": modalities,
    }


def _parse_usage(raw_usage: dict[str, Any], model: ImagesModel) -> ImagesUsage:
    """Parse usage from API response."""
    prompt_tokens = raw_usage.get("prompt_tokens", 0) or 0
    completion_tokens = raw_usage.get("completion_tokens", 0) or 0

    prompt_details = raw_usage.get("prompt_tokens_details", {})
    reported_cached = prompt_details.get("cached_tokens", 0) or 0
    cache_write = prompt_details.get("cache_write_tokens", 0) or 0

    # Calculate actual cache read
    cache_read = max(0, reported_cached - cache_write) if cache_write > 0 else reported_cached

    input_tokens = max(0, prompt_tokens - cache_read - cache_write)
    output_tokens = completion_tokens
    total = input_tokens + output_tokens + cache_read + cache_write

    cost_input = (model.cost.get("input", 0) / 1_000_000) * input_tokens
    cost_output = (model.cost.get("output", 0) / 1_000_000) * output_tokens
    cost_cache_read = (model.cost.get("cacheRead", 0) / 1_000_000) * cache_read
    cost_cache_write = (model.cost.get("cacheWrite", 0) / 1_000_000) * cache_write
    cost_total = cost_input + cost_output + cost_cache_read + cost_cache_write

    return ImagesUsage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=total,
        cost={
            "input": cost_input,
            "output": cost_output,
            "cacheRead": cost_cache_read,
            "cacheWrite": cost_cache_write,
            "total": cost_total,
        },
    )
