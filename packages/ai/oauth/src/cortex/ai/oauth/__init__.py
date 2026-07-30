"""OAuth credential management for AI providers.

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/index.ts``.

This module handles login, token refresh, and credential storage
for OAuth-based providers:
- Anthropic (Claude Pro/Max)
- GitHub Copilot
- OpenAI Codex (ChatGPT)
"""

from __future__ import annotations

import time
from dataclasses import dataclass

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
    LOGIN_CANCELLED,
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
    "LOGIN_CANCELLED",
    "OAuthApiKeyResult",
    "OAuthAuthInfo",
    "OAuthCredentials",
    "OAuthProviderId",
    "OAuthProviderInterface",
    "OAuthPrompt",
    "OAuthSelectOption",
    "OAuthSelectPrompt",
    # Registry functions
    "get_oauth_api_key",
    "get_oauth_provider",
    "get_oauth_providers",
    "register_oauth_provider",
    "reset_oauth_providers",
    "unregister_oauth_provider",
]


@dataclass(frozen=True)
class OAuthApiKeyResult:
    """What :func:`get_oauth_api_key` answers with: the key, and the credentials
    it came from — which may be newer than the ones passed in."""

    new_credentials: OAuthCredentials
    api_key: str


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


async def get_oauth_api_key(
    provider_id: OAuthProviderId,
    credentials: dict[str, OAuthCredentials],
) -> OAuthApiKeyResult | None:
    """Resolve a provider's API key from stored credentials, refreshing if expired.

    Port of ``getOAuthApiKey``. Ported with step 7.11, which is the first caller:
    :class:`cortex.code.config.AuthStorage` needs the refresh-and-report-back
    shape — the *new* credentials come back beside the key so the caller can
    persist them under the same lock it read them under.

    The three outcomes are distinct on purpose: an unknown provider raises, a
    provider with no stored credentials returns ``None``, and a refresh that
    fails raises rather than returning ``None`` so the caller can tell "nothing
    to refresh" from "refreshing did not work".
    """
    provider = get_oauth_provider(provider_id)
    if provider is None:
        raise ValueError(f"Unknown OAuth provider: {provider_id}")

    creds = credentials.get(provider_id)
    if creds is None:
        return None

    if _now_ms() >= creds.expires:
        try:
            creds = await provider.refresh_token(creds)
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            raise RuntimeError(f"Failed to refresh OAuth token for {provider_id}") from error

    return OAuthApiKeyResult(new_credentials=creds, api_key=provider.get_api_key(creds))


def _now_ms() -> int:
    """``Date.now()`` — epoch milliseconds."""
    return int(time.time() * 1000)
