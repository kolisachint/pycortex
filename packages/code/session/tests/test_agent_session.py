"""Tests for the agent-session bridge (step 7.4).

The bridge is what a mode holds: subscribe, prompt, queue, abort, persist. These
drive the real thing — a real `Agent` over `ai/provider-faux` — because the
interesting behaviour is in the seams between them, and a double for either side
would agree with whatever the port happened to do.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cortex.ai.providers.faux import faux_assistant_message, register_faux_provider
from cortex.code.config import SettingsManager
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.session import (
    AgentSession,
    AgentSessionConfig,
    PromptOptions,
    SessionManager,
    create_agent_session,
)


@pytest.fixture
def faux():
    registration = register_faux_provider()
    yield registration
    registration.unregister()


def _settings() -> SettingsManager:
    return SettingsManager.from_storage(InMemorySettingsStorage())


def _session(faux: Any = None, **overrides: Any) -> AgentSession:
    """A session over an unpersisted session file, with a faux model if given."""
    return create_agent_session(
        cwd="/w/project",
        settings_manager=_settings(),
        session_manager=SessionManager("/w/project", "", persist=False),
        model=faux.get_model() if faux is not None else None,
        **overrides,
    ).session


def _text_of(message: Any) -> str:
    """The text blocks of a message, joined — the transcript is a union of types."""
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


async def _run_until_idle(session: AgentSession, passes: int = 200) -> None:
    for _ in range(passes):
        if not session.is_streaming:
            return
        await asyncio.sleep(0)


class TestCreateAgentSession:
    def test_it_wires_the_agent_to_the_session(self, faux: Any) -> None:
        session = _session(faux)
        assert session.agent is not None
        assert session.model is faux.get_model()
        assert session.is_streaming is False

    def test_an_unpersisted_session_writes_no_file(self) -> None:
        session = _session()
        assert session.session_file is None

    def test_the_session_id_reaches_the_agent(self, faux: Any) -> None:
        """Providers key their prompt cache on it, so it cannot be invented twice."""
        session = _session(faux)
        assert session.agent.session_id == session.session_id

    def test_with_no_model_the_agent_holds_the_sentinel(self) -> None:
        session = _session()
        assert session.model is not None
        assert session.model.provider == "unknown"


class TestSubscribe:
    def test_listeners_see_events_and_can_leave(self, faux: Any) -> None:
        session = _session(faux)
        seen: list[dict[str, Any]] = []
        unsubscribe = session.subscribe(seen.append)

        session._emit({"type": "queue_update"})  # pyright: ignore[reportPrivateUsage]
        assert len(seen) == 1

        unsubscribe()
        session._emit({"type": "queue_update"})  # pyright: ignore[reportPrivateUsage]
        assert len(seen) == 1, "an unsubscribed listener kept receiving events"

    def test_dispose_drops_the_listeners_and_the_agent(self, faux: Any) -> None:
        session = _session(faux)
        seen: list[dict[str, Any]] = []
        session.subscribe(seen.append)

        session.dispose()

        assert session._event_listeners == []  # pyright: ignore[reportPrivateUsage]
        assert session.agent._listeners == []  # pyright: ignore[reportPrivateUsage]

    async def test_agent_events_are_forwarded_in_order(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("hello")])
        session = _session(faux)
        seen: list[str] = []
        session.subscribe(lambda event: seen.append(event["type"]))

        await session.prompt("hi")

        assert seen[0] == "agent_start"
        assert seen[-1] == "agent_end"
        assert "message_end" in seen


class TestPrompt:
    async def test_a_prompt_round_trips(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("the answer")])
        session = _session(faux)

        await session.prompt("the question")

        roles = [getattr(m, "role", "") for m in session.messages]
        assert roles == ["user", "assistant"]
        assert _text_of(session.messages[-1]) == "the answer"

    async def test_a_prompt_with_no_model_explains_itself(self) -> None:
        session = _session()

        with pytest.raises(RuntimeError, match="No API key found for the selected model"):
            await session.prompt("hi")

    async def test_the_preflight_hook_reports_the_refusal(self) -> None:
        session = _session()
        results: list[bool] = []

        with pytest.raises(RuntimeError):
            await session.prompt("hi", PromptOptions(preflight_result=results.append))

        assert results == [False]

    async def test_the_preflight_hook_reports_acceptance(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("ok")])
        session = _session(faux)
        results: list[bool] = []

        await session.prompt("hi", PromptOptions(preflight_result=results.append))

        assert results == [True]

    async def test_a_registry_that_has_no_key_says_so(self, faux: Any) -> None:
        class _Registry:
            def has_configured_auth(self, model: Any) -> bool:
                return False

            def is_using_oauth(self, model: Any) -> bool:
                return False

        session = _session(faux, model_registry=_Registry())

        with pytest.raises(RuntimeError, match="No API key found for faux"):
            await session.prompt("hi")

    async def test_an_expired_oauth_login_says_so(self, faux: Any) -> None:
        class _Registry:
            def has_configured_auth(self, model: Any) -> bool:
                return False

            def is_using_oauth(self, model: Any) -> bool:
                return True

        session = _session(faux, model_registry=_Registry())

        with pytest.raises(RuntimeError, match="Authentication failed"):
            await session.prompt("hi")

    async def test_a_registry_with_a_key_lets_the_turn_through(self, faux: Any) -> None:
        class _Registry:
            def has_configured_auth(self, model: Any) -> bool:
                return True

            def is_using_oauth(self, model: Any) -> bool:
                return False

        faux.set_responses([faux_assistant_message("ok")])
        session = _session(faux, model_registry=_Registry())

        await session.prompt("hi")

        assert [getattr(m, "role", "") for m in session.messages] == ["user", "assistant"]


class TestQueues:
    async def test_prompting_mid_turn_needs_a_behaviour(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("late")

        faux.set_responses([slow, faux_assistant_message("second")])
        session = _session(faux)
        turn = asyncio.ensure_future(session.prompt("first"))
        await _wait_until_streaming(session)

        with pytest.raises(RuntimeError, match="streaming_behavior"):
            await session.prompt("no behaviour given")

        released.set()
        await turn

    async def test_a_steered_message_is_queued_and_announced(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("first")

        faux.set_responses([slow, faux_assistant_message("second")])
        session = _session(faux)
        updates: list[dict[str, Any]] = []
        session.subscribe(lambda event: updates.append(event))
        turn = asyncio.ensure_future(session.prompt("first"))
        await _wait_until_streaming(session)

        await session.prompt("and another thing", PromptOptions(streaming_behavior="steer"))

        assert session.get_steering_messages() == ["and another thing"]
        assert session.pending_message_count == 1
        assert any(e["type"] == "queue_update" for e in updates), "the UI was never told"

        released.set()
        await turn
        await _run_until_idle(session)

    async def test_a_delivered_message_leaves_the_queue(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("first")

        faux.set_responses([slow, faux_assistant_message("second")])
        session = _session(faux)
        turn = asyncio.ensure_future(session.prompt("first"))
        await _wait_until_streaming(session)
        await session.prompt("steered", PromptOptions(streaming_behavior="steer"))
        released.set()
        await turn

        assert session.get_steering_messages() == [], "the queue still shows a sent message"

    async def test_a_follow_up_is_queued_separately(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("first")

        faux.set_responses([slow, faux_assistant_message("second")])
        session = _session(faux)
        turn = asyncio.ensure_future(session.prompt("first"))
        await _wait_until_streaming(session)

        await session.prompt("later", PromptOptions(streaming_behavior="followUp"))
        assert session.get_follow_up_messages() == ["later"]

        released.set()
        await turn

    async def test_clear_queue_returns_what_it_dropped(self, faux: Any) -> None:
        session = _session(faux)
        await session.steer("a")
        await session.follow_up("b")

        cleared = session.clear_queue()

        assert cleared == {"steering": ["a"], "follow_up": ["b"]}
        assert session.pending_message_count == 0
        assert session.agent.has_queued_messages() is False


class TestAbort:
    async def test_abort_ends_the_turn(self, faux: Any) -> None:
        released = asyncio.Event()

        async def slow(*_args: Any) -> Any:
            await released.wait()
            return faux_assistant_message("too late")

        faux.set_responses([slow])
        session = _session(faux)
        turn = asyncio.ensure_future(session.prompt("hi"))
        await _wait_until_streaming(session)

        # Release first, then abort: `abort()` has to carry the turn all the way
        # to idle by itself, with nothing else awaiting it.
        released.set()
        await session.abort()

        assert session.is_streaming is False, "abort() returned while the turn was still running"
        await turn
        assert getattr(session.messages[-1], "stop_reason", "") == "aborted"

    async def test_abort_with_nothing_running_is_harmless(self, faux: Any) -> None:
        session = _session(faux)
        await session.abort()
        assert session.is_streaming is False


class TestPersistence:
    async def test_messages_are_appended_to_the_session(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("stored")])
        manager = SessionManager("/w/project", "", persist=False)
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=_settings(),
            session_manager=manager,
            model=faux.get_model(),
        ).session

        await session.prompt("remember this")

        stored = [e for e in manager.get_entries() if e.get("type") == "message"]
        roles = [e["message"]["role"] for e in stored]
        assert roles == ["user", "assistant"]
        assert stored[0]["message"]["content"][0]["text"] == "remember this"

    async def test_a_custom_message_takes_the_custom_entry_path(self, faux: Any) -> None:
        recorded: list[tuple[Any, ...]] = []

        class _Manager:
            def append_message(self, message: dict[str, Any]) -> str:
                recorded.append(("message", message))
                return "1"

            def append_custom_message_entry(self, *args: Any) -> str:
                recorded.append(("custom", *args))
                return "2"

        session = AgentSession(
            AgentSessionConfig(
                agent=faux_agent(faux),
                session_manager=_Manager(),
                settings_manager=_settings(),
                cwd="/w/project",
            )
        )
        custom = {"role": "custom", "custom_type": "note", "content": "aside"}
        await session._process_agent_event({"type": "message_end", "message": custom})  # pyright: ignore[reportPrivateUsage]

        assert recorded == [("custom", "note", "aside", None, None)], recorded


def faux_agent(faux: Any) -> Any:
    from cortex.agent.agent import Agent, AgentOptions

    return Agent(AgentOptions(initial_state={"model": faux.get_model()}))


async def _wait_until_streaming(session: AgentSession, passes: int = 100) -> None:
    for _ in range(passes):
        if session.is_streaming:
            return
        await asyncio.sleep(0)
    raise AssertionError("the turn never started")
