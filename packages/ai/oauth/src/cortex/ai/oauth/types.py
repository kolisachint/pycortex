"""OAuth types.

Mechanical port of hoocode's ``packages/ai/src/utils/oauth/types.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

#: What a cancelled login raises with, everywhere. Callers compare against it
#: rather than reporting it, so that pressing Escape closes the dialog quietly
#: instead of showing an error.
#:
#: One constant rather than the TS's repeated ``new Error("Login cancelled")``,
#: because the string is a contract *between packages*: the dialog in
#: ``cortex.code.interactive`` raises it, the providers here raise it, and
#: ``login_controller`` is what compares. A literal in three places is a drift
#: waiting to happen — and a drifted one shows the user an error for something
#: they did on purpose.
LOGIN_CANCELLED = "Login cancelled"


@dataclass
class OAuthCredentials:
    """OAuth credentials for a provider."""

    refresh: str
    access: str
    expires: int
    extra: dict[str, Any] = field(default_factory=dict)


# Type alias for provider ID
OAuthProviderId = str


@dataclass
class OAuthPrompt:
    """Prompt displayed during OAuth flow."""

    message: str
    placeholder: str | None = None
    allow_empty: bool = False


@dataclass
class OAuthAuthInfo:
    """Auth info displayed to user during OAuth flow."""

    url: str
    instructions: str | None = None


@dataclass
class OAuthSelectOption:
    """Option for OAuth select prompt."""

    id: str
    label: str


@dataclass
class OAuthSelectPrompt:
    """Select prompt for OAuth flow."""

    message: str
    options: list[OAuthSelectOption] = field(default_factory=list)


class OAuthLoginCallbacks(Protocol):
    """Callbacks for OAuth login flow."""

    def on_auth(self, info: OAuthAuthInfo) -> None: ...
    async def on_prompt(self, prompt: OAuthPrompt) -> str: ...
    def on_progress(self, message: str) -> None: ...
    async def on_manual_code_input(self) -> str: ...
    async def on_select(self, prompt: OAuthSelectPrompt) -> str | None: ...


class OAuthProviderInterface(Protocol):
    """Interface for OAuth providers."""

    @property
    def id(self) -> OAuthProviderId: ...

    @property
    def name(self) -> str: ...

    @property
    def uses_callback_server(self) -> bool: ...

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredentials: ...
    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials: ...
    def get_api_key(self, credentials: OAuthCredentials) -> str: ...
