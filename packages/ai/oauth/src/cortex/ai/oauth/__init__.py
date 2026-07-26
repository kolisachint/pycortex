"""OAuth credential management for AI providers.

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/index.ts``.

This module handles login, token refresh, and credential storage
for OAuth-based providers:
- Anthropic (Claude Pro/Max)
- GitHub Copilot
- OpenAI Codex (ChatGPT)
"""

from __future__ import annotations

from cortex.ai.oauth.anthropic import (
    AnthropicOAuthProvider,
    anthropic_oauth_provider,
    login_anthropic,
    refresh_anthropic_token,
)
from cortex.ai.oauth.github_copilot import (
    GitHubCopilotOAuthProvider,
    get_github_copilot_base_url,
    github_copilot_oauth_provider,
    login_github_copilot,
    normalize_domain,
    refresh_github_copilot_token,
)
from cortex.ai.oauth.openai_codex import (
    OpenAICodexOAuthProvider,
    login_openai_codex,
    openai_codex_oauth_provider,
    refresh_openai_codex_token,
)
from cortex.ai.oauth.types import (
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthPrompt,
    OAuthProviderId,
    OAuthProviderInterface,
    OAuthSelectOption,
    OAuthSelectPrompt,
)

__all__ = [
    # Anthropic
    "AnthropicOAuthProvider",
    "anthropic_oauth_provider",
    "login_anthropic",
    "refresh_anthropic_token",
    # GitHub Copilot
    "GitHubCopilotOAuthProvider",
    "get_github_copilot_base_url",
    "github_copilot_oauth_provider",
    "login_github_copilot",
    "normalize_domain",
    "refresh_github_copilot_token",
    # OpenAI Codex
    "OpenAICodexOAuthProvider",
    "login_openai_codex",
    "openai_codex_oauth_provider",
    "refresh_openai_codex_token",
    # Types
    "OAuthAuthInfo",
    "OAuthCredentials",
    "OAuthProviderId",
    "OAuthProviderInterface",
    "OAuthPrompt",
    "OAuthSelectOption",
    "OAuthSelectPrompt",
    # Registry functions
    "get_oauth_provider",
    "get_oauth_providers",
    "register_oauth_provider",
    "reset_oauth_providers",
    "unregister_oauth_provider",
]


# ============================================================================
# Provider Registry
# ============================================================================

# Built-in providers
_BUILT_IN_OAUTH_PROVIDERS: list[OAuthProviderInterface] = [
    anthropic_oauth_provider,
    github_copilot_oauth_provider,
    openai_codex_oauth_provider,
]

# Provider registry
_oauth_provider_registry: dict[str, OAuthProviderInterface] = {
    provider.id: provider for provider in _BUILT_IN_OAUTH_PROVIDERS
}


def get_oauth_provider(provider_id: str) -> OAuthProviderInterface | None:
    """Get an OAuth provider by ID."""
    return _oauth_provider_registry.get(provider_id)


def register_oauth_provider(provider: OAuthProviderInterface) -> None:
    """Register a custom OAuth provider."""
    _oauth_provider_registry[provider.id] = provider


def unregister_oauth_provider(provider_id: str) -> None:
    """Unregister an OAuth provider.

    If the provider is built-in, restores the built-in implementation.
    Custom providers are removed completely.
    """
    built_in = next((p for p in _BUILT_IN_OAUTH_PROVIDERS if p.id == provider_id), None)
    if built_in:
        _oauth_provider_registry[provider_id] = built_in
    else:
        _oauth_provider_registry.pop(provider_id, None)


def reset_oauth_providers() -> None:
    """Reset OAuth providers to built-ins."""
    _oauth_provider_registry.clear()
    for provider in _BUILT_IN_OAUTH_PROVIDERS:
        _oauth_provider_registry[provider.id] = provider


def get_oauth_providers() -> list[OAuthProviderInterface]:
    """Get all registered OAuth providers."""
    return list(_oauth_provider_registry.values())
