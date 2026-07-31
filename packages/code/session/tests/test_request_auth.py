"""Resolving a request's credentials, and what reads the answer.

Two things that were unported and one that was misread:

* :meth:`AgentSession._get_required_request_auth` was a stub that raised for
  every model, so ``/tree``'s branch summary could never run;
* :class:`CompactionController` read the registry's
  :class:`~cortex.code.config.ResolvedRequestAuth` as a camelCase dict, which is
  either an ``AttributeError`` or — for the ``if not auth_result.get("ok")``
  guard — a check that silently never fired;
* the ``streamFn`` ``create_agent_session`` wraps the agent in was missing
  entirely, so nothing ever put a stored key on a request.

Port of the behaviour ``agent-session.ts::_getRequiredRequestAuth`` and
``sdk.ts::createAgentSession`` specify.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.ai.providers.faux import register_faux_provider
from cortex.ai.types import Model, SimpleStreamOptions
from cortex.code.config import ResolvedRequestAuth, SettingsManager
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.session import CompactionController, SessionManager, create_agent_session
from cortex.code.session.runtime import get_attribution_headers


@pytest.fixture
def faux():
    registration = register_faux_provider()
    yield registration
    registration.unregister()


def _settings() -> SettingsManager:
    return SettingsManager.from_storage(InMemorySettingsStorage())


def _model(provider: str = "anthropic", base_url: str = "https://example.invalid") -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        api="anthropic-messages",
        provider=provider,
        base_url=base_url,
        reasoning=False,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=1000,
        max_tokens=100,
    )


class _Registry:
    """A registry with one canned answer. The real one is `code/config`'s."""

    def __init__(self, auth: ResolvedRequestAuth, *, oauth: bool = False) -> None:
        self.auth = auth
        self.oauth = oauth
        self.asked: list[Any] = []

    def has_configured_auth(self, model: Any) -> bool:
        return True

    def is_using_oauth(self, model: Any) -> bool:
        return self.oauth

    async def get_api_key_and_headers(self, model: Any) -> ResolvedRequestAuth:
        self.asked.append(model)
        return self.auth

    def refresh(self) -> None: ...

    async def get_available(self) -> list[Any]:
        return []


def _no_stream(*_args: Any) -> Any:
    """A stream function that is never called — its presence is the point."""
    return None


def _session(faux: Any, registry: Any = None) -> Any:
    return create_agent_session(
        cwd="/w/project",
        settings_manager=_settings(),
        session_manager=SessionManager("/w/project", "", persist=False),
        model=faux.get_model(),
        model_registry=registry,
        # The scenarios' seam, and the reason this can build a session against a
        # registry that resolves nothing: an explicit stream function keeps the
        # wrapper off, so nothing here opens a socket.
        stream_fn=_no_stream,
    ).session


# ---------------------------------------------------------------------------
# `_getRequiredRequestAuth` — its four branches, in the TS's order
# ---------------------------------------------------------------------------


class TestGetRequiredRequestAuth:
    async def test_a_resolved_key_comes_back_with_its_headers(self, faux: Any) -> None:
        registry = _Registry(
            ResolvedRequestAuth(ok=True, api_key="sk-test", headers={"X-Trace": "1"})
        )
        session = _session(faux, registry)

        auth = await session._get_required_request_auth(_model())

        assert auth.api_key == "sk-test"
        assert auth.headers == {"X-Trace": "1"}

    async def test_no_key_found_is_re_worded_with_the_login_guidance(self, faux: Any) -> None:
        registry = _Registry(
            ResolvedRequestAuth(ok=False, error='No API key found for "anthropic"')
        )
        session = _session(faux, registry)

        with pytest.raises(RuntimeError, match="Use /login to log into a provider"):
            await session._get_required_request_auth(_model())

    async def test_any_other_failure_is_reported_as_the_registry_worded_it(self, faux: Any) -> None:
        registry = _Registry(ResolvedRequestAuth(ok=False, error="Command failed: !op read"))
        session = _session(faux, registry)

        with pytest.raises(RuntimeError, match="Command failed: !op read"):
            await session._get_required_request_auth(_model())

    async def test_a_subscription_with_no_token_says_log_in_again(self, faux: Any) -> None:
        """`ok` with no key and an OAuth credential: the token could not be made."""
        registry = _Registry(ResolvedRequestAuth(ok=True, api_key=None), oauth=True)
        session = _session(faux, registry)

        with pytest.raises(RuntimeError, match="Run '/login anthropic' to re-authenticate"):
            await session._get_required_request_auth(_model())

    async def test_ok_with_no_key_and_no_subscription_is_a_missing_key(self, faux: Any) -> None:
        registry = _Registry(ResolvedRequestAuth(ok=True, api_key=None), oauth=False)
        session = _session(faux, registry)

        with pytest.raises(RuntimeError, match="No API key found for anthropic"):
            await session._get_required_request_auth(_model())

    async def test_a_session_with_no_registry_still_answers(self, faux: Any) -> None:
        """A session can be built without one; the flows say so rather than raising on `None`."""
        session = _session(faux, None)

        with pytest.raises(RuntimeError, match="No API key found for anthropic"):
            await session._get_required_request_auth(_model())


