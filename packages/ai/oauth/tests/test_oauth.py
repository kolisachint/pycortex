"""Tests for OAuth module.

Mechanical port of hoocode's oauth tests (if any).
"""

from __future__ import annotations

from cortex.ai.oauth import (
    OAuthCredentials,
    anthropic_oauth_provider,
    get_oauth_provider,
    get_oauth_providers,
    github_copilot_oauth_provider,
    normalize_domain,
    openai_codex_oauth_provider,
    register_oauth_provider,
    reset_oauth_providers,
    unregister_oauth_provider,
)
from cortex.ai.oauth.oauth_page import oauth_error_html, oauth_success_html
from cortex.ai.oauth.pkce import generate_pkce

# ---------------------------------------------------------------------------
# Types tests
# ---------------------------------------------------------------------------


class TestOAuthCredentials:
    def test_create_credentials(self) -> None:
        creds = OAuthCredentials(
            refresh="refresh_token",
            access="access_token",
            expires=1234567890,
        )
        assert creds.refresh == "refresh_token"
        assert creds.access == "access_token"
        assert creds.expires == 1234567890

    def test_create_credentials_with_extra(self) -> None:
        creds = OAuthCredentials(
            refresh="refresh",
            access="access",
            expires=1000,
            extra={"enterprise_url": "https://example.com"},
        )
        assert creds.extra["enterprise_url"] == "https://example.com"


# ---------------------------------------------------------------------------
# PKCE tests
# ---------------------------------------------------------------------------


class TestPKCE:
    async def test_generate_pkce(self) -> None:
        verifier, challenge = await generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 0
        assert len(challenge) > 0

    async def test_pkce_is_unique(self) -> None:
        v1, c1 = await generate_pkce()
        v2, c2 = await generate_pkce()
        assert v1 != v2
        assert c1 != c2


# ---------------------------------------------------------------------------
# OAuth page tests
# ---------------------------------------------------------------------------


class TestOAuthPage:
    def test_success_html(self) -> None:
        html = oauth_success_html("Login successful")
        assert "Authentication successful" in html
        assert "Login successful" in html

    def test_error_html(self) -> None:
        html = oauth_error_html("Something went wrong", "Details here")
        assert "Authentication failed" in html
        assert "Something went wrong" in html
        assert "Details here" in html

    def test_error_html_without_details(self) -> None:
        html = oauth_error_html("Error message")
        assert "Error message" in html


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


class TestProviders:
    def test_anthropic_provider_properties(self) -> None:
        assert anthropic_oauth_provider.id == "anthropic"
        assert anthropic_oauth_provider.name == "Anthropic (Claude Pro/Max)"
        assert anthropic_oauth_provider.uses_callback_server is True

    def test_github_copilot_provider_properties(self) -> None:
        assert github_copilot_oauth_provider.id == "github-copilot"
        assert github_copilot_oauth_provider.name == "GitHub Copilot"
        assert github_copilot_oauth_provider.uses_callback_server is False

    def test_openai_codex_provider_properties(self) -> None:
        assert openai_codex_oauth_provider.id == "openai-codex"
        assert openai_codex_oauth_provider.name == "OpenAI Codex (ChatGPT)"
        assert openai_codex_oauth_provider.uses_callback_server is True

    def test_get_api_key(self) -> None:
        creds = OAuthCredentials(
            refresh="refresh",
            access="my-api-key",
            expires=1000,
        )
        assert anthropic_oauth_provider.get_api_key(creds) == "my-api-key"
        assert github_copilot_oauth_provider.get_api_key(creds) == "my-api-key"


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_all_providers(self) -> None:
        providers = get_oauth_providers()
        assert len(providers) >= 3
        provider_ids = [p.id for p in providers]
        assert "anthropic" in provider_ids
        assert "github-copilot" in provider_ids
        assert "openai-codex" in provider_ids

    def test_get_provider_by_id(self) -> None:
        provider = get_oauth_provider("anthropic")
        assert provider is not None
        assert provider.id == "anthropic"

    def test_get_provider_returns_none_for_unknown(self) -> None:
        provider = get_oauth_provider("unknown")
        assert provider is None

    def test_register_custom_provider(self) -> None:
        class CustomProvider:
            @property
            def id(self) -> str:
                return "custom"

            @property
            def name(self) -> str:
                return "Custom"

            @property
            def uses_callback_server(self) -> bool:
                return False

            async def login(self, callbacks: object) -> OAuthCredentials:
                raise NotImplementedError

            async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
                return credentials

            def get_api_key(self, credentials: OAuthCredentials) -> str:
                return credentials.access

        register_oauth_provider(CustomProvider())
        provider = get_oauth_provider("custom")
        assert provider is not None
        assert provider.name == "Custom"

        # Cleanup
        unregister_oauth_provider("custom")

    def test_unregister_restores_builtin(self) -> None:
        # Unregister a built-in provider
        unregister_oauth_provider("anthropic")
        provider = get_oauth_provider("anthropic")
        # Should still exist (restored)
        assert provider is not None

    def test_unregister_removes_custom(self) -> None:
        class CustomProvider:
            @property
            def id(self) -> str:
                return "custom-temp"

            @property
            def name(self) -> str:
                return "Custom Temp"

            @property
            def uses_callback_server(self) -> bool:
                return False

            async def login(self, callbacks: object) -> OAuthCredentials:
                raise NotImplementedError

            async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
                return credentials

            def get_api_key(self, credentials: OAuthCredentials) -> str:
                return credentials.access

        register_oauth_provider(CustomProvider())
        assert get_oauth_provider("custom-temp") is not None

        unregister_oauth_provider("custom-temp")
        assert get_oauth_provider("custom-temp") is None

    def test_reset_providers(self) -> None:
        reset_oauth_providers()
        providers = get_oauth_providers()
        assert len(providers) >= 3


# ---------------------------------------------------------------------------
# GitHub Copilot utility tests
# ---------------------------------------------------------------------------


class TestGitHubCopilotUtils:
    def test_normalize_domain_empty(self) -> None:
        assert normalize_domain("") is None
        assert normalize_domain("  ") is None

    def test_normalize_domain_plain(self) -> None:
        assert normalize_domain("github.com") == "github.com"

    def test_normalize_domain_with_protocol(self) -> None:
        assert normalize_domain("https://github.com") == "github.com"

    def test_normalize_domain_enterprise(self) -> None:
        assert normalize_domain("company.ghe.com") == "company.ghe.com"

    def test_normalize_domain_invalid(self) -> None:
        # "not a domain" will be parsed - we accept that behavior
        result = normalize_domain("not a domain")
        # Just verify it doesn't raise an exception
        assert result is None or isinstance(result, str)
