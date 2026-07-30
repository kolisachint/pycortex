"""Port of ``test/auth-storage.test.ts``, in the TS's order.

Two groups are this port's rather than the TS's, and both cover behaviour the TS
gets from a library:

* ``TestSerialisation`` — ``proper-lockfile`` and ``JSON.parse`` are one line each
  there; here credentials are revived by hand, so the revival needs its own tests
  (an unknown ``type``, a truncated OAuth row);
* ``TestLocking`` — the ``onCompromised`` test cannot be ported (see the class
  docstring) so the lock is tested for what this implementation actually
  guarantees: mutual exclusion, and recovery from a stale lock.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from cortex.ai.oauth import (
    OAuthCredentials,
    register_oauth_provider,
    reset_oauth_providers,
)
from cortex.code.config.auth_storage import (
    ApiKeyCredential,
    AuthStatus,
    AuthStorage,
    FileAuthStorageBackend,
    OAuthCredential,
    _DirectoryLock,  # pyright: ignore[reportPrivateUsage]
)
from cortex.code.config.resolve_config_value import clear_config_value_cache


@pytest.fixture(autouse=True)
def _clean_caches():  # pyright: ignore[reportUnusedFunction]
    clear_config_value_cache()
    yield
    clear_config_value_cache()
    reset_oauth_providers()


@pytest.fixture
def auth_json_path(tmp_path: Path) -> str:
    return str(tmp_path / "auth.json")


def write_auth_json(path: str, data: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(data), encoding="utf-8")


def read_auth_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _now_ms() -> int:
    return int(time.time() * 1000)


class TestApiKeyResolution:
    async def test_literal_api_key_is_returned_directly(self, auth_json_path: str):
        write_auth_json(
            auth_json_path, {"anthropic": {"type": "api_key", "key": "sk-ant-literal-key"}}
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "sk-ant-literal-key"

    async def test_bang_prefix_executes_command_and_uses_stdout(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "!echo test-api-key-from-command"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "test-api-key-from-command"

    async def test_bang_prefix_trims_whitespace(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "!echo '  spaced-key  '"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "spaced-key"

    async def test_bang_prefix_keeps_interior_newlines(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "!printf 'line1\\nline2'"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "line1\nline2"

    async def test_bang_prefix_returns_none_on_command_failure(self, auth_json_path: str):
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "!exit 1"}})
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") is None

    async def test_bang_prefix_returns_none_on_nonexistent_command(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "!nonexistent-command-12345"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") is None

    async def test_bang_prefix_returns_none_on_empty_output(self, auth_json_path: str):
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "!printf ''"}})
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") is None

    async def test_env_var_name_resolves_to_env_value(
        self, auth_json_path: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("TEST_AUTH_API_KEY_12345", "env-api-key-value")
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "TEST_AUTH_API_KEY_12345"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "env-api-key-value"

    async def test_literal_used_when_not_an_env_var(
        self, auth_json_path: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("literal_api_key_value", raising=False)
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "literal_api_key_value"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "literal_api_key_value"

    async def test_command_can_use_shell_features(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {"anthropic": {"type": "api_key", "key": "!echo 'hello world' | tr ' ' '-'"}},
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "hello-world"


class TestCaching:
    """The command cache is process-wide, which is what these count."""

    @staticmethod
    def _counting_command(counter_file: Path, tail: str) -> str:
        path = str(counter_file).replace("\\", "/")
        return f'!sh -c \'count=$(cat "{path}"); echo $((count + 1)) > "{path}"; {tail}\''

    async def test_command_only_executed_once_per_process(
        self, auth_json_path: str, tmp_path: Path
    ):
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, 'echo "key-value"')
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": command}})

        storage = AuthStorage.create(auth_json_path)
        await storage.get_api_key("anthropic")
        await storage.get_api_key("anthropic")
        await storage.get_api_key("anthropic")

        assert int(counter.read_text().strip()) == 1

    async def test_cache_persists_across_instances(self, auth_json_path: str, tmp_path: Path):
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, 'echo "key-value"')
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": command}})

        await AuthStorage.create(auth_json_path).get_api_key("anthropic")
        await AuthStorage.create(auth_json_path).get_api_key("anthropic")

        assert int(counter.read_text().strip()) == 1

    async def test_clear_cache_allows_command_to_run_again(
        self, auth_json_path: str, tmp_path: Path
    ):
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, 'echo "key-value"')
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": command}})

        storage = AuthStorage.create(auth_json_path)
        await storage.get_api_key("anthropic")
        clear_config_value_cache()
        await storage.get_api_key("anthropic")

        assert int(counter.read_text().strip()) == 2

    async def test_different_commands_cached_separately(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {
                "anthropic": {"type": "api_key", "key": "!echo key-anthropic"},
                "openai": {"type": "api_key", "key": "!echo key-openai"},
            },
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "key-anthropic"
        assert await storage.get_api_key("openai") == "key-openai"

    async def test_failed_commands_are_cached_not_retried(
        self, auth_json_path: str, tmp_path: Path
    ):
        counter = tmp_path / "counter"
        counter.write_text("0")
        command = self._counting_command(counter, "exit 1")
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": command}})

        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") is None
        assert await storage.get_api_key("anthropic") is None

        assert int(counter.read_text().strip()) == 1

    async def test_env_vars_are_not_cached(
        self, auth_json_path: str, monkeypatch: pytest.MonkeyPatch
    ):
        name = "TEST_AUTH_KEY_CACHE_TEST_98765"
        monkeypatch.setenv(name, "first-value")
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": name}})

        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("anthropic") == "first-value"

        monkeypatch.setenv(name, "second-value")
        assert await storage.get_api_key("anthropic") == "second-value"


class _StubOAuthProvider:
    """An OAuth provider whose refresh always succeeds, and counts calls."""

    def __init__(self, provider_id: str) -> None:
        self.id = provider_id
        self.name = "Test OAuth Provider"
        self.uses_callback_server = False
        self.refreshes = 0

    async def login(self, callbacks: Any) -> OAuthCredentials:  # pragma: no cover
        raise AssertionError("Not used in this test")

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        self.refreshes += 1
        return OAuthCredentials(
            refresh=credentials.refresh,
            access="refreshed-access-token",
            expires=_now_ms() + 60_000,
            extra=dict(credentials.extra),
        )

    def get_api_key(self, credentials: OAuthCredentials) -> str:
        return f"Bearer {credentials.access}"


class TestOAuthRefresh:
    async def test_expired_token_is_refreshed_and_persisted(self, auth_json_path: str):
        provider = _StubOAuthProvider("test-oauth-refresh")
        register_oauth_provider(provider)
        write_auth_json(
            auth_json_path,
            {
                provider.id: {
                    "type": "oauth",
                    "refresh": "refresh-token",
                    "access": "expired-access-token",
                    "expires": _now_ms() - 10_000,
                }
            },
        )

        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key(provider.id) == "Bearer refreshed-access-token"

        # The refreshed credentials went to disk under the same lock they were
        # read under, so the next process does not refresh again.
        on_disk = read_auth_json(auth_json_path)[provider.id]
        assert on_disk["access"] == "refreshed-access-token"
        assert on_disk["expires"] > _now_ms()

    async def test_unexpired_token_is_used_without_refreshing(self, auth_json_path: str):
        provider = _StubOAuthProvider("test-oauth-valid")
        register_oauth_provider(provider)
        write_auth_json(
            auth_json_path,
            {
                provider.id: {
                    "type": "oauth",
                    "refresh": "refresh-token",
                    "access": "live-access-token",
                    "expires": _now_ms() + 60_000,
                }
            },
        )

        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key(provider.id) == "Bearer live-access-token"
        assert provider.refreshes == 0

    async def test_unknown_oauth_provider_yields_no_key(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {
                "provider-this-build-never-heard-of": {
                    "type": "oauth",
                    "refresh": "r",
                    "access": "a",
                    "expires": _now_ms() + 60_000,
                }
            },
        )
        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key("provider-this-build-never-heard-of") is None

    async def test_failed_refresh_preserves_credentials_for_retry(self, auth_json_path: str):
        """A refresh that raises must not delete the credentials.

        Losing them would make the failure unrecoverable — the user would have to
        re-authenticate rather than retry — so the key is ``None`` and the file is
        untouched.
        """

        class Failing(_StubOAuthProvider):
            async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
                raise RuntimeError("network down")

        provider = Failing("test-oauth-failing")
        register_oauth_provider(provider)
        stored = {
            "type": "oauth",
            "refresh": "refresh-token",
            "access": "expired-access-token",
            "expires": _now_ms() - 10_000,
        }
        write_auth_json(auth_json_path, {provider.id: stored})

        storage = AuthStorage.create(auth_json_path)
        assert await storage.get_api_key(provider.id) is None
        assert read_auth_json(auth_json_path)[provider.id]["refresh"] == "refresh-token"
        assert storage.drain_errors()


class TestPersistenceSemantics:
    def test_set_preserves_unrelated_external_edits(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {
                "anthropic": {"type": "api_key", "key": "old-anthropic"},
                "openai": {"type": "api_key", "key": "openai-key"},
            },
        )
        storage = AuthStorage.create(auth_json_path)

        # Another process adds a provider while this one is running.
        write_auth_json(
            auth_json_path,
            {
                "anthropic": {"type": "api_key", "key": "old-anthropic"},
                "openai": {"type": "api_key", "key": "openai-key"},
                "google": {"type": "api_key", "key": "google-key"},
            },
        )

        storage.set("anthropic", ApiKeyCredential(key="new-anthropic"))

        updated = read_auth_json(auth_json_path)
        assert updated["anthropic"]["key"] == "new-anthropic"
        assert updated["openai"]["key"] == "openai-key"
        assert updated["google"]["key"] == "google-key"

    def test_remove_preserves_unrelated_external_edits(self, auth_json_path: str):
        write_auth_json(
            auth_json_path,
            {
                "anthropic": {"type": "api_key", "key": "anthropic-key"},
                "openai": {"type": "api_key", "key": "openai-key"},
            },
        )
        storage = AuthStorage.create(auth_json_path)

        write_auth_json(
            auth_json_path,
            {
                "anthropic": {"type": "api_key", "key": "anthropic-key"},
                "openai": {"type": "api_key", "key": "openai-key"},
                "google": {"type": "api_key", "key": "google-key"},
            },
        )

        storage.remove("anthropic")

        updated = read_auth_json(auth_json_path)
        assert "anthropic" not in updated
        assert updated["openai"]["key"] == "openai-key"
        assert updated["google"]["key"] == "google-key"

    def test_does_not_overwrite_malformed_file_after_load_error(self, auth_json_path: str):
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "anthropic-key"}})
        storage = AuthStorage.create(auth_json_path)
        Path(auth_json_path).write_text("{invalid-json", encoding="utf-8")

        storage.reload()
        storage.set("openai", ApiKeyCredential(key="openai-key"))

        assert Path(auth_json_path).read_text(encoding="utf-8") == "{invalid-json"

    def test_reload_records_errors_and_drain_clears_them(self, auth_json_path: str):
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "anthropic-key"}})
        storage = AuthStorage.create(auth_json_path)
        Path(auth_json_path).write_text("{invalid-json", encoding="utf-8")

        storage.reload()

        # A failed reload keeps the last good data rather than emptying itself.
        assert storage.get("anthropic") == ApiKeyCredential(key="anthropic-key")

        first_drain = storage.drain_errors()
        assert len(first_drain) > 0
        assert isinstance(first_drain[0], Exception)

        assert storage.drain_errors() == []


class TestAuthStatus:
    def test_does_not_expose_stored_keys_or_tokens(self):
        storage = AuthStorage.in_memory(
            {
                "anthropic": ApiKeyCredential(key="secret-api-key"),
                "openai": OAuthCredential(
                    refresh="secret-refresh-token",
                    access="secret-access-token",
                    expires=_now_ms() + 1000,
                ),
            }
        )

        assert storage.get_auth_status("anthropic") == AuthStatus(configured=True, source="stored")
        assert storage.get_auth_status("openai") == AuthStatus(configured=True, source="stored")
        assert "secret-api-key" not in repr(storage.get_auth_status("anthropic"))
        assert "secret-access-token" not in repr(storage.get_auth_status("openai"))
        assert "secret-refresh-token" not in repr(storage.get_auth_status("openai"))

    def test_environment_is_reported_as_unconfigured_with_its_var_named(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """An env key is usable but is not something ``/logout`` can remove, so
        ``configured`` stays False and the label names where it came from."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        storage = AuthStorage.in_memory()

        status = storage.get_auth_status("anthropic")
        assert status.configured is False
        assert status.source == "environment"
        assert status.label == "ANTHROPIC_API_KEY"
        # ...while `has_auth` — "can this provider be called" — says yes.
        assert storage.has_auth("anthropic") is True

    def test_fallback_resolver_is_reported(self):
        storage = AuthStorage.in_memory()
        storage.set_fallback_resolver(lambda provider: "k" if provider == "custom" else None)

        status = storage.get_auth_status("custom")
        assert status.source == "fallback"
        assert status.label == "custom provider config"
        assert storage.has_auth("custom") is True
        assert storage.has_auth("other") is False


