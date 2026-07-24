"""Tests for image_models module.

Mechanical port of hoocode's image-models.ts tests (if any).
"""

from cortex.ai.models.image_models import (
    get_image_model,
    get_image_models,
    get_image_providers,
)


def test_get_image_providers_returns_list() -> None:
    """get_image_providers should return a list of providers."""
    providers = get_image_providers()
    assert isinstance(providers, list)
    assert "openrouter" in providers


def test_get_image_model_returns_model() -> None:
    """get_image_model should return a model if found."""
    model = get_image_model("openrouter", "openai/gpt-5-image")
    assert model is not None
    assert model.id == "openai/gpt-5-image"
    assert model.provider == "openrouter"


def test_get_image_model_returns_none_for_unknown() -> None:
    """get_image_model should return None for unknown model."""
    model = get_image_model("openrouter", "nonexistent-model")
    assert model is None


def test_get_image_models_returns_list() -> None:
    """get_image_models should return a list of models for a provider."""
    models = get_image_models("openrouter")
    assert isinstance(models, list)
    assert len(models) > 0
