"""API provider registry.

Mechanical port of hoocode's `packages/ai/src/api-registry.ts`.
"""

from __future__ import annotations

from typing import Any

from cortex.ai.types import Api, Context, Model


class StreamFunction:
    """Callable that produces an event stream."""

    def __call__(
        self,
        model: Model,
        context: Context,
        options: dict[str, Any] | None = None,
    ) -> Any: ...


class ApiProvider:
    """Registered API provider with stream and streamSimple functions."""

    def __init__(
        self,
        api: Api,
        stream: StreamFunction,
        stream_simple: StreamFunction,
    ) -> None:
        self.api = api
        self.stream = stream
        self.stream_simple = stream_simple


class RegisteredApiProvider:
    """Internal registry entry."""

    def __init__(
        self,
        provider: ApiProvider,
        source_id: str | None = None,
    ) -> None:
        self.provider = provider
        self.source_id = source_id


# Global registry
_api_provider_registry: dict[str, RegisteredApiProvider] = {}


def _wrap_stream(api: Api, stream: StreamFunction) -> StreamFunction:
    """Wrap a stream function to validate api matches."""

    def wrapped(
        model: Model,
        context: Context,
        options: dict[str, Any] | None = None,
    ) -> Any:
        if model.api != api:
            raise ValueError(f"Mismatched api: {model.api} expected {api}")
        return stream(model, context, options)

    return wrapped  # type: ignore


def _wrap_stream_simple(api: Api, stream_simple: StreamFunction) -> StreamFunction:
    """Wrap a streamSimple function to validate api matches."""

    def wrapped(
        model: Model,
        context: Context,
        options: dict[str, Any] | None = None,
    ) -> Any:
        if model.api != api:
            raise ValueError(f"Mismatched api: {model.api} expected {api}")
        return stream_simple(model, context, options)

    return wrapped  # type: ignore


def register_api_provider(
    provider: ApiProvider,
    source_id: str | None = None,
) -> None:
    """Register an API provider."""
    wrapped = ApiProvider(
        api=provider.api,
        stream=_wrap_stream(provider.api, provider.stream),
        stream_simple=_wrap_stream_simple(provider.api, provider.stream_simple),
    )
    _api_provider_registry[provider.api] = RegisteredApiProvider(
        provider=wrapped,
        source_id=source_id,
    )


def get_api_provider(api: str) -> ApiProvider | None:
    """Get a registered API provider by api name."""
    entry = _api_provider_registry.get(api)
    return entry.provider if entry else None


def get_api_providers() -> list[ApiProvider]:
    """Get all registered API providers."""
    return [entry.provider for entry in _api_provider_registry.values()]


def unregister_api_providers(source_id: str) -> None:
    """Unregister all API providers from a specific source."""
    to_remove = [
        api for api, entry in _api_provider_registry.items() if entry.source_id == source_id
    ]
    for api in to_remove:
        del _api_provider_registry[api]


def clear_api_providers() -> None:
    """Clear all registered API providers."""
    _api_provider_registry.clear()