# ---------------------------------------------------------------------------
# The compaction controller, which reads both shapes of answer
# ---------------------------------------------------------------------------


class _CompactionDeps:
    """Everything :class:`CompactionController` reads, and nothing else."""

    def __init__(self, *, auth: Any, registry_auth: ResolvedRequestAuth | None = None) -> None:
        self._auth = auth
        self.model_registry = _Registry(
            registry_auth if registry_auth is not None else ResolvedRequestAuth(ok=False)
        )
        self.settings_manager = _StubSettings()
        self.session_manager = _StubSessionManager()
        self.events: list[dict[str, Any]] = []
        self.applied: list[dict[str, Any]] = []

    def get_model(self) -> Any:
        return _model()

    async def get_required_request_auth(self, model: Any) -> Any:
        if isinstance(self._auth, Exception):
            raise self._auth
        return self._auth

    def disconnect_from_agent(self) -> None: ...

    def reconnect_to_agent(self) -> None: ...

    async def abort_session(self) -> None: ...

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _StubSessionManager:
    def get_branch(self) -> list[dict[str, Any]]:
        return [{"type": "message"}]


class _StubSettings:
    """`get_compaction_settings` is the controller's whole reading of settings.

    `SettingsManager` has no such method yet — the controller is not wired to a
    session (see its module docstring), and the getter arrives with whatever step
    wires it.
    """

    def get_compaction_settings(self) -> dict[str, Any]:
        return {"enabled": True}


def _controller(deps: Any) -> CompactionController:
    controller = CompactionController(deps)

    def prepare(entries: Any, settings: Any) -> Any:
        return {"entries": entries}

    async def apply(**kwargs: Any) -> dict[str, Any]:
        deps.applied.append(kwargs)
        return {"status": "done", "result": {"summary": "ok"}}

    controller._prepare_compaction = prepare  # type: ignore[method-assign]
    controller._apply_compaction = apply  # type: ignore[method-assign]
    return controller


class TestManualCompactionAuth:
    async def test_the_resolved_key_and_headers_reach_the_compaction(self) -> None:
        deps = _CompactionDeps(
            auth=ResolvedRequestAuth(ok=True, api_key="sk-test", headers={"X-Trace": "1"})
        )

        await _controller(deps).compact()

        assert deps.applied[0]["api_key"] == "sk-test"
        assert deps.applied[0]["headers"] == {"X-Trace": "1"}

    async def test_a_refusal_from_the_resolver_is_the_error_the_user_sees(self) -> None:
        deps = _CompactionDeps(auth=RuntimeError("No API key found for anthropic."))

        with pytest.raises(RuntimeError, match="No API key found for anthropic"):
            await _controller(deps).compact()


async def _run_auto(deps: Any) -> None:
    """The auto-compaction entry point, which `check_compaction` calls."""
    await _controller(deps)._run_auto_compaction("auto", False)  # pyright: ignore[reportPrivateUsage]


class TestAutoCompactionAuth:
    """The auto path asks the registry directly, so it reads `ok` itself."""

    async def test_a_failed_lookup_ends_the_auto_compaction_quietly(self) -> None:
        deps = _CompactionDeps(auth=None, registry_auth=ResolvedRequestAuth(ok=False, error="nope"))

        await _run_auto(deps)

        assert deps.applied == [], "a failed lookup still ran a compaction"
        assert deps.events[-1]["type"] == "compaction_end"

    async def test_ok_with_no_key_ends_it_too(self) -> None:
        deps = _CompactionDeps(auth=None, registry_auth=ResolvedRequestAuth(ok=True, api_key=None))

        await _run_auto(deps)

        assert deps.applied == [], "an empty key still ran a compaction"

    async def test_a_resolved_key_reaches_the_auto_compaction(self) -> None:
        deps = _CompactionDeps(
            auth=None,
            registry_auth=ResolvedRequestAuth(ok=True, api_key="sk-auto", headers={"X-A": "1"}),
        )

        await _run_auto(deps)

        assert deps.applied[0]["api_key"] == "sk-auto"
        assert deps.applied[0]["headers"] == {"X-A": "1"}


# ---------------------------------------------------------------------------
# The stream function the session wraps the agent in
# ---------------------------------------------------------------------------


