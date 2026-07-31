"""GitHub Copilot OAuth flow.

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/github-copilot.ts``.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from cortex.ai.oauth.types import (
    LOGIN_CANCELLED,
    OAuthAuthInfo,
    OAuthCredentials,
    OAuthPrompt,
)

__all__ = [
    "GitHubCopilotOAuthProvider",
    "get_github_copilot_base_url",
    "login_github_copilot",
    "normalize_domain",
    "refresh_github_copilot_token",
]

# Decode obfuscated client ID
_CLIENT_ID = base64.b64decode("SXYxLmI1MDdhMDhjODdlY2ZlOTg=").decode("ascii")

_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}

_INITIAL_POLL_INTERVAL_MULTIPLIER = 1.2
_SLOW_DOWN_POLL_INTERVAL_MULTIPLIER = 1.4


def normalize_domain(input_str: str) -> str | None:
    """Normalize a GitHub Enterprise domain."""
    trimmed = input_str.strip()
    if not trimmed:
        return None
    try:
        if "://" not in trimmed:
            trimmed = f"https://{trimmed}"
        parsed = urlparse(trimmed)
        return parsed.hostname
    except Exception:
        return None


def _get_urls(domain: str) -> dict[str, str]:
    """Get OAuth URLs for a domain."""
    return {
        "deviceCodeUrl": f"https://{domain}/login/device/code",
        "accessTokenUrl": f"https://{domain}/login/oauth/access_token",
        "copilotTokenUrl": f"https://api.{domain}/copilot_internal/v2/token",
    }


def get_github_copilot_base_url(
    token: str | None = None,
    enterprise_domain: str | None = None,
) -> str:
    """Get the base URL for GitHub Copilot API."""
    if token:
        # Extract from proxy-ep in token
        import re

        match = re.search(r"proxy-ep=([^;]+)", token)
        if match:
            proxy_host = match[1]
            api_host = proxy_host.replace("proxy.", "api.", 1)
            return f"https://{api_host}"

    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return "https://api.individual.githubcopilot.com"


async def _fetch_json(url: str, **kwargs: Any) -> Any:
    """Fetch JSON from a URL."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{response.status_code} {response.status_code}: {response.text}")
        return response.json()


async def _start_device_flow(domain: str) -> dict[str, Any]:
    """Start the device code flow."""
    urls = _get_urls(domain)
    data = await _fetch_json(
        urls["deviceCodeUrl"],
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "GitHubCopilotChat/0.35.0",
        },
        content=f"client_id={_CLIENT_ID}&scope=read:user",
    )

    if not isinstance(data, dict):
        raise RuntimeError("Invalid device code response")

    required_fields = ["device_code", "user_code", "verification_uri", "interval", "expires_in"]
    for field in required_fields:
        if field not in data:
            raise RuntimeError(f"Missing field: {field}")

    return data


def _aborted(signal: Any) -> bool:
    """Whether an abort signal has been tripped.

    ``getattr`` rather than a type, because this port has no ``AbortSignal``
    type: anything with a boolean ``aborted`` is one (see MIGRATION_NOTES).
    """
    return bool(getattr(signal, "aborted", False))


async def _abortable_sleep(seconds: float, signal: Any = None) -> None:
    """Port of ``abortableSleep``.

    The TS listens for the abort event; there is no event here, so the wait is
    broken into short slices and the flag is checked between them. A cancelled
    login has to stop within a beat of Escape rather than at the end of the
    current poll interval, which can be tens of seconds.
    """
    if _aborted(signal):
        raise RuntimeError(LOGIN_CANCELLED)
    if signal is None:
        await asyncio.sleep(seconds)
        return

    slice_seconds = 0.1
    remaining = seconds
    while remaining > 0:
        await asyncio.sleep(min(slice_seconds, remaining))
        if _aborted(signal):
            raise RuntimeError(LOGIN_CANCELLED)
        remaining -= slice_seconds


