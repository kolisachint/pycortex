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
from cortex.code.config import ResolvedRequestAuth, SettingsManager
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

            async def get_api_key_and_headers(self, model: Any) -> ResolvedRequestAuth:
                return ResolvedRequestAuth(ok=True, api_key="k")

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


class TestBashExecution:
    """`executeBash` / `recordBashResult` — the `!command` path (7.6)."""

    def _result(self, output: str = "hi", exit_code: int | None = 0):
        from cortex.code.session.bash_executor import BashResult

        return BashResult(output=output, exit_code=exit_code, cancelled=False, truncated=False)

    def test_an_idle_session_records_the_row_immediately(self, faux: Any):
        session = _session(faux)
        session.record_bash_result("ls", self._result())
        roles = [m.get("role") if isinstance(m, dict) else m.role for m in session.messages]
        assert "bashExecution" in roles
        assert not session.has_pending_bash_messages

    def test_the_row_carries_the_command_and_its_output(self, faux: Any):
        session = _session(faux)
        session.record_bash_result("ls -la", self._result("a\nb"))
        row = session.messages[-1]
        assert row["command"] == "ls -la"
        assert row["output"] == "a\nb"
        assert row["exit_code"] == 0

    async def test_a_row_finished_mid_turn_waits_for_the_turn(self, faux: Any):
        """Ordering, not tidiness: a bash row slipped between a tool call and
        its result makes the next request to the provider malformed."""
        session = _session(faux)
        released = asyncio.Event()

        async def slow(*_args: object):
            await released.wait()
            return faux_assistant_message("done")

        faux.set_responses([slow])
        turn = asyncio.ensure_future(session.prompt("go"))
        for _ in range(50):
            if session.is_streaming:
                break
            await asyncio.sleep(0)

        session.record_bash_result("ls", self._result())
        assert session.has_pending_bash_messages
        roles = [m.get("role") if isinstance(m, dict) else m.role for m in session.messages]
        assert "bashExecution" not in roles, "the row landed in the middle of the turn"

        released.set()
        await turn
        await _run_until_idle(session)

        # The next prompt is what flushes it, as in the TS.
        faux.set_responses([faux_assistant_message("and again")])
        await session.prompt("next")
        await _run_until_idle(session)
        roles = [m.get("role") if isinstance(m, dict) else m.role for m in session.messages]
        assert "bashExecution" in roles, "the deferred row was never flushed"
        assert not session.has_pending_bash_messages

    def test_exclude_from_context_is_recorded_on_the_row(self, faux: Any):
        session = _session(faux)
        session.record_bash_result("cat secrets", self._result(), exclude_from_context=True)
        assert session.messages[-1]["exclude_from_context"] is True

    async def test_execute_bash_runs_the_command_and_records_it(self, faux: Any):
        session = _session(faux)
        chunks: list[str] = []

        class Ops:
            def exec(self, command: str, cwd: str, *, on_data: Any, **_: Any) -> Any:
                on_data(b"the output\n")

                class Result:
                    exit_code = 0

                return Result()

        result = await session.execute_bash("echo hi", chunks.append, operations=Ops())
        assert "the output" in result.output
        assert chunks == ["the output\n"]
        assert session.messages[-1]["command"] == "echo hi"
        assert not session.is_bash_running

    async def test_the_shell_prefix_from_settings_is_applied(self, faux: Any):
        settings = _settings()
        settings.set_shell_command_prefix("shopt -s expand_aliases")
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=settings,
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
        ).session

        seen: list[str] = []

        class Ops:
            def exec(self, command: str, cwd: str, *, on_data: Any, **_: Any) -> Any:
                seen.append(command)

                class Result:
                    exit_code = 0

                return Result()

        await session.execute_bash("ls", operations=Ops())
        assert seen == ["shopt -s expand_aliases\nls"]
        # The row records what the user typed, not what the shell was handed.
        assert session.messages[-1]["command"] == "ls"

    async def test_aborting_a_bash_command_cancels_it(self, faux: Any):
        session = _session(faux)

        class Ops:
            def exec(self, command: str, cwd: str, *, on_data: Any, signal: Any = None, **_: Any):
                session.abort_bash()
                raise RuntimeError("aborted")

        result = await session.execute_bash("sleep 100", operations=Ops())
        assert result.cancelled
        assert not session.is_bash_running


class TestSessionName:
    """`/name` writes through the session; the manager owns the value."""

    def test_a_fresh_session_has_no_name(self):
        assert _session().session_name is None

    def test_setting_a_name_reads_back(self):
        session = _session()
        session.set_session_name("ada")
        assert session.session_name == "ada"

    def test_the_latest_entry_wins(self):
        session = _session()
        session.set_session_name("first")
        session.set_session_name("second")
        assert session.session_name == "second"

    def test_a_name_is_stripped(self):
        session = _session()
        session.set_session_name("  padded  ")
        assert session.session_name == "padded"

    def test_a_blank_name_clears_it(self):
        # `getSessionName` treats a whitespace-only entry as unset, which is how
        # a name is removed without a delete entry.
        session = _session()
        session.set_session_name("ada")
        session.set_session_name("   ")
        assert session.session_name is None

    def test_the_name_is_an_entry_not_a_message(self):
        session = _session()
        session.set_session_name("ada")
        entries = session.session_manager.get_entries()
        assert [entry["type"] for entry in entries] == ["session_info"]
        assert session.messages == []


