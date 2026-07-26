"""Anthropic OAuth flow (Claude Pro/Max).

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/anthropic.ts``.

NOTE: This module uses a local HTTP server for the OAuth callback.
It is only intended for CLI use, not browser environments.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from cortex.ai.oauth.pkce import generate_pkce
from cortex.ai.oauth.types import OAuthCredentials

__all__ = [
    "AnthropicOAuthProvider",
    "login_anthropic",
    "refresh_anthropic_token",
]

# Decode obfuscated client ID
_CLIENT_ID = base64.b64decode("OWQxYzI1MGEtZTYxYi00NGQ5LTg4ZWQtNTk0NGQxOTYyZjVl").decode("ascii")
_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_CALLBACK_HOST = "127.0.0.1"
_CALLBACK_PORT = 53692
_CALLBACK_PATH = "/callback"
_REDIRECT_URI = f"http://localhost:{_CALLBACK_PORT}{_CALLBACK_PATH}"
_SCOPES = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload"
)


def _parse_authorization_input(input_str: str) -> dict[str, str | None]:
    """Parse authorization code from various input formats."""
    value = input_str.strip()
    if not value:
        return {}

    # Try parsing as URL
    try:
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            params = parse_qs(parsed.query)
            return {
                "code": params.get("code", [None])[0],
                "state": params.get("state", [None])[0],
            }
    except Exception:
        pass

    # Try parsing as fragment format (code#state)
    if "#" in value:
        parts = value.split("#", 1)
        return {"code": parts[0], "state": parts[1] if len(parts) > 1 else None}

    # Try parsing as query string
    if "code=" in value:
        params = parse_qs(value)
        return {
            "code": params.get("code", [None])[0],
            "state": params.get("state", [None])[0],
        }

    # Assume it's just the code
    return {"code": value}


async def _post_json(url: str, body: dict[str, Any]) -> str:
    """POST JSON to a URL and return the response body."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP request failed. status={response.status_code}; url={url}; "
                f"body={response.text}"
            )
        return response.text


async def _exchange_authorization_code(
    code: str,
    state: str,
    verifier: str,
    redirect_uri: str,
) -> OAuthCredentials:
    """Exchange authorization code for tokens."""
    try:
        response_body = await _post_json(
            _TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "client_id": _CLIENT_ID,
                "code": code,
                "state": state,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
    except Exception as error:
        raise RuntimeError(
            f"Token exchange request failed. url={_TOKEN_URL}; redirect_uri={redirect_uri}; "
            f"details={error}"
        ) from error

    try:
        import json

        token_data = json.loads(response_body)
    except Exception as error:
        raise RuntimeError(
            f"Token exchange returned invalid JSON. url={_TOKEN_URL}; body={response_body}; "
            f"details={error}"
        ) from error

    return OAuthCredentials(
        refresh=token_data["refresh_token"],
        access=token_data["access_token"],
        expires=int(time.time() * 1000) + token_data["expires_in"] * 1000 - 5 * 60 * 1000,
    )


async def login_anthropic(
    on_auth: Any = None,
    on_prompt: Any = None,
    on_progress: Any = None,
    on_manual_code_input: Any = None,
) -> OAuthCredentials:
    """Login with Anthropic OAuth (authorization code + PKCE).

    Args:
        on_auth: Callback called with auth info (url, instructions).
        on_prompt: Async callback to prompt user for input.
        on_progress: Callback for progress messages.
        on_manual_code_input: Async callback for manual code input.

    Returns:
        OAuth credentials.
    """
    verifier, challenge = await generate_pkce()

    # Build authorization URL
    auth_params = {
        "code": "true",
        "client_id": _CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "scope": _SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }

    from urllib.parse import urlencode

    auth_url = f"{_AUTHORIZE_URL}?{urlencode(auth_params)}"

    if on_auth:
        on_auth(
            {
                "url": auth_url,
                "instructions": (
                    "Complete login in your browser. If the browser is on another machine, "
                    "paste the final redirect URL here."
                ),
            }
        )

    # For now, prompt user to paste the code
    if on_prompt:
        input_str = await on_prompt(
            {
                "message": "Paste the authorization code or full redirect URL:",
                "placeholder": _REDIRECT_URI,
            }
        )
        parsed = _parse_authorization_input(input_str)
        code = parsed.get("code")
        state = parsed.get("state") or verifier

        if not code:
            raise RuntimeError("Missing authorization code")

        if on_progress:
            on_progress("Exchanging authorization code for tokens...")

        return await _exchange_authorization_code(code, state, verifier, _REDIRECT_URI)

    raise RuntimeError("No prompt callback provided")


async def refresh_anthropic_token(refresh_token: str) -> OAuthCredentials:
    """Refresh Anthropic OAuth token."""
    try:
        response_body = await _post_json(
            _TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": _CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
    except Exception as error:
        raise RuntimeError(
            f"Anthropic token refresh request failed. url={_TOKEN_URL}; details={error}"
        ) from error

    try:
        import json

        data = json.loads(response_body)
    except Exception as error:
        raise RuntimeError(
            f"Anthropic token refresh returned invalid JSON. url={_TOKEN_URL}; "
            f"body={response_body}; details={error}"
        ) from error

    return OAuthCredentials(
        refresh=data["refresh_token"],
        access=data["access_token"],
        expires=int(time.time() * 1000) + data["expires_in"] * 1000 - 5 * 60 * 1000,
    )


class AnthropicOAuthProvider:
    """Anthropic OAuth provider."""

    @property
    def id(self) -> str:
        return "anthropic"

    @property
    def name(self) -> str:
        return "Anthropic (Claude Pro/Max)"

    @property
    def uses_callback_server(self) -> bool:
        return True

    async def login(self, callbacks: Any) -> OAuthCredentials:
        """Run the login flow."""
        return await login_anthropic(
            on_auth=callbacks.on_auth,
            on_prompt=callbacks.on_prompt,
            on_progress=callbacks.on_progress,
            on_manual_code_input=callbacks.on_manual_code_input,
        )

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        """Refresh expired credentials."""
        return await refresh_anthropic_token(credentials.refresh)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        """Convert credentials to API key string."""
        return credentials.access


# Singleton instance
anthropic_oauth_provider = AnthropicOAuthProvider()
