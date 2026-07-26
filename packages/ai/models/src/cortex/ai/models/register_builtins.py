"""Lazy registration of built-in API providers.

Mechanical port of hoocode's ``packages/ai/src/providers/register-builtins.ts``.

The TS side uses dynamic ``import()`` to lazily load provider modules. In Python,
we use ``importlib.import_module()`` for the same effect — provider modules are
only imported when first used, keeping startup time fast and avoiding import
cycles.

Each provider exposes a ``stream`` and ``stream_simple`` function. The lazy
wrappers forward calls to the real functions after loading the module once.
"""

from __future__ import annotations

import importlib
import time
from typing import Any

from cortex.ai.models.api_registry import (
    ApiProvider,
    StreamFunction,
    clear_api_providers,
    register_api_provider,
)
from cortex.ai.types import (
    AssistantMessage,
    Context,
    ErrorEvent,
    Model,
    Usage,
)

__all__ = [
    "register_built_in_api_providers",
    "reset_api_providers",
    "stream_anthropic",
    "stream_azure_openai_responses",
    "stream_google",
    "stream_google_vertex",
    "stream_openai_codex_responses",
    "stream_openai_completions",
    "stream_openai_responses",
    "stream_simple_anthropic",
    "stream_simple_azure_openai_responses",
    "stream_simple_google",
    "stream_simple_google_vertex",
    "stream_simple_openai_codex_responses",
    "stream_simple_openai_completions",
    "stream_simple_openai_responses",
]


# ---------------------------------------------------------------------------
# Lazy module loading infrastructure
# ---------------------------------------------------------------------------


class _LazyProviderModule:
    """Wrapper that lazily loads a provider module on first access."""

    def __init__(
        self,
        module_path: str,
        stream_name: str,
        stream_simple_name: str | None = None,
    ) -> None:
        self._module_path = module_path
        self._stream_name = stream_name
        self._stream_simple_name = stream_simple_name
        self._module: Any = None

    def _load(self) -> None:
        if self._module is None:
            self._module = importlib.import_module(self._module_path)

    @property
    def stream(self) -> StreamFunction:
        self._load()
        return getattr(self._module, self._stream_name)

    @property
    def stream_simple(self) -> StreamFunction | None:
        if self._stream_simple_name is None:
            return None
        self._load()
        return getattr(self._module, self._stream_simple_name, None)


# Module path → lazy loader cache
_lazy_modules: dict[str, _LazyProviderModule] = {}


def _get_lazy_module(
    module_path: str, stream_name: str, stream_simple_name: str | None = None
) -> _LazyProviderModule:
    """Get or create a lazy module loader."""
    if module_path not in _lazy_modules:
        _lazy_modules[module_path] = _LazyProviderModule(
            module_path, stream_name, stream_simple_name
        )
    return _lazy_modules[module_path]


# ---------------------------------------------------------------------------
# Lazy load error handling (port of createLazyLoadErrorMessage)
# ---------------------------------------------------------------------------


def _create_lazy_load_error_message(model: Model, error: Exception) -> AssistantMessage:
    """Create an error AssistantMessage for lazy load failures."""
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(
            input=0,
            output=0,
            cache_read=0,
            cache_write=0,
            total_tokens=0,
            cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        ),
        stop_reason="error",
        error_message=str(error),
        timestamp=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# Forward stream (port of forwardStream)
# ---------------------------------------------------------------------------


async def _forward_stream(target: Any, source: Any) -> None:
    """Forward events from source to target stream."""
    async for event in source:
        target.push(event)
    target.end()


# ---------------------------------------------------------------------------
# Lazy stream factories (port of createLazyStream / createLazySimpleStream)
# ---------------------------------------------------------------------------


def _create_lazy_stream(lazy_module: _LazyProviderModule) -> StreamFunction:
    """Create a lazy stream function that loads the module on first use."""

    def lazy_stream(model: Model, context: Context, options: Any = None) -> Any:  # type: ignore[override]
        from cortex.ai.stream import create_assistant_message_event_stream

        outer = create_assistant_message_event_stream()

        async def run() -> None:
            try:
                inner = lazy_module.stream(model, context, options)
                await _forward_stream(outer, inner)
            except Exception as error:
                message = _create_lazy_load_error_message(model, error)
                outer.push(
                    ErrorEvent(
                        reason="error",
                        error=message,
                    )
                )
                outer.end(message)

        import asyncio

        asyncio.ensure_future(run())
        return outer

    return lazy_stream  # type: ignore[return-value]


