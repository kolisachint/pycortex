"""What the harness does with a :class:`ResolvedRequestAuth`.

The resolver hands back a frozen dataclass, and the harness read it as a
camelCase dict — ``auth.get("apiKey")`` on an object with no ``get``, and
``if not auth`` on an object that is always truthy. Neither had a test, so both
survived the port: the first crashes the moment a provider asks for a key, and
the second lets a *failed* lookup through as though it had succeeded.

These use a hand-written resolver rather than a real ``ModelRegistry``: the
contract under test is the dataclass, and the registry that produces one is
covered next door in ``packages/code/config/tests/test_model_registry.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.agent.harness import AgentHarness, AgentHarnessOptions
from cortex.ai.types import Model


def _model(provider: str = "anthropic") -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        api="anthropic-messages",
        provider=provider,
        base_url="https://example.invalid",
        reasoning=False,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=1000,
        max_tokens=100,
    )


class _Auth:
    """The registry's answer, spelled out. Same fields as ``ResolvedRequestAuth``."""

    def __init__(
        self,
        *,
        ok: bool = True,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        error: str | None = None,
    ) -> None:
        self.ok = ok
        self.api_key = api_key
        self.headers = headers
        self.error = error


def _harness(auth: Any, *, model: Model | None = None) -> AgentHarness:
    async def resolve(_model: Model) -> Any:
        return auth

    return AgentHarness(
        AgentHarnessOptions(
            model=model if model is not None else _model(),
            get_api_key_and_headers=resolve if auth is not None else None,
        )
    )


async def _key_for(harness: AgentHarness, provider: str) -> str | None:
    """What the agent's ``get_api_key`` callback answers — the harness's own hook."""
    get_api_key: Any = harness.agent.get_api_key
    return await get_api_key(provider)


class TestGetApiKey:
    async def test_it_reads_the_key_off_the_dataclass(self) -> None:
        harness = _harness(_Auth(api_key="sk-test"))

        assert await _key_for(harness, "anthropic") == "sk-test"

    async def test_a_different_provider_is_not_asked(self) -> None:
        harness = _harness(_Auth(api_key="sk-test"))

        assert await _key_for(harness, "openai") is None

    async def test_no_resolver_means_no_key(self) -> None:
        harness = _harness(None)

        assert await _key_for(harness, "anthropic") is None

    async def test_a_resolver_with_no_key_says_none(self) -> None:
        harness = _harness(_Auth(api_key=None))

        assert await _key_for(harness, "anthropic") is None


class TestCompactionAuth:
    """``compact`` refuses before it touches the session, so no session is needed."""

    async def test_a_failed_lookup_stops_the_compaction(self) -> None:
        harness = _harness(_Auth(ok=False, error='No API key found for "anthropic"'))

        with pytest.raises(ValueError, match="No auth available for compaction"):
            await harness.compact()

    async def test_a_successful_lookup_with_no_key_stops_it_too(self) -> None:
        harness = _harness(_Auth(ok=True, api_key=None))

        with pytest.raises(ValueError, match="No auth available for compaction"):
            await harness.compact()

    async def test_no_resolver_stops_it(self) -> None:
        harness = _harness(None)

        with pytest.raises(ValueError, match="No auth available for compaction"):
            await harness.compact()

    async def test_a_resolved_key_gets_past_the_guard(self) -> None:
        # Past the guard is as far as this can go without a session; that it
        # fails *later* is the proof the auth check itself let it through.
        harness = _harness(_Auth(ok=True, api_key="sk-test"))

        with pytest.raises(Exception) as caught:
            await harness.compact()

        assert "No auth available for compaction" not in str(caught.value)
