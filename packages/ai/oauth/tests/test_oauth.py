"""Tests for OAuth module.

Mechanical port of hoocode's oauth tests (if any).
"""

from __future__ import annotations

import time

from cortex.ai.oauth import (
    LOGIN_CANCELLED,
    OAuthCredentials,
    anthropic_oauth_provider,
    get_oauth_api_key,
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


# ---------------------------------------------------------------------------
# get_oauth_api_key (ported with step 7.11, its first caller)
# ---------------------------------------------------------------------------


class _CountingProvider:
    """A provider whose refresh succeeds and is counted."""

    id = "test-get-api-key"
    name = "Counting Provider"
    uses_callback_server = False

    def __init__(self) -> None:
        self.refreshes = 0

    async def login(self, callbacks: object) -> OAuthCredentials:  # pragma: no cover
        raise AssertionError("not used")

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        self.refreshes += 1
        return OAuthCredentials(
            refresh=credentials.refresh,
            access="fresh-access",
            expires=_now_ms() + 60_000,
        )

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return f"Bearer {credentials.access}"


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


class TestGetOAuthApiKey:
    async def test_unexpired_credentials_are_used_as_they_are(self) -> None:
        provider = _CountingProvider()
        register_oauth_provider(provider)
        try:
            creds = OAuthCredentials(refresh="r", access="live-access", expires=_now_ms() + 60_000)
            result = await get_oauth_api_key(provider.id, {provider.id: creds})

            assert result is not None
            assert result.api_key == "Bearer live-access"
            assert provider.refreshes == 0
        finally:
            reset_oauth_providers()

    async def test_expired_credentials_are_refreshed_and_handed_back(self) -> None:
        """The *new* credentials come back beside the key so the caller can persist
        them under the same lock it read the old ones under."""
        provider = _CountingProvider()
        register_oauth_provider(provider)
        try:
            creds = OAuthCredentials(refresh="r", access="stale-access", expires=_now_ms() - 1000)
            result = await get_oauth_api_key(provider.id, {provider.id: creds})

            assert result is not None
            assert result.api_key == "Bearer fresh-access"
            assert result.new_credentials.access == "fresh-access"
            assert result.new_credentials.expires > _now_ms()
            assert provider.refreshes == 1
        finally:
            reset_oauth_providers()

    async def test_no_stored_credentials_is_none_not_an_error(self) -> None:
        provider = _CountingProvider()
        register_oauth_provider(provider)
        try:
            assert await get_oauth_api_key(provider.id, {}) is None
        finally:
            reset_oauth_providers()

    async def test_an_unknown_provider_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Unknown OAuth provider"):
            await get_oauth_api_key("no-such-provider", {})

    async def test_a_failing_refresh_raises_rather_than_returning_none(self) -> None:
        """The three outcomes have to stay distinct: the caller must be able to
        tell "nothing to refresh" from "refreshing did not work"."""
        import pytest

        class Failing(_CountingProvider):
            id = "test-failing-refresh"

            async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
                raise RuntimeError("network down")

        provider = Failing()
        register_oauth_provider(provider)
        try:
            creds = OAuthCredentials(refresh="r", access="a", expires=_now_ms() - 1000)
            with pytest.raises(RuntimeError, match="Failed to refresh OAuth token"):
                await get_oauth_api_key(provider.id, {provider.id: creds})
        finally:
            reset_oauth_providers()


# ---------------------------------------------------------------------------
# Cancelling a login (the abort signal, wired with step 7.11)
# ---------------------------------------------------------------------------


class _Signal:
    """Anything with a boolean ``aborted`` is an abort signal in this port."""

    def __init__(self, aborted: bool = False) -> None:
        self.aborted = aborted


class TestLoginCancellation:
    def test_the_sentinel_has_the_value_the_ts_uses(self) -> None:
        """Pinned because it is a contract across packages, not a private detail:
        the dialog in ``cortex.code.interactive`` raises it and
        ``login_controller`` compares against it. That the two agree is asserted
        on the far side, in ``test_login_dialog.py`` — this leaf must not import
        the one that depends on it."""
        assert LOGIN_CANCELLED == "Login cancelled"

    async def test_an_already_aborted_signal_stops_the_sleep_immediately(self) -> None:
        import pytest
        from cortex.ai.oauth.github_copilot import (
            _abortable_sleep,  # pyright: ignore[reportPrivateUsage]
        )

        with pytest.raises(RuntimeError, match=LOGIN_CANCELLED):
            await _abortable_sleep(60.0, _Signal(aborted=True))

    async def test_a_sleep_with_no_signal_still_sleeps(self) -> None:
        from cortex.ai.oauth.github_copilot import (
            _abortable_sleep,  # pyright: ignore[reportPrivateUsage]
        )

        await _abortable_sleep(0.01, None)

    async def test_aborting_mid_sleep_stops_within_a_beat(self) -> None:
        """A cancel must not wait out the poll interval, which can be tens of
        seconds — pressing Escape has to take effect roughly when it is pressed."""
        import asyncio

        import pytest
        from cortex.ai.oauth.github_copilot import (
            _abortable_sleep,  # pyright: ignore[reportPrivateUsage]
        )

        signal = _Signal()

        async def abort_soon() -> None:
            await asyncio.sleep(0.05)
            signal.aborted = True

        started = time.monotonic()
        with pytest.raises(RuntimeError, match=LOGIN_CANCELLED):
            await asyncio.gather(_abortable_sleep(30.0, signal), abort_soon())
        assert time.monotonic() - started < 5

    async def test_the_poll_loop_refuses_to_start_when_already_aborted(self) -> None:
        """Regression: the signal was accepted and never used, so a cancelled
        Copilot login kept polling until the device code expired."""
        import pytest
        from cortex.ai.oauth.github_copilot import (
            _poll_for_access_token,  # pyright: ignore[reportPrivateUsage]
        )

        with pytest.raises(RuntimeError, match=LOGIN_CANCELLED):
            await _poll_for_access_token("github.com", "device-code", 5, 900, _Signal(aborted=True))