def _create_lazy_simple_stream(lazy_module: _LazyProviderModule) -> StreamFunction:
    """Create a lazy simple stream function that loads the module on first use."""

    def lazy_stream_simple(model: Model, context: Context, options: Any = None) -> Any:  # type: ignore[override]
        from cortex.ai.stream import create_assistant_message_event_stream

        outer = create_assistant_message_event_stream()

        async def run() -> None:
            try:
                # If stream_simple is not available, fall back to stream
                if lazy_module.stream_simple:
                    func = lazy_module.stream_simple
                else:
                    func = lazy_module.stream
                inner = func(model, context, options)
                await _forward_stream(outer, inner)
            except Exception as error:
                message = _create_lazy_load_error_message(model, error)
                outer.push(
                    ErrorEvent(
                        reason="error",
                        error=message,
                    )
                )
                outer.end(message)

        import asyncio

        asyncio.ensure_future(run())
        return outer

    return lazy_stream_simple  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Provider-specific lazy loaders
# ---------------------------------------------------------------------------


def _load_anthropic_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.anthropic",
        "stream_anthropic",
        "stream_simple_anthropic",
    )


def _load_azure_openai_responses_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.openai.azure_openai_responses",
        "stream_azure_openai_responses",
        None,  # stream_simple not yet implemented
    )


def _load_google_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.google",
        "stream_google",
        "stream_simple_google",
    )


def _load_google_vertex_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.google",
        "stream_google_vertex",
        "stream_simple_google_vertex",
    )


def _load_openai_codex_responses_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.openai.openai_codex_responses",
        "stream_openai_codex_responses",
        None,  # stream_simple not yet implemented
    )


def _load_openai_completions_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.openai.openai_completions",
        "stream_openai_completions",
        "stream_simple_openai_completions",
    )


def _load_openai_responses_module() -> _LazyProviderModule:
    return _get_lazy_module(
        "cortex.ai.providers.openai.openai_responses",
        "stream_openai_responses",
        "stream_simple_openai_responses",
    )


# ---------------------------------------------------------------------------
# Public lazy stream functions (port of exported stream* / streamSimple*)
# ---------------------------------------------------------------------------


# Anthropic
stream_anthropic = _create_lazy_stream(_load_anthropic_module())
stream_simple_anthropic = _create_lazy_simple_stream(_load_anthropic_module())

# Azure OpenAI Responses
stream_azure_openai_responses = _create_lazy_stream(_load_azure_openai_responses_module())
stream_simple_azure_openai_responses = _create_lazy_simple_stream(
    _load_azure_openai_responses_module()
)

# Google Generative AI
stream_google = _create_lazy_stream(_load_google_module())
stream_simple_google = _create_lazy_simple_stream(_load_google_module())

# Google Vertex
stream_google_vertex = _create_lazy_stream(_load_google_vertex_module())
stream_simple_google_vertex = _create_lazy_simple_stream(_load_google_vertex_module())

# OpenAI Codex Responses
stream_openai_codex_responses = _create_lazy_stream(_load_openai_codex_responses_module())
stream_simple_openai_codex_responses = _create_lazy_simple_stream(
    _load_openai_codex_responses_module()
)

# OpenAI Completions
stream_openai_completions = _create_lazy_stream(_load_openai_completions_module())
stream_simple_openai_completions = _create_lazy_simple_stream(_load_openai_completions_module())

# OpenAI Responses
stream_openai_responses = _create_lazy_stream(_load_openai_responses_module())
stream_simple_openai_responses = _create_lazy_simple_stream(_load_openai_responses_module())


# ---------------------------------------------------------------------------
# Registration (port of registerBuiltInApiProviders / resetApiProviders)
# ---------------------------------------------------------------------------


def register_built_in_api_providers() -> None:
    """Register all built-in API providers with lazy loading."""
    register_api_provider(
        ApiProvider(
            api="anthropic-messages",
            stream=stream_anthropic,
            stream_simple=stream_simple_anthropic,
        ),
        source_id="builtin",
    )

    register_api_provider(
        ApiProvider(
            api="openai-completions",
            stream=stream_openai_completions,
            stream_simple=stream_simple_openai_completions,
        ),
        source_id="builtin",
    )

    register_api_provider(
        ApiProvider(
            api="openai-responses",
            stream=stream_openai_responses,
            stream_simple=stream_simple_openai_responses,
        ),
        source_id="builtin",
    )

    register_api_provider(
        ApiProvider(
            api="azure-openai-responses",
            stream=stream_azure_openai_responses,
            stream_simple=stream_simple_azure_openai_responses,
        ),
        source_id="builtin",
    )

    register_api_provider(
        ApiProvider(
            api="openai-codex-responses",
            stream=stream_openai_codex_responses,
            stream_simple=stream_simple_openai_codex_responses,
        ),
        source_id="builtin",
    )

    register_api_provider(
        ApiProvider(
            api="google-generative-ai",
            stream=stream_google,
            stream_simple=stream_simple_google,
        ),
        source_id="builtin",
    )

    register_api_provider(
        ApiProvider(
            api="google-vertex",
            stream=stream_google_vertex,
            stream_simple=stream_simple_google_vertex,
        ),
        source_id="builtin",
    )


def reset_api_providers() -> None:
    """Clear all providers and re-register built-ins."""
    clear_api_providers()
    register_built_in_api_providers()


# Auto-register on import (matches TS behavior: registerBuiltInApiProviders())
register_built_in_api_providers()
