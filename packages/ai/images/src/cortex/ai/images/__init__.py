"""Image generation module.

Mechanical port of hoocode's ``packages/ai/src/images.ts`` and related files.
"""

from __future__ import annotations

from cortex.ai.images.api_registry import (
    ImagesApiProvider,
    get_images_api_provider,
    register_images_api_provider,
)
from cortex.ai.images.types import (
    AssistantImages,
    ImageContent,
    ImagesApi,
    ImagesContext,
    ImagesModel,
    ImagesOptions,
    ImagesUsage,
    TextContent,
)

__all__ = [
    # Registry
    "ImagesApiProvider",
    "get_images_api_provider",
    "register_images_api_provider",
    # Main function
    "generate_images",
    # Types
    "AssistantImages",
    "ImageContent",
    "ImagesApi",
    "ImagesContext",
    "ImagesModel",
    "ImagesOptions",
    "ImagesUsage",
    "TextContent",
]


def _resolve_images_api_provider(api: str) -> ImagesApiProvider:
    """Resolve an images API provider by api name."""
    provider = get_images_api_provider(api)
    if not provider:
        raise ValueError(f"No API provider registered for api: {api}")
    return provider


async def generate_images(
    model: ImagesModel,
    context: ImagesContext,
    options: ImagesOptions | None = None,
) -> AssistantImages:
    """Generate images using the registered provider."""
    provider = _resolve_images_api_provider(model.api)
    return await provider.generate_images(model, context, options)
