"""Model registry and helper functions.

Mechanical port of hoocode's `packages/ai/src/models.ts`.
"""

from __future__ import annotations

from typing import Any

from cortex.ai.models.models_generated import MODELS
from cortex.ai.types import (
    Model,
    ModelThinkingLevel,
    Usage,
)


def _convert_camel_to_snake(model_data: dict[str, Any]) -> dict[str, Any]:
    """Convert camelCase keys to snake_case for Model creation."""
    converted = {}
    for key, value in model_data.items():
        if key == "baseUrl":
            converted["base_url"] = value
        elif key == "contextWindow":
            converted["context_window"] = value
        elif key == "maxTokens":
            converted["max_tokens"] = value
        elif key == "thinkingLevelMap":
            converted["thinking_level_map"] = value
        elif key == "cacheRead":
            # Handle cost dict nested keys
            if isinstance(value, dict):
                cost_converted = {}
                for k, v in value.items():
                    if k == "cacheRead":
                        cost_converted["cache_read"] = v
                    elif k == "cacheWrite":
                        cost_converted["cache_write"] = v
                    else:
                        cost_converted[k] = v
                converted[key] = cost_converted
            else:
                converted[key] = value
        else:
            converted[key] = value
    return converted


# Initialize registry from MODELS on module load
_model_registry: dict[str, dict[str, Model]] = {}

for provider, models in MODELS.items():
    provider_models: dict[str, Model] = {}
    for model_id, model_data in models.items():
        converted = _convert_camel_to_snake(model_data)
        provider_models[model_id] = Model(**converted)
    _model_registry[provider] = provider_models


def get_model(provider: str, model_id: str) -> Model | None:
    """Get a model by provider and model ID.

    Args:
        provider: The provider name.
        model_id: The model ID.

    Returns:
        The model if found, None otherwise.
    """
    provider_models = _model_registry.get(provider)
    if provider_models is None:
        return None
    return provider_models.get(model_id)


def get_providers() -> list[str]:
    """Get all registered provider names.

    Returns:
        List of provider names.
    """
    return list(_model_registry.keys())


def get_models(provider: str) -> list[Model]:
    """Get all models for a provider.

    Args:
        provider: The provider name.

    Returns:
        List of models for the provider.
    """
    provider_models = _model_registry.get(provider)
    if provider_models is None:
        return []
    return list(provider_models.values())


def calculate_cost(model: Model, usage: Usage) -> dict[str, float]:
    """Calculate the cost of a model usage.

    Args:
        model: The model to calculate cost for.
        usage: The usage to calculate cost for.

    Returns:
        The cost breakdown.
    """
    cost = {
        "input": (model.cost["input"] / 1_000_000) * usage.input,
        "output": (model.cost["output"] / 1_000_000) * usage.output,
        "cache_read": (model.cost["cache_read"] / 1_000_000) * usage.cache_read,
        "cache_write": (model.cost["cache_write"] / 1_000_000) * usage.cache_write,
    }
    cost["total"] = cost["input"] + cost["output"] + cost["cache_read"] + cost["cache_write"]
    return cost


EXTENDED_THINKING_LEVELS: list[ModelThinkingLevel] = [
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
]


def get_supported_thinking_levels(model: Model) -> list[ModelThinkingLevel]:
    """Get the supported thinking levels for a model.

    Args:
        model: The model to check.

    Returns:
        List of supported thinking levels.
    """
    if not model.reasoning:
        return ["off"]

    result: list[ModelThinkingLevel] = []
    for level in EXTENDED_THINKING_LEVELS:
        if model.thinking_level_map is not None and level in model.thinking_level_map:
            # Level is explicitly set in the map
            mapped = model.thinking_level_map[level]
            if mapped is not None and mapped != "":
                # Explicitly enabled
                result.append(level)
            # If mapped is None, level is disabled - skip
        elif level in ("off", "minimal", "low", "medium", "high"):
            # Default levels are supported if not in map
            result.append(level)
        # "xhigh" is not supported by default - only if explicitly enabled

    return result


def clamp_thinking_level(model: Model, level: ModelThinkingLevel) -> ModelThinkingLevel:
    """Clamp a thinking level to the closest supported level.

    Args:
        model: The model to check.
        level: The requested thinking level.

    Returns:
        The clamped thinking level.
    """
    available_levels = get_supported_thinking_levels(model)
    if level in available_levels:
        return level

    if level in EXTENDED_THINKING_LEVELS:
        requested_index = EXTENDED_THINKING_LEVELS.index(level)
    else:
        requested_index = -1
    if requested_index == -1:
        return available_levels[0] if available_levels else "off"

    # Try levels at or above the requested level
    for i in range(requested_index, len(EXTENDED_THINKING_LEVELS)):
        candidate = EXTENDED_THINKING_LEVELS[i]
        if candidate in available_levels:
            return candidate

    # Try levels below the requested level
    for i in range(requested_index - 1, -1, -1):
        candidate = EXTENDED_THINKING_LEVELS[i]
        if candidate in available_levels:
            return candidate

    return available_levels[0] if available_levels else "off"


def models_are_equal(a: Model | None, b: Model | None) -> bool:
    """Check if two models are equal by comparing id and provider.

    Args:
        a: First model.
        b: Second model.

    Returns:
        True if models are equal, False otherwise.
    """
    if a is None or b is None:
        return False
    return a.id == b.id and a.provider == b.provider