class TestSessionStats:
    def test_an_empty_session_counts_nothing(self):
        stats = _session().get_session_stats()
        assert (stats.total_messages, stats.user_messages, stats.assistant_messages) == (0, 0, 0)
        assert stats.tokens.total == 0

    def test_it_names_the_session_it_came_from(self):
        session = _session()
        assert session.get_session_stats().session_id == session.session_id

    def test_an_unpersisted_session_has_no_file(self):
        assert _session().get_session_stats().session_file is None

    async def test_a_turn_is_counted(self, faux: Any):
        faux.set_responses([faux_assistant_message("hello back")])
        session = _session(faux)
        await session.prompt("hello")
        await _run_until_idle(session)

        stats = session.get_session_stats()
        assert stats.user_messages == 1
        assert stats.assistant_messages == 1
        assert stats.total_messages == 2
        assert stats.tokens.total > 0, "a completed turn reported no tokens"

    def test_it_carries_the_context_usage(self, faux: Any):
        session = _session(faux)
        stats = session.get_session_stats()
        assert stats.context_usage is not None
        assert stats.context_usage.context_window > 0


class _Registry:
    """A model registry over a fixed list. What 7.11 will supply for real."""

    def __init__(self, models: list[Any], *, authed: bool = True) -> None:
        self.models = models
        self._authed = authed
        self.refreshes = 0

    def has_configured_auth(self, model: Any) -> bool:
        return self._authed

    def is_using_oauth(self, model: Any) -> bool:
        return False

    async def get_api_key_and_headers(self, model: Any) -> ResolvedRequestAuth:
        if self._authed:
            return ResolvedRequestAuth(ok=True, api_key="k")
        return ResolvedRequestAuth(ok=False, error=f'No API key found for "{model.provider}"')

    def refresh(self) -> None:
        self.refreshes += 1

    async def get_available(self) -> list[Any]:
        return list(self.models)


def _reasoning_model(faux: Any) -> Any:
    """The faux model with reasoning on, so thinking levels have somewhere to go."""
    return faux.get_model().model_copy(update={"reasoning": True})


class TestSetModel:
    async def test_it_moves_the_agent_onto_the_model(self, faux: Any):
        other = faux.get_model().model_copy(update={"id": "faux-2"})
        session = _session(faux, model_registry=_Registry([faux.get_model(), other]))
        await session.set_model(other)
        assert session.model is other

    async def test_it_records_the_switch_in_the_settings(self, faux: Any):
        other = faux.get_model().model_copy(update={"id": "faux-2"})
        settings = _settings()
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=settings,
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
            model_registry=_Registry([faux.get_model(), other]),
        ).session
        await session.set_model(other)
        assert settings.get_default_model() == "faux-2"
        assert settings.get_default_provider() == other.provider

    async def test_a_model_with_no_auth_is_refused(self, faux: Any):
        session = _session(faux, model_registry=_Registry([faux.get_model()], authed=False))
        before = session.model
        with pytest.raises(RuntimeError, match="No API key"):
            await session.set_model(faux.get_model())
        assert session.model is before, "a refused switch still moved the agent"

    async def test_without_a_registry_the_switch_is_allowed(self, faux: Any):
        """There is nothing to check against, and refusing everything would make
        the selector untestable rather than safe."""
        other = faux.get_model().model_copy(update={"id": "faux-2"})
        session = _session(faux)
        await session.set_model(other)
        assert session.model is other


