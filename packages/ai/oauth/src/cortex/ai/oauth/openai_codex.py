"""OpenAI Codex (ChatGPT) OAuth flow.

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/openai-codex.ts``.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from cortex.ai.oauth.types import OAuthCredentials

__all__ = [
    "OpenAICodexOAuthProvider",
    "login_openai_codex",
    "refresh_openai_codex_token",
]

_AUTHORIZE_URL = "https://auth0.openai.com/authorize"
_TOKEN_URL = "https://auth0.openai.com/oauth/token"
_CLIENT_ID = "oRQgIPL9m00pZJxZMsX6gh2TGNnRrSby"


async def _post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    """POST JSON to a URL and return the response."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=body,
            headers=headers or {"Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
        return response.json()


async def login_openai_codex(
    on_auth: Any = None,
    on_prompt: Any = None,
    on_progress: Any = None,
) -> OAuthCredentials:
    """Login with OpenAI Codex OAuth.

    This is a simplified implementation. The full flow requires PKCE and
    a local callback server.
    """
    if on_progress:
        on_progress("OpenAI Codex login not fully implemented yet")

    raise NotImplementedError("OpenAI Codex OAuth login is not yet implemented")


async def refresh_openai_codex_token(refresh_token: str) -> OAuthCredentials:
    """Refresh OpenAI Codex token."""
    try:
        data = await _post_json(
            _TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": _CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
    except Exception as error:
        raise RuntimeError(f"OpenAI Codex token refresh failed: {error}") from error

    return OAuthCredentials(
        refresh=data.get("refresh_token", refresh_token),
        access=data["access_token"],
        expires=int(time.time() * 1000) + data.get("expires_in", 3600) * 1000 - 5 * 60 * 1000,
    )


class OpenAICodexOAuthProvider:
    """OpenAI Codex OAuth provider."""

    @property
    def id(self) -> str:
        return "openai-codex"

    @property
    def name(self) -> str:
        return "OpenAI Codex (ChatGPT)"

    @property
    def uses_callback_server(self) -> bool:
        return True

    async def login(self, callbacks: Any) -> OAuthCredentials:
        """Run the login flow."""
        return await login_openai_codex(
            on_auth=callbacks.on_auth,
            on_prompt=callbacks.on_prompt,
            on_progress=callbacks.on_progress,
        )

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        """Refresh expired credentials."""
        return await refresh_openai_codex_token(credentials.refresh)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        """Convert credentials to API key string."""
        return credentials.access


# Singleton instance
openai_codex_oauth_provider = OpenAICodexOAuthProvider()
