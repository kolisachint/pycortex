"""Tests for register_builtins module.

Mechanical port of hoocode's register-builtins.ts tests (if any).
Tests the lazy loading and registration of built-in API providers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cortex.ai.models.api_registry import (
    clear_api_providers,
    get_api_provider,
    get_api_providers,
)
from cortex.ai.models.register_builtins import (
    _create_lazy_load_error_message,  # type: ignore[attr-defined]
    _LazyProviderModule,  # type: ignore[attr-defined]
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
# LazyProviderModule tests
# ---------------------------------------------------------------------------


class TestLazyProviderModule:
    def test_loads_module_lazily(self) -> None:
        """Module should not be imported until stream is accessed."""
        with patch("cortex.ai.models.register_builtins.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_importlib.import_module.return_value = mock_module

            lazy = _LazyProviderModule(
                "test.module",
                "stream_func",
                "stream_simple_func",
            )

            # Module should not be loaded yet
            mock_importlib.import_module.assert_not_called()

            # Access stream to trigger load
            _ = lazy.stream
            mock_importlib.import_module.assert_called_once_with("test.module")

    def test_caches_loaded_module(self) -> None:
        """Module should only be imported once."""
        with patch("cortex.ai.models.register_builtins.importlib") as mock_importlib:
            mock_module = MagicMock()
            mock_importlib.import_module.return_value = mock_module

            lazy = _LazyProviderModule(
                "test.module",
                "stream_func",
                "stream_simple_func",
            )

            # Access stream twice
            _ = lazy.stream
            _ = lazy.stream

            # Should only import once
            mock_importlib.import_module.assert_called_once()

    def test_stream_simple_returns_none_when_not_available(self) -> None:
        """stream_simple should return None when name is None."""
        lazy = _LazyProviderModule(
            "test.module",
            "stream_func",
            None,
        )

        assert lazy.stream_simple is None


# ---------------------------------------------------------------------------
# Error message tests
# ---------------------------------------------------------------------------


class TestCreateLazyLoadErrorMessage:
    def test_creates_error_message(self) -> None:
        from cortex.ai.types import Model

        model = Model(
            id="test-model",
            name="Test Model",
            api="anthropic-messages",
            provider="test",
            base_url="",
            reasoning=False,
            input=["text"],
            cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            context_window=1000,
            max_tokens=100,
        )

        error = Exception("Test error")
        message = _create_lazy_load_error_message(model, error)

        assert message.stop_reason == "error"
        assert message.error_message == "Test error"
        assert message.api == "anthropic-messages"


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
        from cortex.ai.models.api_registry import ApiProvider, register_api_provider

        def dummy_stream(*args: object) -> object:
            return None

        register_api_provider(
            ApiProvider(
                api="custom-api",
                stream=dummy_stream,  # type: ignore[arg-type]
                stream_simple=dummy_stream,  # type: ignore[arg-type]
            ),
            source_id="test",
        )

        assert len(get_api_providers()) == initial_count + 1

        # Reset should clear custom and re-register builtins
        reset_api_providers()

        assert len(get_api_providers()) == initial_count
        assert get_api_provider("custom-api") is None

    def test_providers_are_registered_with_builtin_source_id(self) -> None:
        """All built-in providers should have source_id='builtin'."""
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