class TestRuntimeOverrides:
    async def test_runtime_override_takes_priority(self, auth_json_path: str):
        write_auth_json(
            auth_json_path, {"anthropic": {"type": "api_key", "key": "!echo stored-key"}}
        )
        storage = AuthStorage.create(auth_json_path)
        storage.set_runtime_api_key("anthropic", "runtime-key")

        assert await storage.get_api_key("anthropic") == "runtime-key"

    async def test_removing_runtime_override_falls_back(self, auth_json_path: str):
        write_auth_json(
            auth_json_path, {"anthropic": {"type": "api_key", "key": "!echo stored-key"}}
        )
        storage = AuthStorage.create(auth_json_path)
        storage.set_runtime_api_key("anthropic", "runtime-key")
        storage.remove_runtime_api_key("anthropic")

        assert await storage.get_api_key("anthropic") == "stored-key"

    async def test_runtime_override_is_reported_but_not_configured(self):
        storage = AuthStorage.in_memory()
        storage.set_runtime_api_key("anthropic", "runtime-key")

        status = storage.get_auth_status("anthropic")
        assert status.source == "runtime"
        assert status.label == "--api-key"
        assert status.configured is False

    async def test_include_fallback_false_skips_the_resolver(self):
        """The registry passes ``include_fallback=False`` so that its own
        ``models.json`` key is not resolved twice."""
        storage = AuthStorage.in_memory()
        storage.set_fallback_resolver(lambda _provider: "from-models-json")

        assert await storage.get_api_key("custom") == "from-models-json"
        assert await storage.get_api_key("custom", include_fallback=False) is None