class TestCycleModel:
    async def test_one_model_is_not_a_cycle(self, faux: Any):
        session = _session(faux, model_registry=_Registry([faux.get_model()]))
        assert await session.cycle_model("forward") is None

    async def test_forward_steps_to_the_next_model(self, faux: Any):
        other = faux.get_model().model_copy(update={"id": "faux-2"})
        session = _session(faux, model_registry=_Registry([faux.get_model(), other]))
        result = await session.cycle_model("forward")
        assert result is not None
        assert result.model.id == "faux-2"
        assert result.is_scoped is False

    async def test_backward_wraps_around(self, faux: Any):
        other = faux.get_model().model_copy(update={"id": "faux-2"})
        session = _session(faux, model_registry=_Registry([faux.get_model(), other]))
        result = await session.cycle_model("backward")
        assert result is not None
        assert result.model.id == "faux-2", "backward from the first model must wrap to the last"

    async def test_from_a_model_outside_the_list_it_steps_onto_the_second(self, faux: Any):
        """The TS treats "not found" as index 0 and then steps, so the first
        cycle lands on the list's *second* entry. Treating it as -1 instead would
        land on the first and make one model unreachable by cycling forward."""
        first = faux.get_model().model_copy(update={"id": "first"})
        second = faux.get_model().model_copy(update={"id": "second"})
        session = _session(faux, model_registry=_Registry([first, second]))
        session.agent.state.model = faux.get_model().model_copy(update={"id": "stranger"})

        result = await session.cycle_model("forward")
        assert result is not None
        assert result.model.id == "second"

    async def test_scoped_models_win_over_the_registry(self, faux: Any):
        from cortex.code.session import ScopedModel

        scoped_a = faux.get_model().model_copy(update={"id": "scoped-a"})
        scoped_b = faux.get_model().model_copy(update={"id": "scoped-b"})
        session = _session(
            faux,
            model_registry=_Registry([faux.get_model()]),
            scoped_models=[ScopedModel(scoped_a), ScopedModel(scoped_b)],
        )
        result = await session.cycle_model("forward")
        assert result is not None
        assert result.is_scoped is True
        assert result.model.id in {"scoped-a", "scoped-b"}

    async def test_a_scoped_model_can_pin_a_thinking_level(self, faux: Any):
        from cortex.code.session import ScopedModel

        thinker = _reasoning_model(faux).model_copy(update={"id": "thinker"})
        other = _reasoning_model(faux).model_copy(update={"id": "other"})
        session = _session(
            faux,
            scoped_models=[ScopedModel(other), ScopedModel(thinker, "high")],
        )
        # Land on `thinker`, whose pattern pinned "high".
        result = await session.cycle_model("forward")
        while result is not None and result.model.id != "thinker":
            result = await session.cycle_model("forward")
        assert result is not None
        assert result.thinking_level == "high"


class TestThinkingLevel:
    def test_a_model_without_reasoning_offers_only_off(self, faux: Any):
        assert _session(faux).get_available_thinking_levels() == ["off"]

    def test_a_reasoning_model_offers_the_range(self, faux: Any):
        session = _session(faux)
        session.agent.state.model = _reasoning_model(faux)
        assert session.get_available_thinking_levels() == [
            "off",
            "minimal",
            "low",
            "medium",
            "high",
        ]

    def test_a_level_the_model_cannot_do_is_clamped(self, faux: Any):
        session = _session(faux)
        session.set_thinking_level("high")
        assert session.thinking_level == "off", "a non-reasoning model took a thinking level"

    def test_setting_the_level_records_it(self, faux: Any):
        settings = _settings()
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=settings,
            session_manager=SessionManager("/w/project", "", persist=False),
            model=_reasoning_model(faux),
        ).session
        session.set_thinking_level("medium")
        assert session.thinking_level == "medium"
        assert settings.get_default_thinking_level() == "medium"

    def test_setting_the_same_level_twice_records_once(self, faux: Any):
        session = _session(faux)
        session.agent.state.model = _reasoning_model(faux)
        session.set_thinking_level("low")
        before = len(session.session_manager.get_entries())
        session.set_thinking_level("low")
        assert len(session.session_manager.get_entries()) == before

    def test_a_change_is_announced(self, faux: Any):
        session = _session(faux)
        session.agent.state.model = _reasoning_model(faux)
        events: list[dict[str, Any]] = []
        session.subscribe(lambda event: events.append(event))
        session.set_thinking_level("high")
        assert {"type": "thinking_level_changed", "level": "high"} in events

    def test_cycling_without_reasoning_answers_nothing(self, faux: Any):
        assert _session(faux).cycle_thinking_level() is None

    def test_cycling_steps_through_the_levels(self, faux: Any):
        session = _session(faux)
        session.agent.state.model = _reasoning_model(faux)
        assert session.cycle_thinking_level() == "minimal"
        assert session.cycle_thinking_level() == "low"

    async def test_a_switch_back_to_a_thinker_restores_the_stored_level(self, faux: Any):
        """A model that cannot think clamps the level to `off`. Carrying *that*
        across the next switch would strand the session on `off` for good, so
        what comes back is the stored default — the level the user last chose
        deliberately."""
        settings = _settings()
        thinker = _reasoning_model(faux)
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=settings,
            session_manager=SessionManager("/w/project", "", persist=False),
            model=thinker,
        ).session
        session.set_thinking_level("high")

        plain = faux.get_model().model_copy(update={"id": "plain"})
        await session.set_model(plain)
        assert session.thinking_level == "off", "a model without reasoning kept a level"

        await session.set_model(_reasoning_model(faux).model_copy(update={"id": "thinker-2"}))
        assert session.thinking_level == "high"


class TestQueueModes:
    def test_steering_mode_reaches_the_agent_and_the_settings(self, faux: Any):
        settings = _settings()
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=settings,
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
        ).session
        session.set_steering_mode("one-at-a-time")
        assert session.steering_mode == "one-at-a-time"
        assert settings.get_steering_mode() == "one-at-a-time"

    def test_follow_up_mode_reaches_the_agent_and_the_settings(self, faux: Any):
        settings = _settings()
        session = create_agent_session(
            cwd="/w/project",
            settings_manager=settings,
            session_manager=SessionManager("/w/project", "", persist=False),
            model=faux.get_model(),
        ).session
        session.set_follow_up_mode("one-at-a-time")
        assert session.follow_up_mode == "one-at-a-time"
        assert settings.get_follow_up_mode() == "one-at-a-time"
