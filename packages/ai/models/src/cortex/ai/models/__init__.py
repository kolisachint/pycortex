"""AI models package — port of hoocode's packages/ai/src/*.

This package provides:
- Model registry and helper functions
- Image model registry
- API provider registry
"""

from cortex.ai.models.api_registry import (
    ApiProvider,
    clear_api_providers,
    get_api_provider,
    get_api_providers,
    register_api_provider,
    unregister_api_providers,
)
from cortex.ai.models.image_models import (
    get_image_model,
    get_image_models,
    get_image_providers,
)
from cortex.ai.models.models import (
    calculate_cost,
    clamp_thinking_level,
    get_model,
    get_models,
    get_providers,
    get_supported_thinking_levels,
    models_are_equal,
)

__all__ = [
    # API Registry
    "ApiProvider",
    "clear_api_providers",
    "get_api_provider",
    "get_api_providers",
    "register_api_provider",
    "unregister_api_providers",
    # Image Models
    "get_image_model",
    "get_image_models",
    "get_image_providers",
    # Models
    "calculate_cost",
    "clamp_thinking_level",
    "get_model",
    "get_models",
    "get_providers",
    "get_supported_thinking_levels",
    "models_are_equal",
]
