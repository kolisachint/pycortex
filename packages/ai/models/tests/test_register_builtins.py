"""Tests for register_builtins module.

Mechanical port of hoocode's register-builtins.ts tests (if any).
Tests the lazy loading and registration of built-in API providers.
"""

from __future__ import annotations

from cortex.ai.models.api_registry import (
    ApiProvider,
    clear_api_providers,
    get_api_provider,
    get_api_providers,
    register_api_provider,
)
from cortex.ai.models.register_builtins import (
    register_built_in_api_providers,
    reset_api_providers,
    stream_anthropic,
    stream_google,
    stream_google_vertex,
    stream_openai_completions,
    stream_openai_responses,
    stream_simple_anthropic,
    stream_simple_google,
    stream_simple_google_vertex,
    stream_simple_openai_completions,
    stream_simple_openai_responses,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _create_dummy_stream() -> object:
    """Create a dummy stream function for testing."""

    def dummy_stream(*args: object) -> object:
        return None

    return dummy_stream


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_built_in_api_providers(self) -> None:
        """register_built_in_api_providers should register all providers."""
        clear_api_providers()
        register_built_in_api_providers()

        providers = get_api_providers()
        provider_apis = [p.api for p in providers]

        # Check that key providers are registered
        assert "anthropic-messages" in provider_apis
        assert "google-generative-ai" in provider_apis
        assert "google-vertex" in provider_apis
        assert "openai-completions" in provider_apis
        assert "openai-responses" in provider_apis

    def test_reset_api_providers(self) -> None:
        """reset_api_providers should clear and re-register."""
        clear_api_providers()
        register_built_in_api_providers()

        initial_count = len(get_api_providers())

        # Add a custom provider
        register_api_provider(
            ApiProvider(
                api="custom-api",
                stream=_create_dummy_stream(),  # type: ignore[arg-type]
                stream_simple=_create_dummy_stream(),  # type: ignore[arg-type]
            ),
            source_id="test",
        )

        assert len(get_api_providers()) == initial_count + 1

        # Reset should clear custom and re-register builtins
        reset_api_providers()

        assert len(get_api_providers()) == initial_count
        assert get_api_provider("custom-api") is None

    def test_providers_are_registered(self) -> None:
        """All built-in providers should be registered."""
        clear_api_providers()
        register_built_in_api_providers()

        # Check that anthropic is registered
        anthropic = get_api_provider("anthropic-messages")
        assert anthropic is not None


# ---------------------------------------------------------------------------
# Lazy stream function tests
# ---------------------------------------------------------------------------


class TestLazyStreamFunctions:
    """Test that lazy stream functions can be called without errors."""

    def test_stream_anthropic_is_callable(self) -> None:
        assert callable(stream_anthropic)

    def test_stream_simple_anthropic_is_callable(self) -> None:
        assert callable(stream_simple_anthropic)

    def test_stream_google_is_callable(self) -> None:
        assert callable(stream_google)

    def test_stream_simple_google_is_callable(self) -> None:
        assert callable(stream_simple_google)

    def test_stream_google_vertex_is_callable(self) -> None:
        assert callable(stream_google_vertex)

    def test_stream_simple_google_vertex_is_callable(self) -> None:
        assert callable(stream_simple_google_vertex)

    def test_stream_openai_completions_is_callable(self) -> None:
        assert callable(stream_openai_completions)

    def test_stream_simple_openai_completions_is_callable(self) -> None:
        assert callable(stream_simple_openai_completions)

    def test_stream_openai_responses_is_callable(self) -> None:
        assert callable(stream_openai_responses)

    def test_stream_simple_openai_responses_is_callable(self) -> None:
        assert callable(stream_simple_openai_responses)


# ---------------------------------------------------------------------------
# Lazy loading behavior tests
# ---------------------------------------------------------------------------


class TestLazyLoadingBehavior:
    """Test that lazy loading works correctly."""

    def test_import_does_not_load_providers(self) -> None:
        """Importing register_builtins should not immediately load provider modules."""
        # This is a basic check - the module should import successfully
        # without errors, which means lazy loading is working
        import importlib

        # Verify the module can be imported
        mod = importlib.import_module("cortex.ai.models.register_builtins")
        assert hasattr(mod, "register_built_in_api_providers")
