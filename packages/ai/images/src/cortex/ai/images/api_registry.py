"""Images API provider registry.

Mechanical port of hoocode's ``packages/ai/src/images-api-registry.ts``.
"""

from __future__ import annotations

from typing import Any

from cortex.ai.images.types import ImagesApi, ImagesFunction, ImagesModel

__all__ = [
    "ImagesApiProvider",
    "get_images_api_provider",
    "register_images_api_provider",
]


class ImagesApiProvider:
    """Registered images API provider."""

    def __init__(
        self,
        api: ImagesApi,
        generate_images: ImagesFunction,
    ) -> None:
        self.api = api
        self.generate_images = generate_images


class _RegisteredImagesApiProvider:
    """Internal registry entry."""

    def __init__(
        self,
        provider: ImagesApiProvider,
        source_id: str | None = None,
    ) -> None:
        self.provider = provider
        self.source_id = source_id


# Global registry
_images_api_provider_registry: dict[str, _RegisteredImagesApiProvider] = {}


def _wrap_generate_images(api: ImagesApi, generate_images: ImagesFunction) -> ImagesFunction:
    """Wrap a generate_images function to validate api matches."""

    def wrapped(
        model: ImagesModel,
        context: Any,
        options: Any = None,
    ) -> Any:
        if model.api != api:
            raise ValueError(f"Mismatched api: {model.api} expected {api}")
        return generate_images(model, context, options)

    return wrapped  # type: ignore


def register_images_api_provider(
    provider: ImagesApiProvider,
    source_id: str | None = None,
) -> None:
    """Register an images API provider."""
    wrapped = ImagesApiProvider(
        api=provider.api,  # type: ignore[arg-type]
        generate_images=_wrap_generate_images(provider.api, provider.generate_images),  # type: ignore[arg-type]
    )
    _images_api_provider_registry[provider.api] = _RegisteredImagesApiProvider(
        provider=wrapped,
        source_id=source_id,
    )


def get_images_api_provider(api: str) -> ImagesApiProvider | None:
    """Get a registered images API provider by api name."""
    entry = _images_api_provider_registry.get(api)
    return entry.provider if entry else None


def clear_images_api_providers() -> None:
    """Clear all images API providers."""
    _images_api_provider_registry.clear()