class TestRegistryStreamFn:
    def test_an_explicit_stream_fn_still_wins(self, faux: Any) -> None:
        """The faux and e2e sessions substitute one; the wrapper must not displace it."""

        def mine(*_args: Any) -> Any:
            return None

        session = create_agent_session(
            cwd="/w/project",
            settings_manager=_settings(),
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
            model_registry=_Registry(ResolvedRequestAuth(ok=True, api_key="k")),
            stream_fn=mine,
        ).session

        assert session.agent.stream_fn is mine

    def test_no_registry_means_no_wrapper(self, faux: Any) -> None:
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=_settings(),
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
        ).session

        assert session.agent.stream_fn is None

    def test_a_registry_installs_one(self, faux: Any) -> None:
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=_settings(),
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
            model_registry=_Registry(ResolvedRequestAuth(ok=True, api_key="k")),
        ).session

        assert session.agent.stream_fn is not None

    async def test_it_puts_the_resolved_key_on_the_request(self, faux: Any) -> None:
        seen: list[SimpleStreamOptions] = []
        session = self._session_over(faux, ResolvedRequestAuth(ok=True, api_key="sk-test"), seen)

        await session.agent.stream_fn(faux.get_model(), None, SimpleStreamOptions())

        assert seen[0].api_key == "sk-test"

    async def test_a_failed_lookup_raises_the_registry_s_reason(self, faux: Any) -> None:
        seen: list[SimpleStreamOptions] = []
        session = self._session_over(
            faux, ResolvedRequestAuth(ok=False, error="No API key found for anthropic."), seen
        )

        with pytest.raises(RuntimeError, match="No API key found for anthropic"):
            await session.agent.stream_fn(faux.get_model(), None, SimpleStreamOptions())

        assert seen == [], "the provider was called anyway"

    async def test_headers_merge_auth_under_the_caller_s_own(self, faux: Any) -> None:
        seen: list[SimpleStreamOptions] = []
        session = self._session_over(
            faux,
            ResolvedRequestAuth(ok=True, api_key="k", headers={"X-A": "auth", "X-B": "auth"}),
            seen,
        )

        await session.agent.stream_fn(
            faux.get_model(), None, SimpleStreamOptions(headers={"X-B": "caller"})
        )

        assert seen[0].headers == {"X-A": "auth", "X-B": "caller"}

    async def test_the_settings_fill_in_a_retry_budget_the_caller_left_unset(
        self, faux: Any
    ) -> None:
        seen: list[SimpleStreamOptions] = []
        session = self._session_over(faux, ResolvedRequestAuth(ok=True, api_key="k"), seen)

        await session.agent.stream_fn(faux.get_model(), None, SimpleStreamOptions(max_retries=9))

        assert seen[0].max_retries == 9, "the caller's own budget was overwritten"
        assert seen[0].max_retry_delay_ms == 60000, "the settings default never arrived"

    @staticmethod
    def _session_over(faux: Any, auth: ResolvedRequestAuth, seen: list[Any]) -> Any:
        """A session whose wrapper calls a recorder instead of a provider."""
        import cortex.ai.stream as stream_module
        import cortex.code.session.runtime as runtime_module

        def record(_model: Any, _context: Any, options: Any) -> Any:
            seen.append(options)
            return options

        # The wrapper imports `stream_simple` when it is built, so the patch has
        # to be in place before `create_agent_session` runs.
        original = stream_module.stream_simple
        stream_module.stream_simple = record
        try:
            assert runtime_module is not None
            return create_agent_session(
                cwd="/w/project",
                settings_manager=_settings(),
                session_manager=SessionManager("/w/project", "", persist=False),
                model=faux.get_model(),
                model_registry=_Registry(auth),
            ).session
        finally:
            stream_module.stream_simple = original


# ---------------------------------------------------------------------------
# Attribution headers
# ---------------------------------------------------------------------------


class TestAttributionHeaders:
    def test_openrouter_gets_them(self) -> None:
        headers = get_attribution_headers(_model(provider="openrouter"), _settings())

        assert headers is not None
        assert headers["X-OpenRouter-Title"] == "hoocode"

    def test_a_custom_provider_pointed_at_openrouter_gets_them_too(self) -> None:
        model = _model(provider="mine", base_url="https://openrouter.ai/api/v1")

        assert get_attribution_headers(model, _settings()) is not None

    def test_anyone_else_gets_none(self) -> None:
        assert get_attribution_headers(_model(provider="anthropic"), _settings()) is None

    def test_telemetry_off_means_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOOCODE_TELEMETRY", "0")

        assert get_attribution_headers(_model(provider="openrouter"), _settings()) is None
