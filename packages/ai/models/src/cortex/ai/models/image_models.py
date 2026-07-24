"""Image model registry and helper functions.

Mechanical port of hoocode's `packages/ai/src/image-models.ts`.
"""

from __future__ import annotations

from cortex.ai.models.image_models_generated import IMAGE_MODELS
from cortex.ai.types import ImagesModel

# Initialize registry from IMAGE_MODELS on module load
_image_model_registry: dict[str, dict[str, ImagesModel]] = {}

for provider, models in IMAGE_MODELS.items():
    provider_models: dict[str, ImagesModel] = {}
    for model_id, model in models.items():
        provider_models[model_id] = ImagesModel(**model)
    _image_model_registry[provider] = provider_models


def get_image_model(provider: str, model_id: str) -> ImagesModel | None:
    """Get an image model by provider and model ID.

    Args:
        provider: The provider name.
        model_id: The model ID.

    Returns:
        The image model if found, None otherwise.
    """
    provider_models = _image_model_registry.get(provider)
    if provider_models is None:
        return None
    return provider_models.get(model_id)


def get_image_providers() -> list[str]:
    """Get all registered image provider names.

    Returns:
        List of provider names.
    """
    return list(_image_model_registry.keys())


def get_image_models(provider: str) -> list[ImagesModel]:
    """Get all image models for a provider.

    Args:
        provider: The provider name.

    Returns:
        List of image models for the provider.
    """
    provider_models = _image_model_registry.get(provider)
    if provider_models is None:
        return []
    return list(provider_models.values())