class TestSerialisation:
    """Reviving credentials is this port's own code, so it gets its own tests."""

    def test_unknown_credential_type_is_skipped_not_fatal(self, auth_json_path: str):
        """`auth.json` is shared with other builds; one unreadable row must not
        cost the user every other credential in the file."""
        write_auth_json(
            auth_json_path,
            {
                "anthropic": {"type": "api_key", "key": "good"},
                "future": {"type": "passkey", "handle": "whatever"},
            },
        )
        storage = AuthStorage.create(auth_json_path)

        assert storage.get("anthropic") == ApiKeyCredential(key="good")
        assert storage.get("future") is None
        assert storage.list() == ["anthropic"]

    def test_truncated_oauth_row_is_skipped(self, auth_json_path: str):
        write_auth_json(auth_json_path, {"openai": {"type": "oauth", "access": "a"}})
        storage = AuthStorage.create(auth_json_path)
        assert storage.get("openai") is None

    def test_oauth_extra_fields_survive_a_round_trip(self, auth_json_path: str):
        """GitHub Copilot stores its endpoint in ``extra``, which the TS writes
        flat beside the token rather than nested."""
        storage = AuthStorage.create(auth_json_path)
        storage.set(
            "github-copilot",
            OAuthCredential(refresh="r", access="a", expires=123, extra={"endpoint": "https://x"}),
        )

        raw = read_auth_json(auth_json_path)["github-copilot"]
        assert raw["endpoint"] == "https://x"
        assert "extra" not in raw

        reloaded = AuthStorage.create(auth_json_path).get("github-copilot")
        assert isinstance(reloaded, OAuthCredential)
        assert reloaded.extra == {"endpoint": "https://x"}

    def test_file_is_created_with_owner_only_permissions(self, tmp_path: Path):
        path = str(tmp_path / "nested" / "auth.json")
        storage = AuthStorage.create(path)
        storage.set("anthropic", ApiKeyCredential(key="k"))

        if os.name != "nt":
            assert os.stat(path).st_mode & 0o777 == 0o600
            assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700


