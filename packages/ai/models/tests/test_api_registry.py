"""Tests for api_registry module.

Mechanical port of hoocode's api-registry.ts tests (if any).
"""

from cortex.ai.models.api_registry import (
    ApiProvider,
    StreamFunction,
    clear_api_providers,
    get_api_provider,
    get_api_providers,
    register_api_provider,
    unregister_api_providers,
)


def _create_stream_function() -> StreamFunction:
    """Create a mock stream function for testing."""

    def stream(model: object, context: object, options: object = None) -> object:
        raise NotImplementedError("Mock stream function")

    return stream  # type: ignore


def test_register_and_get_api_provider() -> None:
    """register_api_provider should register a provider that can be retrieved."""
    clear_api_providers()  # Start clean

    provider = ApiProvider(
        api="anthropic-messages",
        stream=_create_stream_function(),
        stream_simple=_create_stream_function(),
    )
    register_api_provider(provider, source_id="test")

    retrieved = get_api_provider("anthropic-messages")
    assert retrieved is not None
    assert retrieved.api == "anthropic-messages"


def test_get_api_provider_returns_none_for_unknown() -> None:
    """get_api_provider should return None for unknown api."""
    retrieved = get_api_provider("unknown-api")
    assert retrieved is None


def test_get_api_providers_returns_list() -> None:
    """get_api_providers should return a list of providers."""
    clear_api_providers()  # Start clean

    provider = ApiProvider(
        api="anthropic-messages",
        stream=_create_stream_function(),
        stream_simple=_create_stream_function(),
    )
    register_api_provider(provider, source_id="test")

    providers = get_api_providers()
    assert isinstance(providers, list)
    assert len(providers) > 0


def test_unregister_api_providers() -> None:
    """unregister_api_providers should remove providers with matching source_id."""
    clear_api_providers()  # Start clean

    provider1 = ApiProvider(
        api="anthropic-messages",
        stream=_create_stream_function(),
        stream_simple=_create_stream_function(),
    )
    register_api_provider(provider1, source_id="source1")

    provider2 = ApiProvider(
        api="openai-completions",
        stream=_create_stream_function(),
        stream_simple=_create_stream_function(),
    )
    register_api_provider(provider2, source_id="source2")

    unregister_api_providers("source1")

    assert get_api_provider("anthropic-messages") is None
    assert get_api_provider("openai-completions") is not None


def test_clear_api_providers() -> None:
    """clear_api_providers should remove all providers."""
    clear_api_providers()  # Start clean

    provider = ApiProvider(
        api="anthropic-messages",
        stream=_create_stream_function(),
        stream_simple=_create_stream_function(),
    )
    register_api_provider(provider, source_id="test")

    clear_api_providers()

    providers = get_api_providers()
    assert len(providers) == 0
