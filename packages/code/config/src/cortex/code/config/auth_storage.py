"""Credentials for API keys and OAuth tokens. Port of ``core/auth-storage.ts``.

What a provider's key can come from, in the order :meth:`AuthStorage.get_api_key`
tries them: a runtime override (``--api-key``), a stored ``api_key``, a stored
OAuth token (refreshed if expired), an environment variable, and finally the
fallback resolver ``models.json``'s custom providers install. The order is the
contract — a user who passes ``--api-key`` expects it to win over the file, and a
user with a stored key expects it to win over an env var they forgot they set.

**The lock is not incidental.** Several ``pycortex`` processes share one
``auth.json``, and an expired OAuth token has them all wanting to refresh it at
once. Without the lock, the last writer wins and the others' refresh tokens are
silently replaced by a stale one — an unrecoverable state, because a refresh
token is consumed by using it. So the refresh path re-reads the file *inside* the
lock (another process may have already done the work) and writes under the same
lock it read under.

``proper-lockfile`` has no Python counterpart in this workspace, so
:class:`_DirectoryLock` is a port of the part of it that is used: a lock is a
*directory* beside the file, created with ``mkdir`` because that is atomic on
every filesystem worth supporting, and considered stale after 30s so a killed
process cannot wedge the file forever.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Protocol, TypeVar

from cortex.ai.env import find_env_keys, get_env_api_key
from cortex.ai.oauth import (
    OAuthCredentials,
    OAuthProviderId,
    OAuthProviderInterface,
    get_oauth_api_key,
    get_oauth_provider,
    get_oauth_providers,
)
from cortex.code.config.config import get_auth_path
from cortex.code.config.resolve_config_value import resolve_config_value

__all__ = [
    "ApiKeyCredential",
    "AuthCredential",
    "AuthStatus",
    "AuthStorage",
    "AuthStorageBackend",
    "FileAuthStorageBackend",
    "InMemoryAuthStorageBackend",
    "LockResult",
    "OAuthCredential",
]

T = TypeVar("T")

#: Where a provider's auth came from. ``configured`` is separate because the two
#: answers are not the same question: an env var *is* usable auth but is not
#: something ``/logout`` can remove, so the selector shows it as "env: KEY"
#: rather than as configured-by-you.
AuthSource = Literal[
    "stored", "runtime", "environment", "fallback", "models_json_key", "models_json_command"
]


@dataclass
class ApiKeyCredential:
    """A stored API key. ``key`` goes through
    :func:`~cortex.code.config.resolve_config_value.resolve_config_value`, so it
    may be a literal, an env var name, or a ``!command``."""

    key: str
    type: Literal["api_key"] = "api_key"


@dataclass
class OAuthCredential:
    """A stored OAuth token triple, flattened onto the credential as in the TS."""

    refresh: str
    access: str
    expires: int
    extra: dict[str, Any] = field(default_factory=dict)
    type: Literal["oauth"] = "oauth"

    def credentials(self) -> OAuthCredentials:
        """The provider-facing view: what ``refresh_token``/``get_api_key`` take."""
        return OAuthCredentials(
            refresh=self.refresh, access=self.access, expires=self.expires, extra=dict(self.extra)
        )


AuthCredential = ApiKeyCredential | OAuthCredential

AuthStorageData = dict[str, AuthCredential]


@dataclass(frozen=True)
class AuthStatus:
    """Whether a provider has auth, and where it came from — with no key in it.

    The selector renders this, so it must be answerable without refreshing a
    token or running a ``!command``; that is why it carries a ``label`` (the env
    var's name) rather than a value.
    """

    configured: bool
    source: AuthSource | None = None
    label: str | None = None


@dataclass(frozen=True)
class LockResult(Generic[T]):
    """What a locked callback hands back: its own result, and optionally the new
    file contents to write before the lock is released."""

    result: T
    next: str | None = None


class AuthStorageBackend(Protocol):
    """Read-modify-write over the credential store, under a lock."""

    def with_lock(self, fn: Callable[[str | None], LockResult[Any]]) -> Any: ...

    async def with_lock_async(
        self, fn: Callable[[str | None], Awaitable[LockResult[Any]]]
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------

#: A lock older than this is assumed to belong to a dead process (``stale``).
_STALE_SECONDS = 30.0


class _DirectoryLock:
    """``proper-lockfile`` reduced to what this module uses.

    ``os.mkdir`` is the primitive because it fails atomically when the directory
    exists — no create-then-check window — on every filesystem, including the
    network ones where ``O_EXCL`` is unreliable.
    """

    def __init__(self, path: str) -> None:
        self._lock_path = f"{path}.lock"

    def _try_acquire(self) -> bool:
        try:
            os.mkdir(self._lock_path)
        except FileExistsError:
            if self._is_stale():
                self._break_stale()
                return self._try_acquire_once()
            return False
        except OSError as error:
            if error.errno == errno.ENOENT:
                # The parent directory vanished under us; the caller recreates it.
                raise
            return False
        return True

    def _try_acquire_once(self) -> bool:
        try:
            os.mkdir(self._lock_path)
        except OSError:
            return False
        return True

    def _is_stale(self) -> bool:
        try:
            age = time.time() - os.stat(self._lock_path).st_mtime
        except OSError:
            return False
        return age > _STALE_SECONDS

    def _break_stale(self) -> None:
        try:
            os.rmdir(self._lock_path)
        except OSError:
            pass

    def release(self) -> None:
        try:
            os.rmdir(self._lock_path)
        except OSError:
            pass

    def acquire_sync(self) -> None:
        """Ten attempts, 20ms apart — the TS's ``acquireLockSyncWithRetry``."""
        max_attempts = 10
        delay_seconds = 0.02
        for attempt in range(1, max_attempts + 1):
            if self._try_acquire():
                return
            if attempt == max_attempts:
                break
            time.sleep(delay_seconds)
        raise TimeoutError(f"Failed to acquire auth storage lock: {self._lock_path}")

    async def acquire_async(self) -> None:
        """Ten retries with randomised exponential backoff, 100ms → 10s.

        The TS's retry options, one for one. The backoff matters more here than
        in the sync path: this is the token-refresh path, where the process that
        loses the race wants to wait for the winner to *finish* rather than to
        give up and refresh in parallel.
        """
        retries = 10
        min_timeout = 0.1
        max_timeout = 10.0
        for attempt in range(retries + 1):
            if self._try_acquire():
                return
            if attempt == retries:
                break
            backoff = min(min_timeout * (2**attempt), max_timeout)
            await asyncio.sleep(backoff * (0.5 + random.random() * 0.5))  # noqa: S311
        raise TimeoutError(f"Failed to acquire auth storage lock: {self._lock_path}")


class FileAuthStorageBackend:
    """``auth.json`` on disk, mode 0600 in a 0700 directory."""

    def __init__(self, auth_path: str | None = None) -> None:
        self._auth_path = auth_path if auth_path is not None else get_auth_path()

    def _ensure_parent_dir(self) -> None:
        directory = os.path.dirname(self._auth_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, mode=0o700, exist_ok=True)

    def _ensure_file_exists(self) -> None:
        if not os.path.exists(self._auth_path):
            with open(self._auth_path, "w", encoding="utf-8") as handle:
                handle.write("{}")
            os.chmod(self._auth_path, 0o600)

    def _read(self) -> str | None:
        if not os.path.exists(self._auth_path):
            return None
        with open(self._auth_path, encoding="utf-8") as handle:
            return handle.read()

    def _write(self, content: str) -> None:
        with open(self._auth_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(self._auth_path, 0o600)

    def with_lock(self, fn: Callable[[str | None], LockResult[Any]]) -> Any:
        self._ensure_parent_dir()
        self._ensure_file_exists()

        lock = _DirectoryLock(self._auth_path)
        lock.acquire_sync()
        try:
            outcome = fn(self._read())
            if outcome.next is not None:
                self._write(outcome.next)
            return outcome.result
        finally:
            lock.release()

    async def with_lock_async(self, fn: Callable[[str | None], Awaitable[LockResult[Any]]]) -> Any:
        self._ensure_parent_dir()
        self._ensure_file_exists()

        lock = _DirectoryLock(self._auth_path)
        await lock.acquire_async()
        try:
            outcome = await fn(self._read())
            if outcome.next is not None:
                self._write(outcome.next)
            return outcome.result
        finally:
            lock.release()


class InMemoryAuthStorageBackend:
    """The same contract with no file and no lock — one process, one copy."""

    def __init__(self) -> None:
        self._value: str | None = None

    def with_lock(self, fn: Callable[[str | None], LockResult[Any]]) -> Any:
        outcome = fn(self._value)
        if outcome.next is not None:
            self._value = outcome.next
        return outcome.result

    async def with_lock_async(self, fn: Callable[[str | None], Awaitable[LockResult[Any]]]) -> Any:
        outcome = await fn(self._value)
        if outcome.next is not None:
            self._value = outcome.next
        return outcome.result


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _credential_from_json(raw: Any) -> AuthCredential | None:
    """Revive one credential, or ``None`` for a row this build cannot read.

    A row with an unknown ``type`` is skipped rather than raised on: `auth.json`
    is shared with other versions of the tool, and one unreadable provider must
    not cost the user every other credential in the file.
    """
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind == "api_key":
        key = raw.get("key")
        return ApiKeyCredential(key=key) if isinstance(key, str) else None
    if kind == "oauth":
        refresh = raw.get("refresh")
        access = raw.get("access")
        expires = raw.get("expires")
        if not isinstance(refresh, str) or not isinstance(access, str):
            return None
        if not isinstance(expires, int | float):
            return None
        extra = {k: v for k, v in raw.items() if k not in ("type", "refresh", "access", "expires")}
        return OAuthCredential(refresh=refresh, access=access, expires=int(expires), extra=extra)
    return None


def _credential_to_json(credential: AuthCredential) -> dict[str, Any]:
    """The TS's flat shape: OAuth ``extra`` fields sit beside the token, not under it."""
    if isinstance(credential, ApiKeyCredential):
        return {"type": "api_key", "key": credential.key}
    return {
        "type": "oauth",
        "refresh": credential.refresh,
        "access": credential.access,
        "expires": credential.expires,
        **credential.extra,
    }


def _dump(data: AuthStorageData) -> str:
    return json.dumps(
        {provider: _credential_to_json(cred) for provider, cred in data.items()}, indent=2
    )


# ---------------------------------------------------------------------------
# AuthStorage
# ---------------------------------------------------------------------------


class AuthStorage:
    """Credentials, and every way of resolving one.

    Constructed through :meth:`create`, :meth:`from_storage` or
    :meth:`in_memory` rather than directly, as in the TS — the constructor
    reloads, and which backend it reloads from is the whole difference between
    the three.
    """

    def __init__(self, storage: AuthStorageBackend) -> None:
        self._storage = storage
        self._data: AuthStorageData = {}
        self._runtime_overrides: dict[str, str] = {}
        self._fallback_resolver: Callable[[str], str | None] | None = None
        self._load_error: Exception | None = None
        self._errors: list[Exception] = []
        self.reload()

    @staticmethod
    def create(auth_path: str | None = None) -> AuthStorage:
        return AuthStorage(FileAuthStorageBackend(auth_path))

    @staticmethod
    def from_storage(storage: AuthStorageBackend) -> AuthStorage:
        return AuthStorage(storage)

    @staticmethod
    def in_memory(data: AuthStorageData | None = None) -> AuthStorage:
        storage = InMemoryAuthStorageBackend()
        storage.with_lock(lambda _current: LockResult(result=None, next=_dump(data or {})))
        return AuthStorage.from_storage(storage)

    # -- runtime overrides and fallbacks ------------------------------------

    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        """Install a key that is never written to disk (the ``--api-key`` flag)."""
        self._runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        self._runtime_overrides.pop(provider, None)

    def set_fallback_resolver(self, resolver: Callable[[str], str | None]) -> None:
        """Install the last resort: ``models.json``'s custom-provider keys.

        The registry sets this on itself, which is why the two classes know about
        each other in both directions — the registry needs the storage to resolve
        a key, and the storage needs the registry to know about keys that only
        exist in ``models.json``.
        """
        self._fallback_resolver = resolver

    # -- loading and persisting --------------------------------------------

    def _record_error(self, error: Exception) -> None:
        self._errors.append(error)

    def _parse_storage_data(self, content: str | None) -> AuthStorageData:
        if not content:
            return {}
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return {}
        data: AuthStorageData = {}
        for provider, raw in parsed.items():
            credential = _credential_from_json(raw)
            if credential is not None:
                data[str(provider)] = credential
        return data

    def reload(self) -> None:
        """Re-read the store. A read that fails leaves the last good data in place."""
        content: str | None = None

        def read(current: str | None) -> LockResult[None]:
            nonlocal content
            content = current
            return LockResult(result=None)

        try:
            self._storage.with_lock(read)
            self._data = self._parse_storage_data(content)
            self._load_error = None
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            self._load_error = error
            self._record_error(error)

    def _persist_provider_change(self, provider: str, credential: AuthCredential | None) -> None:
        """Write one provider's change, merging over whatever is on disk now.

        Merging rather than dumping ``self._data`` is deliberate: another process
        may have added a provider since this one loaded, and a full dump would
        delete it.
        """
        if self._load_error is not None:
            return

        def merge(current: str | None) -> LockResult[None]:
            merged = dict(self._parse_storage_data(current))
            if credential is not None:
                merged[provider] = credential
            else:
                merged.pop(provider, None)
            return LockResult(result=None, next=_dump(merged))

        try:
            self._storage.with_lock(merge)
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            self._record_error(error)

    # -- the store ---------------------------------------------------------

    def get(self, provider: str) -> AuthCredential | None:
        return self._data.get(provider)

    def set(self, provider: str, credential: AuthCredential) -> None:
        self._data[provider] = credential
        self._persist_provider_change(provider, credential)

    def remove(self, provider: str) -> None:
        self._data.pop(provider, None)
        self._persist_provider_change(provider, None)

    def list(self) -> list[str]:
        return list(self._data.keys())

    def has(self, provider: str) -> bool:
        """Whether ``auth.json`` holds a credential — not whether auth *works*."""
        return provider in self._data

    def has_auth(self, provider: str) -> bool:
        """Whether any form of auth is configured. Never refreshes a token."""
        if provider in self._runtime_overrides:
            return True
        if provider in self._data:
            return True
        if get_env_api_key(provider):
            return True
        return bool(self._fallback_resolver and self._fallback_resolver(provider))

    def get_auth_status(self, provider: str) -> AuthStatus:
        """Where a provider's auth comes from, with no value and no refresh.

        Only a *stored* credential reports ``configured=True``: the other sources
        are usable but are not something ``/logout`` owns, and the selector says
        so differently for each.
        """
        if provider in self._data:
            return AuthStatus(configured=True, source="stored")

        if provider in self._runtime_overrides:
            return AuthStatus(configured=False, source="runtime", label="--api-key")

        env_keys = find_env_keys(provider)
        if env_keys:
            return AuthStatus(configured=False, source="environment", label=env_keys[0])

        if self._fallback_resolver and self._fallback_resolver(provider):
            return AuthStatus(configured=False, source="fallback", label="custom provider config")

        return AuthStatus(configured=False)

    def get_all(self) -> AuthStorageData:
        return dict(self._data)

    def drain_errors(self) -> list[Exception]:
        """Hand over the errors accumulated since the last drain, and forget them."""
        drained = list(self._errors)
        self._errors = []
        return drained

    # -- login / logout ----------------------------------------------------

    async def login(self, provider_id: OAuthProviderId, callbacks: Any) -> None:
        """Run a provider's OAuth flow and store what it returns."""
        provider = get_oauth_provider(provider_id)
        if provider is None:
            raise ValueError(f"Unknown OAuth provider: {provider_id}")

        credentials = await provider.login(callbacks)
        self.set(
            provider_id,
            OAuthCredential(
                refresh=credentials.refresh,
                access=credentials.access,
                expires=credentials.expires,
                extra=dict(credentials.extra),
            ),
        )

    def logout(self, provider: str) -> None:
        self.remove(provider)

    def get_oauth_providers(self) -> list[OAuthProviderInterface]:
        return get_oauth_providers()

    # -- resolving a key ---------------------------------------------------

    async def _refresh_oauth_token_with_lock(
        self, provider_id: OAuthProviderId
    ) -> tuple[str, OAuthCredentials] | None:
        """Refresh under the lock, re-reading the file first.

        The re-read is the point: by the time this process gets the lock another
        may have refreshed already, in which case the stored token is valid and
        there is nothing to do. Refreshing anyway would burn a refresh token that
        the other process is now the only holder of.
        """
        provider = get_oauth_provider(provider_id)
        if provider is None:
            return None

        async def refresh(current: str | None) -> LockResult[tuple[str, OAuthCredentials] | None]:
            current_data = self._parse_storage_data(current)
            self._data = current_data
            self._load_error = None

            cred = current_data.get(provider_id)
            if not isinstance(cred, OAuthCredential):
                return LockResult(result=None)

            if _now_ms() < cred.expires:
                return LockResult(
                    result=(provider.get_api_key(cred.credentials()), cred.credentials())
                )

            oauth_creds = {
                key: value.credentials()
                for key, value in current_data.items()
                if isinstance(value, OAuthCredential)
            }

            refreshed = await get_oauth_api_key(provider_id, oauth_creds)
            if refreshed is None:
                return LockResult(result=None)

            merged = dict(current_data)
            merged[provider_id] = OAuthCredential(
                refresh=refreshed.new_credentials.refresh,
                access=refreshed.new_credentials.access,
                expires=refreshed.new_credentials.expires,
                extra=dict(refreshed.new_credentials.extra),
            )
            self._data = merged
            self._load_error = None
            return LockResult(
                result=(refreshed.api_key, refreshed.new_credentials), next=_dump(merged)
            )

        result: tuple[str, OAuthCredentials] | None = await self._storage.with_lock_async(refresh)
        return result

    async def get_api_key(self, provider_id: str, *, include_fallback: bool = True) -> str | None:
        """A usable key for the provider, refreshing an expired token if needed.

        Priority: runtime override, stored ``api_key``, stored OAuth token,
        environment variable, fallback resolver.
        """
        runtime_key = self._runtime_overrides.get(provider_id)
        if runtime_key:
            return runtime_key

        cred = self._data.get(provider_id)

        if isinstance(cred, ApiKeyCredential):
            return resolve_config_value(cred.key)

        if isinstance(cred, OAuthCredential):
            provider = get_oauth_provider(provider_id)
            if provider is None:
                # An OAuth credential for a provider this build does not know
                # about: nothing can turn it into a key.
                return None

            if _now_ms() >= cred.expires:
                try:
                    result = await self._refresh_oauth_token_with_lock(provider_id)
                    if result is not None:
                        return result[0]
                except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
                    self._record_error(error)
                    # Refreshing failed here — but another process may have
                    # succeeded while this one was trying, so re-read before
                    # giving up.
                    self.reload()
                    updated = self._data.get(provider_id)
                    if isinstance(updated, OAuthCredential) and _now_ms() < updated.expires:
                        return provider.get_api_key(updated.credentials())

                    # Genuinely failed. Returning None makes model discovery skip
                    # this provider; the credentials stay put so `/login` can
                    # retry rather than the user having to re-authenticate blind.
                    return None
            else:
                return provider.get_api_key(cred.credentials())

        env_key = get_env_api_key(provider_id)
        if env_key:
            return env_key

        if include_fallback and self._fallback_resolver:
            return self._fallback_resolver(provider_id)

        return None


def _now_ms() -> int:
    """``Date.now()`` — epoch milliseconds, which is what ``expires`` is in."""
    return int(time.time() * 1000)