class TestLocking:
    """What this lock guarantees, since the TS's library is not here.

    ``proper-lockfile``'s ``onCompromised`` callback — the subject of the TS's
    "oauth lock compromise handling" test — has no counterpart: that callback
    fires when the library's own lock-refresh timer notices the lockfile was
    deleted under it, and :class:`_DirectoryLock` has no refresh timer. The
    behaviour it protects (a compromised lock aborts the write rather than
    racing) is therefore **not ported**; what is here is mutual exclusion and
    stale recovery, which is what the refresh path actually relies on.
    """

    def test_second_acquire_fails_while_the_first_is_held(self, tmp_path: Path):
        path = str(tmp_path / "auth.json")
        first = _DirectoryLock(path)
        first.acquire_sync()
        try:
            with pytest.raises(TimeoutError):
                _DirectoryLock(path).acquire_sync()
        finally:
            first.release()

        # Released, so the next acquire succeeds.
        second = _DirectoryLock(path)
        second.acquire_sync()
        second.release()

    def test_a_stale_lock_is_broken(self, tmp_path: Path):
        """A killed process must not wedge ``auth.json`` forever."""
        path = str(tmp_path / "auth.json")
        lock_dir = tmp_path / "auth.json.lock"
        lock_dir.mkdir()
        stale = time.time() - 120
        os.utime(lock_dir, (stale, stale))

        lock = _DirectoryLock(path)
        lock.acquire_sync()
        lock.release()

    def test_backend_releases_the_lock_when_the_callback_raises(self, tmp_path: Path):
        """A raising callback must not leave the file locked for the process."""
        path = str(tmp_path / "auth.json")
        backend = FileAuthStorageBackend(path)

        def boom(_current: str | None) -> Any:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            backend.with_lock(boom)

        assert not (tmp_path / "auth.json.lock").exists()