async def _poll_for_access_token(
    domain: str,
    device_code: str,
    interval_seconds: int,
    expires_in: int,
    signal: Any = None,
) -> str:
    """Poll for access token.

    ``signal`` arrives with step 7.11, the first caller to pass one: the login
    dialog trips it on Escape, and without it a cancelled Copilot login kept
    polling until the device code expired — up to a quarter of an hour after the
    user thought they had backed out.
    """
    urls = _get_urls(domain)
    deadline = time.time() + expires_in
    interval_ms = max(1000, interval_seconds * 1000)
    interval_multiplier = _INITIAL_POLL_INTERVAL_MULTIPLIER
    slow_down_responses = 0

    while time.time() < deadline:
        if _aborted(signal):
            raise RuntimeError(LOGIN_CANCELLED)

        remaining_ms = (deadline - time.time()) * 1000
        wait_ms = min(interval_ms * interval_multiplier, remaining_ms)
        await _abortable_sleep(wait_ms / 1000, signal)

        raw = await _fetch_json(
            urls["accessTokenUrl"],
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "GitHubCopilotChat/0.35.0",
            },
            content=(
                f"client_id={_CLIENT_ID}&device_code={device_code}"
                "&grant_type=urn:ietf:params:oauth:grant-type:device_code"
            ),
        )

        if isinstance(raw, dict) and "access_token" in raw:
            return raw["access_token"]

        if isinstance(raw, dict) and "error" in raw:
            error = raw["error"]
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                slow_down_responses += 1
                interval_ms = raw.get("interval", interval_ms + 5000) * 1000
                interval_multiplier = _SLOW_DOWN_POLL_INTERVAL_MULTIPLIER
                continue
            description = raw.get("error_description", "")
            raise RuntimeError(f"Device flow failed: {error}: {description}")

    if slow_down_responses > 0:
        raise RuntimeError(
            "Device flow timed out after one or more slow_down responses. "
            "This is often caused by clock drift in WSL or VM environments."
        )

    raise RuntimeError("Device flow timed out")


async def refresh_github_copilot_token(
    refresh_token: str,
    enterprise_domain: str | None = None,
) -> OAuthCredentials:
    """Refresh GitHub Copilot token."""
    domain = enterprise_domain or "github.com"
    urls = _get_urls(domain)

    raw = await _fetch_json(
        urls["copilotTokenUrl"],
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {refresh_token}",
            **_COPILOT_HEADERS,
        },
    )

    if not isinstance(raw, dict):
        raise RuntimeError("Invalid Copilot token response")

    token = raw.get("token")
    expires_at = raw.get("expires_at")

    if not isinstance(token, str) or not isinstance(expires_at, (int, float)):
        raise RuntimeError("Invalid Copilot token response fields")

    return OAuthCredentials(
        refresh=refresh_token,
        access=token,
        expires=int(expires_at * 1000) - 5 * 60 * 1000,
        extra={"enterprise_url": enterprise_domain},
    )


async def login_github_copilot(
    on_auth: Any = None,
    on_prompt: Any = None,
    on_progress: Any = None,
    signal: Any = None,
) -> OAuthCredentials:
    """Login with GitHub Copilot OAuth (device code flow)."""
    if on_prompt:
        input_str = await on_prompt(
            OAuthPrompt(
                message="GitHub Enterprise URL/domain (blank for github.com)",
                placeholder="company.ghe.com",
                allow_empty=True,
            )
        )
    else:
        input_str = ""

    enterprise_domain = normalize_domain(input_str)
    if input_str.strip() and not enterprise_domain:
        raise RuntimeError("Invalid GitHub Enterprise URL/domain")

    domain = enterprise_domain or "github.com"

    device = await _start_device_flow(domain)

    if on_auth:
        on_auth(
            OAuthAuthInfo(
                url=device["verification_uri"],
                instructions=f"Enter code: {device['user_code']}",
            )
        )

    github_access_token = await _poll_for_access_token(
        domain,
        device["device_code"],
        device["interval"],
        device["expires_in"],
        signal,
    )

    credentials = await refresh_github_copilot_token(github_access_token, enterprise_domain)
    return credentials


class GitHubCopilotOAuthProvider:
    """GitHub Copilot OAuth provider."""

    @property
    def id(self) -> str:
        return "github-copilot"

    @property
    def name(self) -> str:
        return "GitHub Copilot"

    @property
    def uses_callback_server(self) -> bool:
        return False

    async def login(self, callbacks: Any) -> OAuthCredentials:
        """Run the login flow."""
        return await login_github_copilot(
            on_auth=callbacks.on_auth,
            on_prompt=callbacks.on_prompt,
            on_progress=callbacks.on_progress,
            signal=callbacks.signal,
        )

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        """Refresh expired credentials."""
        enterprise_url = credentials.extra.get("enterprise_url")
        enterprise_domain = normalize_domain(enterprise_url) if enterprise_url else None
        return await refresh_github_copilot_token(credentials.refresh, enterprise_domain)

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        """Convert credentials to API key string."""
        return credentials.access


# Singleton instance
github_copilot_oauth_provider = GitHubCopilotOAuthProvider()
