"""Lazy registration of built-in images API providers.

Mechanical port of hoocode's ``packages/ai/src/providers/images/register-builtins.ts``.
"""

from __future__ import annotations

from typing import Any

from cortex.ai.images.api_registry import (
    ImagesApiProvider,
    register_images_api_provider,
)
from cortex.ai.images.types import AssistantImages, ImagesModel

__all__ = [
    "register_built_in_images_api_providers",
    "generate_images_openrouter",
]


def _create_lazy_load_error_images(model: ImagesModel, error: Exception) -> AssistantImages:
    """Create an error AssistantImages for lazy load failures."""
    return AssistantImages(
        api=model.api,
        provider=model.provider,
        model=model.id,
        output=[],
        stop_reason="error",
        error_message=str(error),
    )


async def generate_images_openrouter(
    model: ImagesModel,
    context: Any,
    options: Any = None,
) -> AssistantImages:
    """Lazy-loaded OpenRouter images generator."""
    try:
        from cortex.ai.images.openrouter import (
            generate_images_openrouter as _generate,
        )

        return await _generate(model, context, options)
    except Exception as error:
        return _create_lazy_load_error_images(model, error)


def register_built_in_images_api_providers() -> None:
    """Register all built-in images API providers."""
    register_images_api_provider(
        ImagesApiProvider(
            api="openrouter-images",
            generate_images=generate_images_openrouter,
        ),
        source_id="builtin",
    )


# Auto-register on import
register_built_in_images_api_providers()