class TestPriorityOrder:
    """The resolution order itself, each step pinned against the one below it.

    Added after mutation testing: the tests above set one source at a time, so a
    mutant that reordered the chain passed every one of them.
    """

    async def test_a_stored_key_beats_an_environment_variable(
        self, auth_json_path: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "sk-stored"}})
        storage = AuthStorage.create(auth_json_path)

        assert await storage.get_api_key("anthropic") == "sk-stored"

    async def test_a_runtime_override_beats_a_stored_key_and_the_environment(
        self, auth_json_path: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "sk-stored"}})
        storage = AuthStorage.create(auth_json_path)
        storage.set_runtime_api_key("anthropic", "sk-runtime")

        assert await storage.get_api_key("anthropic") == "sk-runtime"

    async def test_the_environment_beats_the_fallback_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        storage = AuthStorage.in_memory()
        storage.set_fallback_resolver(lambda _provider: "sk-fallback")

        assert await storage.get_api_key("anthropic") == "sk-from-env"

    async def test_a_stored_oauth_token_beats_an_environment_variable(
        self, auth_json_path: str, monkeypatch: pytest.MonkeyPatch
    ):
        provider = _StubOAuthProvider("test-priority-oauth")
        register_oauth_provider(provider)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        write_auth_json(
            auth_json_path,
            {
                provider.id: {
                    "type": "oauth",
                    "refresh": "r",
                    "access": "live",
                    "expires": _now_ms() + 60_000,
                }
            },
        )
        storage = AuthStorage.create(auth_json_path)

        assert await storage.get_api_key(provider.id) == "Bearer live"

    def test_a_write_is_skipped_entirely_after_a_load_error(self, auth_json_path: str):
        """The guard is what stops a write being *attempted*, which the file
        contents alone cannot show — a corrupt file makes the merge raise anyway,
        so the only visible difference is whether an error was recorded."""
        write_auth_json(auth_json_path, {"anthropic": {"type": "api_key", "key": "anthropic-key"}})
        storage = AuthStorage.create(auth_json_path)
        Path(auth_json_path).write_text("{invalid-json", encoding="utf-8")

        storage.reload()
        assert storage.drain_errors()  # the failed reload

        storage.set("openai", ApiKeyCredential(key="openai-key"))

        # No second error, because no write was tried.
        assert storage.drain_errors() == []
        assert Path(auth_json_path).read_text(encoding="utf-8") == "{invalid-json"
