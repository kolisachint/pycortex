"""Tests for models module.

Mechanical port of hoocode's models.ts tests (if any).
"""

from cortex.ai.models.models import (
    calculate_cost,
    clamp_thinking_level,
    get_model,
    get_models,
    get_providers,
    get_supported_thinking_levels,
    models_are_equal,
)
from cortex.ai.types import Model, Usage


def _create_model(
    provider: str = "anthropic",
    model_id: str = "claude-opus-4-1",
    reasoning: bool = True,
    thinking_level_map: dict[str, str | None] | None = None,
) -> Model:
    """Create a Model for testing."""
    return Model(
        id=model_id,
        name="Test Model",
        api="anthropic-messages",
        provider=provider,
        base_url="https://api.anthropic.com",
        reasoning=reasoning,
        thinking_level_map=thinking_level_map,
        input=["text", "image"],
        cost={"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
        context_window=200000,
        max_tokens=32000,
    )


def _create_usage(
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 100,
    cache_write: int = 50,
) -> Usage:
    """Create a Usage for testing."""
    return Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=input_tokens + output_tokens + cache_read + cache_write,
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
    )


def test_get_providers_returns_list() -> None:
    """get_providers should return a list of providers."""
    providers = get_providers()
    assert isinstance(providers, list)
    assert "anthropic" in providers


def test_get_model_returns_model() -> None:
    """get_model should return a model if found."""
    model = get_model("anthropic", "claude-opus-4-1")
    assert model is not None
    assert model.id == "claude-opus-4-1"
    assert model.provider == "anthropic"


def test_get_model_returns_none_for_unknown() -> None:
    """get_model should return None for unknown model."""
    model = get_model("anthropic", "nonexistent-model")
    assert model is None


def test_get_models_returns_list() -> None:
    """get_models should return a list of models for a provider."""
    models = get_models("anthropic")
    assert isinstance(models, list)
    assert len(models) > 0


def test_calculate_cost() -> None:
    """calculate_cost should calculate correct costs."""
    model = _create_model()
    usage = _create_usage(input_tokens=1000000, output_tokens=1000000)

    cost = calculate_cost(model, usage)

    # Cost is $/million tokens * tokens / 1000000
    assert cost["input"] == 15.0  # 15 * 1000000 / 1000000
    assert cost["output"] == 75.0  # 75 * 1000000 / 1000000
    expected_total = cost["input"] + cost["output"] + cost["cache_read"] + cost["cache_write"]
    assert cost["total"] == expected_total


def test_get_supported_thinking_levels_off_when_not_reasoning() -> None:
    """get_supported_thinking_levels should return ['off'] when reasoning is False."""
    model = _create_model(reasoning=False)
    levels = get_supported_thinking_levels(model)
    assert levels == ["off"]


def test_get_supported_thinking_levels_with_reasoning() -> None:
    """get_supported_thinking_levels should return multiple levels when reasoning is True."""
    model = _create_model(reasoning=True)
    levels = get_supported_thinking_levels(model)
    assert "off" in levels
    assert "minimal" in levels
    assert "low" in levels
    assert "medium" in levels
    assert "high" in levels


def test_clamp_thinking_level_returns_same_if_supported() -> None:
    """clamp_thinking_level should return the same level if supported."""
    model = _create_model(reasoning=True)
    level = clamp_thinking_level(model, "medium")
    assert level == "medium"


def test_clamp_thinking_level_clamps_to_closest() -> None:
    """clamp_thinking_level should clamp to closest supported level."""
    model = _create_model(
        reasoning=True,
        thinking_level_map={"low": "low", "medium": None, "high": None},
    )
    # "high" is disabled, should clamp to "medium" or "low"
    level = clamp_thinking_level(model, "high")
    assert level in ("medium", "low")


def test_models_are_equal_with_same_models() -> None:
    """models_are_equal should return True for same models."""
    model1 = _create_model(provider="anthropic", model_id="claude-opus-4-1")
    model2 = _create_model(provider="anthropic", model_id="claude-opus-4-1")
    assert models_are_equal(model1, model2) is True


def test_models_are_equal_with_different_models() -> None:
    """models_are_equal should return False for different models."""
    model1 = _create_model(provider="anthropic", model_id="claude-opus-4-1")
    model2 = _create_model(provider="anthropic", model_id="claude-haiku-4-5")
    assert models_are_equal(model1, model2) is False


def test_models_are_equal_with_none() -> None:
    """models_are_equal should return False when either model is None."""
    model = _create_model()
    assert models_are_equal(model, None) is False
    assert models_are_equal(None, model) is False
    assert models_are_equal(None, None) is False
