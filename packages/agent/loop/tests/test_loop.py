"""Tests for agent loop module.

The loop's contract is the sequence of events one turn produces and the message
it leaves behind, so these drive it against `ai/provider-faux` rather than
checking that a stub raises.
"""

from __future__ import annotations

from typing import Any

import pytest
from cortex.agent.loop import AgentLoop, run_agent_loop, run_agent_loop_continue
from cortex.agent.types import AgentContext, AgentLoopConfig
from cortex.ai.providers.faux import (
    faux_assistant_message,
    faux_tool_call,
    register_faux_provider,
)
from cortex.ai.types import TextContent, UserMessage


@pytest.fixture
def faux():
    registration = register_faux_provider()
    yield registration
    registration.unregister()


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], timestamp=0)


#: The schema every echo-shaped tool in these tests validates against. Written
#: out rather than left empty: an empty schema accepts anything, and half of
#: what `_prepare_tool_call` does is decide what to reject.
_ECHO_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}


def _echo_tool(executed: list[Any]) -> Any:
    """The TS tests' `echo` tool: records what it was called with, echoes it back."""
    from cortex.agent.types import AgentTool, AgentToolResult

    async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
        executed.append(params["value"])
        return AgentToolResult(
            content=[TextContent(text=f"echoed: {params['value']}")],
            details={"value": params["value"]},
        )

    return AgentTool(
        name="echo",
        description="Echo tool",
        label="Echo",
        parameters=_ECHO_SCHEMA,
        execute=execute,
    )


def _config(faux: Any) -> AgentLoopConfig:
    return AgentLoopConfig(model=faux.get_model())


class _Sink:
    """Records every event the loop emits, in order."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


def _text_of(message: Any) -> str:
    """The text blocks of a message, joined.

    Written as a helper rather than reaching into `content[0]` because the
    transcript is a union of message types and pyright is right to say so.
    """
    return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


def _tool_results(messages: list[Any]) -> list[Any]:
    """The tool-result messages of a run.

    `run_agent_loop` returns the `Message` union, and only one arm of it carries
    `tool_call_id` — so the narrowing has to happen somewhere, and a helper the
    tests read through beats a `cast` at every call site.
    """
    return [m for m in messages if getattr(m, "role", "") == "toolResult"]


def _stop_reason(message: Any) -> str:
    return str(getattr(message, "stop_reason", ""))


def _error_message(message: Any) -> str:
    return str(getattr(message, "error_message", ""))


# ---------------------------------------------------------------------------
# AgentLoop class tests
# ---------------------------------------------------------------------------


class TestAgentLoop:
    def test_agent_loop_creation(self) -> None:
        """AgentLoop can be created with config and context."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)
        assert loop.config is config
        assert loop.context is context

    def test_agent_loop_default_context(self) -> None:
        """AgentLoop creates default context if not provided."""
        config = AgentLoopConfig()
        loop = AgentLoop(config=config)
        assert loop.context is not None
        assert loop.context.system_prompt == ""

    async def test_agent_loop_run_continue_empty_context(self) -> None:
        """AgentLoop.run_continue raises on empty context."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)

        with pytest.raises(ValueError, match="Cannot continue: no messages"):
            await loop.run_continue()

    async def test_agent_loop_run_continue_rejects_assistant_last(self, faux: Any) -> None:
        context = AgentContext(system_prompt="", messages=[faux_assistant_message("done")])
        loop = AgentLoop(config=_config(faux), context=context)

        with pytest.raises(ValueError, match="Cannot continue from message role: assistant"):
            await loop.run_continue()


# ---------------------------------------------------------------------------
# One turn, end to end
# ---------------------------------------------------------------------------


class TestStreamAssistantResponse:
    async def test_a_turn_emits_the_ts_event_sequence(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("hello")])
        sink = _Sink()
        context = AgentContext(system_prompt="be brief", messages=[])

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=context,
            config=_config(faux),
            emit=sink,
        )

        assert sink.types[:4] == ["agent_start", "turn_start", "message_start", "message_end"]
        assert sink.types[-2:] == ["turn_end", "agent_end"]
        assert "message_update" in sink.types, "the deltas never reached the sink"
        assert [getattr(m, "role", "") for m in messages] == ["user", "assistant"]
        assert _text_of(messages[-1]) == "hello"

    async def test_the_prompt_and_the_answer_land_in_the_context(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("answered")])
        context = AgentContext(system_prompt="", messages=[])

        await run_agent_loop(
            prompts=[_user("question")], context=context, config=_config(faux), emit=_Sink()
        )

        roles = [getattr(m, "role", "") for m in context.messages]
        assert roles == ["user", "assistant"]
        # The partial that `start` pushed was replaced, not appended alongside.
        assert _text_of(context.messages[-1]) == "answered"

    async def test_an_error_turn_ends_the_run(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("", stop_reason="error", error_message="boom")])
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=_config(faux),
            emit=sink,
        )

        assert sink.types[-2:] == ["turn_end", "agent_end"]
        assert _stop_reason(messages[-1]) == "error"
        assert _error_message(messages[-1]) == "boom"

    async def test_an_errored_turn_ends_the_run_even_with_a_message_queued(self, faux: Any) -> None:
        """An error stops the loop where it stands — it does not steer on.

        With nothing queued, "return here" and "fall through to the end" produce
        the same events, so the queue is what makes the difference visible.
        """
        faux.set_responses(
            [
                faux_assistant_message("", stop_reason="error", error_message="boom"),
                faux_assistant_message("should never be asked for"),
            ]
        )
        queued: list[list[Any]] = [[], [_user("carry on")], []]

        async def get_steering_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_steering_messages=get_steering_messages
        )
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=sink,
        )

        assert sink.types.count("turn_start") == 1, "the loop ran another turn after an error"
        assert [getattr(m, "role", "") for m in messages] == ["user", "assistant"]
        assert faux.get_pending_response_count() == 1, "the provider was asked again"

    async def test_an_aborted_stream_ends_the_run(self, faux: Any) -> None:
        class _Signal:
            aborted = True

        faux.set_responses([faux_assistant_message("never sent")])

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=_config(faux),
            emit=_Sink(),
            signal=_Signal(),
        )

        assert _stop_reason(messages[-1]) == "aborted"

    async def test_convert_to_llm_filters_what_the_provider_sees(self, faux: Any) -> None:
        seen: list[Any] = []

        def convert(messages: list[Any]) -> list[Any]:
            seen.append(list(messages))
            return [m for m in messages if getattr(m, "role", "") == "user"]

        faux.set_responses([faux_assistant_message("ok")])
        config = AgentLoopConfig(model=faux.get_model(), convert_to_llm=convert)

        await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=_Sink(),
        )

        assert seen, "convert_to_llm was never called"

    async def test_tools_are_projected_onto_the_provider_schema(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool

        captured: list[Any] = []

        def stream_fn(model: Any, context: Any, options: Any):
            captured.append(context)
            from cortex.ai.stream import stream_simple

            return stream_simple(model, context, options)

        faux.set_responses([faux_assistant_message("ok")])
        tool = AgentTool(name="read", description="Read a file", parameters={"type": "object"})

        await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=_Sink(),
            stream_fn=stream_fn,
        )

        assert captured, "the stream function was never called"
        assert captured[0].tools is not None
        assert captured[0].tools[0].name == "read"


class TestQueues:
    async def test_a_steering_message_starts_another_turn(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])
        queued: list[list[Any]] = [[], [_user("and another thing")], []]

        async def get_steering_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_steering_messages=get_steering_messages
        )
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=sink,
        )

        texts = [_text_of(m) for m in messages if getattr(m, "role", "") == "assistant"]
        assert texts == ["first", "second"], texts
        assert sink.types.count("turn_start") == 2

    async def test_a_follow_up_message_starts_another_turn(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("first"), faux_assistant_message("second")])
        queued = [[_user("one more")], []]

        async def get_follow_up_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_follow_up_messages=get_follow_up_messages
        )

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="", messages=[]),
            config=config,
            emit=_Sink(),
        )

        texts = [_text_of(m) for m in messages if getattr(m, "role", "") == "assistant"]
        assert texts == ["first", "second"], texts


class TestToolCalls:
    """Step 7.5's half of the loop, driven the way the TS tests drive it.

    A tool call is only "handled" if the tool actually ran, its result went back
    into the transcript in the right place, and the loop asked the provider
    again — so every test here asserts on the events *and* the messages.
    """

    async def test_a_tool_call_runs_the_tool_and_answers_it(self, faux: Any) -> None:
        executed: list[Any] = []
        tool = _echo_tool(executed)
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"value": "hello"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
            ]
        )
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("echo something")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=sink,
        )

        assert executed == ["hello"]
        assert [getattr(m, "role", "") for m in messages] == [
            "user",
            "assistant",
            "toolResult",
            "assistant",
        ]
        result = _tool_results(messages)[0]
        assert result.tool_call_id == "tool-1"
        assert result.is_error is False
        assert _text_of(result) == "echoed: hello"
        starts = [e for e in sink.events if e["type"] == "tool_execution_start"]
        ends = [e for e in sink.events if e["type"] == "tool_execution_end"]
        assert [e["tool_name"] for e in starts] == ["echo"]
        assert [e["is_error"] for e in ends] == [False]

    async def test_an_unknown_tool_answers_the_call_instead_of_hanging(self, faux: Any) -> None:
        """The model asked for something that is not there; the turn must go on."""
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("nope", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("sorry about that"),
            ]
        )

        messages = await run_agent_loop(
            prompts=[_user("use a tool")],
            context=AgentContext(system_prompt="", messages=[], tools=[]),
            config=_config(faux),
            emit=_Sink(),
        )

        result = _tool_results(messages)[0]
        assert result.is_error is True
        assert _text_of(result) == "Tool nope not found"

    async def test_invalid_arguments_come_back_as_an_error_result(self, faux: Any) -> None:
        """Arguments that are *present* and wrong, not merely absent.

        An empty `{}` is falsy, and half the ways to skip validation still reject
        it by accident; a call carrying the wrong key is what tells the two apart.
        """
        executed: list[Any] = []
        tool = _echo_tool(executed)
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"valeu": "typo"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("i will try again"),
            ]
        )

        messages = await run_agent_loop(
            prompts=[_user("echo")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=_Sink(),
        )

        assert executed == [], "the tool ran on arguments that do not match its schema"
        result = _tool_results(messages)[0]
        assert result.is_error is True
        # And it was *rejected*, not merely fatal: a tool handed the wrong keys
        # throws too, and the model can do nothing with a bare `KeyError`.
        assert 'Validation failed for tool "echo"' in _text_of(result), _text_of(result)

    async def test_before_tool_call_can_block_with_its_own_reason(self, faux: Any) -> None:
        executed: list[Any] = []
        tool = _echo_tool(executed)

        async def before_tool_call(context: Any, signal: Any = None) -> Any:
            from cortex.agent.types import BeforeToolCallResult

            return BeforeToolCallResult(block=True, reason="not on my watch")

        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"value": "hi"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("understood"),
            ]
        )
        config = AgentLoopConfig(model=faux.get_model(), before_tool_call=before_tool_call)

        messages = await run_agent_loop(
            prompts=[_user("echo")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        assert executed == [], "a blocked tool ran anyway"
        assert _text_of(messages[2]) == "not on my watch"
        assert _tool_results(messages)[0].is_error is True

    async def test_before_tool_call_mutations_reach_the_tool_unrevalidated(self, faux: Any) -> None:
        """The hook gets the validated args object and may edit it in place.

        The TS revalidates nothing after the hook, so a mutation that would fail
        the schema still executes — which is the point of the hook.
        """
        executed: list[Any] = []
        tool = _echo_tool(executed)

        async def before_tool_call(context: Any, signal: Any = None) -> Any:
            context.args["value"] = 123
            return None

        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"value": "hello"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
            ]
        )
        config = AgentLoopConfig(model=faux.get_model(), before_tool_call=before_tool_call)

        await run_agent_loop(
            prompts=[_user("echo")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        assert executed == [123]

    async def test_prepare_arguments_reshapes_the_call_before_validation(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        executed: list[Any] = []

        def prepare_arguments(args: Any) -> Any:
            if not isinstance(args, dict) or "old_text" not in args:
                return args
            return {"edits": [{"old_text": args["old_text"], "new_text": args["new_text"]}]}

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            executed.append(params["edits"])
            return AgentToolResult(
                content=[_Text(text=f"edited {len(params['edits'])}")],
                details={"count": len(params["edits"])},
            )

        tool = AgentTool(
            name="edit",
            description="Edit tool",
            label="Edit",
            parameters={
                "type": "object",
                "properties": {"edits": {"type": "array"}},
                "required": ["edits"],
            },
            prepare_arguments=prepare_arguments,
            execute=execute,
        )

        faux.set_responses(
            [
                faux_assistant_message(
                    [
                        faux_tool_call(
                            "edit",
                            {"old_text": "before", "new_text": "after"},
                            {"id": "tool-1"},
                        )
                    ],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
            ]
        )

        await run_agent_loop(
            prompts=[_user("edit")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=_Sink(),
        )

        assert executed == [[{"old_text": "before", "new_text": "after"}]]

    async def test_a_streaming_tool_reports_partial_results_as_it_runs(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            on_update(AgentToolResult(content=[_Text(text="line one")], details={}))
            on_update(AgentToolResult(content=[_Text(text="line two")], details={}))
            return AgentToolResult(content=[_Text(text="line one\nline two")], details={})

        tool = AgentTool(
            name="stream",
            description="Streams",
            label="Stream",
            parameters={"type": "object"},
            execute=execute,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("stream", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("done"),
            ]
        )
        sink = _Sink()

        await run_agent_loop(
            prompts=[_user("stream")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=sink,
        )

        updates = [e for e in sink.events if e["type"] == "tool_execution_update"]
        assert [_text_of(e["partial_result"]) for e in updates] == ["line one", "line two"]
        # Every update lands before the execution it belongs to is reported done.
        assert sink.types.index("tool_execution_update") < sink.types.index("tool_execution_end")

    async def test_parallel_calls_end_in_completion_order_and_answer_in_source_order(
        self, faux: Any
    ) -> None:
        """Two orders, both deliberate, and only one of them is the transcript's."""
        import asyncio

        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        release_first = asyncio.Event()

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            if params["value"] == "first":
                await release_first.wait()
            else:
                release_first.set()
            return AgentToolResult(content=[_Text(text=params["value"])], details={})

        tool = AgentTool(
            name="echo",
            description="Echo tool",
            label="Echo",
            parameters=_ECHO_SCHEMA,
            execute=execute,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [
                        faux_tool_call("echo", {"value": "first"}, {"id": "tool-1"}),
                        faux_tool_call("echo", {"value": "second"}, {"id": "tool-2"}),
                    ],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
            ]
        )
        sink = _Sink()

        messages = await run_agent_loop(
            prompts=[_user("both")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=sink,
        )

        ends = [e["tool_call_id"] for e in sink.events if e["type"] == "tool_execution_end"]
        assert ends == ["tool-2", "tool-1"], "execution ends are not in completion order"
        results = [m.tool_call_id for m in _tool_results(messages)]
        assert results == ["tool-1", "tool-2"], "tool results are not in assistant source order"

    async def test_one_sequential_tool_forces_the_whole_batch_sequential(self, faux: Any) -> None:
        """`executionMode: "sequential"` is a property of the tool, not the batch."""
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        running = 0
        max_concurrent = 0

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            nonlocal running, max_concurrent
            running += 1
            max_concurrent = max(max_concurrent, running)
            await asyncio.sleep(0)
            running -= 1
            return AgentToolResult(content=[_Text(text=params["value"])], details={})

        import asyncio

        parallel_tool = AgentTool(
            name="echo",
            description="Echo",
            label="Echo",
            parameters=_ECHO_SCHEMA,
            execute=execute,
            execution_mode="parallel",
        )
        sequential_tool = AgentTool(
            name="serial",
            description="Serial",
            label="Serial",
            parameters=_ECHO_SCHEMA,
            execute=execute,
            execution_mode="sequential",
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [
                        faux_tool_call("echo", {"value": "a"}, {"id": "tool-1"}),
                        faux_tool_call("serial", {"value": "b"}, {"id": "tool-2"}),
                    ],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
            ]
        )

        await run_agent_loop(
            prompts=[_user("both")],
            context=AgentContext(
                system_prompt="", messages=[], tools=[parallel_tool, sequential_tool]
            ),
            config=_config(faux),
            emit=_Sink(),
        )

        assert max_concurrent == 1, "the sequential tool ran alongside another"

    async def test_a_batch_that_all_terminates_stops_the_loop(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            return AgentToolResult(content=[_Text(text="bye")], details={}, terminate=True)

        tool = AgentTool(
            name="stop",
            description="Stops",
            label="Stop",
            parameters={"type": "object"},
            execute=execute,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("stop", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("should never be asked for"),
            ]
        )
        sink = _Sink()

        await run_agent_loop(
            prompts=[_user("stop")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=sink,
        )

        assert sink.types.count("turn_start") == 1, "the loop ran another turn after terminate"
        assert faux.get_pending_response_count() == 1, "the provider was asked again"

    async def test_one_terminating_result_in_a_batch_is_not_enough(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            return AgentToolResult(
                content=[_Text(text=params["value"])],
                details={},
                terminate=params["value"] == "stop",
            )

        tool = AgentTool(
            name="echo",
            description="Echo",
            label="Echo",
            parameters=_ECHO_SCHEMA,
            execute=execute,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [
                        faux_tool_call("echo", {"value": "stop"}, {"id": "tool-1"}),
                        faux_tool_call("echo", {"value": "go"}, {"id": "tool-2"}),
                    ],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("carried on"),
            ]
        )
        sink = _Sink()

        await run_agent_loop(
            prompts=[_user("mixed")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=sink,
        )

        assert sink.types.count("turn_start") == 2

    async def test_after_tool_call_overrides_the_result_field_by_field(self, faux: Any) -> None:
        from cortex.agent.types import AfterToolCallResult
        from cortex.ai.types import TextContent as _Text

        executed: list[Any] = []
        tool = _echo_tool(executed)

        async def after_tool_call(context: Any, signal: Any = None) -> Any:
            return AfterToolCallResult(content=[_Text(text="redacted")], is_error=True)

        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"value": "secret"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
            ]
        )
        config = AgentLoopConfig(model=faux.get_model(), after_tool_call=after_tool_call)

        messages = await run_agent_loop(
            prompts=[_user("echo")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        assert executed == ["secret"], "the hook runs after the tool, not instead of it"
        result = _tool_results(messages)[0]
        assert _text_of(result) == "redacted"
        assert result.is_error is True
        # `details` was not in the override, so the tool's own value survived.
        assert result.details == {"value": "secret"}

    async def test_an_after_tool_call_that_throws_becomes_the_result(self, faux: Any) -> None:
        """The hook's contract says it must not throw. It gets to be wrong anyway."""
        executed: list[Any] = []
        tool = _echo_tool(executed)

        async def after_tool_call(context: Any, signal: Any = None) -> Any:
            raise RuntimeError("the hook fell over")

        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"value": "hi"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("carried on"),
            ]
        )
        config = AgentLoopConfig(model=faux.get_model(), after_tool_call=after_tool_call)

        messages = await run_agent_loop(
            prompts=[_user("echo")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        result = _tool_results(messages)[0]
        assert _text_of(result) == "the hook fell over"
        assert result.is_error is True

    async def test_a_steering_message_waits_for_the_tool_batch(self, faux: Any) -> None:
        executed: list[Any] = []
        tool = _echo_tool(executed)
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("echo", {"value": "hi"}, {"id": "tool-1"})],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("after the tool"),
            ]
        )
        queued: list[list[Any]] = [[], [_user("and one more thing")], []]

        async def get_steering_messages() -> list[Any]:
            return queued.pop(0) if queued else []

        config = AgentLoopConfig(
            model=faux.get_model(), get_steering_messages=get_steering_messages
        )

        messages = await run_agent_loop(
            prompts=[_user("echo")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        roles = [getattr(m, "role", "") for m in messages]
        assert roles == ["user", "assistant", "toolResult", "user", "assistant"], roles


class TestBackgroundTools:
    """A background tool answers its call at once and reports back a turn later."""

    async def test_a_background_tool_does_not_block_the_turn(self, faux: Any) -> None:
        import asyncio

        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        release = asyncio.Event()

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            await release.wait()
            return AgentToolResult(content=[_Text(text="the slow answer")], details={})

        tool = AgentTool(
            name="slow",
            description="Slow",
            label="Slow",
            parameters={"type": "object"},
            execute=execute,
            background=True,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("slow", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("started it"),
                faux_assistant_message("and here is what it said"),
            ]
        )

        async def run() -> list[Any]:
            return await run_agent_loop(
                prompts=[_user("go")],
                context=AgentContext(system_prompt="", messages=[], tools=[tool]),
                config=_config(faux),
                emit=_Sink(),
            )

        task = asyncio.ensure_future(run())
        # The turn answers the tool call without waiting for the tool: the loop
        # gets far enough to ask the provider a second time while it is still
        # blocked. Then the tool finishes and its result comes back as a message.
        for _ in range(50):
            await asyncio.sleep(0)
        assert not task.done(), "the loop finished before the background tool did"
        release.set()
        messages = await task

        placeholder = _tool_results(messages)[0]
        assert "in the background" in _text_of(placeholder)
        follow_up = [m for m in messages if getattr(m, "role", "") == "user"][-1]
        assert "the slow answer" in _text_of(follow_up)
        assert 'Background tool "slow"' in _text_of(follow_up)

    async def test_background_and_foreground_results_keep_source_order(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            return AgentToolResult(content=[_Text(text=tool_call_id)], details={})

        background_tool = AgentTool(
            name="bg",
            description="Background",
            label="Background",
            parameters={"type": "object"},
            execute=execute,
            background=True,
        )
        foreground_tool = AgentTool(
            name="fg",
            description="Foreground",
            label="Foreground",
            parameters={"type": "object"},
            execute=execute,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [
                        faux_tool_call("fg", {}, {"id": "tool-1"}),
                        faux_tool_call("bg", {}, {"id": "tool-2"}),
                        faux_tool_call("fg", {}, {"id": "tool-3"}),
                    ],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
                faux_assistant_message("and done again"),
            ]
        )

        messages = await run_agent_loop(
            prompts=[_user("mixed")],
            context=AgentContext(
                system_prompt="", messages=[], tools=[background_tool, foreground_tool]
            ),
            config=_config(faux),
            emit=_Sink(),
        )

        results = [m.tool_call_id for m in _tool_results(messages)]
        assert results == ["tool-1", "tool-2", "tool-3"], results

    async def test_a_background_tool_that_fails_says_so_in_its_follow_up(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            raise RuntimeError("it went wrong in the background")

        tool = AgentTool(
            name="bg",
            description="Background",
            label="Background",
            parameters={"type": "object"},
            execute=execute,
            background=True,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("bg", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("started"),
                faux_assistant_message("oh dear"),
            ]
        )

        messages = await run_agent_loop(
            prompts=[_user("go")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=_Sink(),
        )

        follow_up = [m for m in messages if getattr(m, "role", "") == "user"][-1]
        assert 'Background tool "bg" (id tool-1) failed:' in _text_of(follow_up)
        assert "it went wrong in the background" in _text_of(follow_up)

    async def test_a_background_predicate_decides_per_call(self, faux: Any) -> None:
        """`background` may be a function of the arguments, and it may throw."""
        from cortex.agent.types import AgentTool, AgentToolResult

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            return AgentToolResult(content=[TextContent(text=params["value"])], details={})

        def background(tool_call: Any) -> bool:
            if tool_call.arguments["value"] == "boom":
                raise RuntimeError("a predicate that cannot decide")
            return tool_call.arguments["value"] == "detached"

        tool = AgentTool(
            name="echo",
            description="Echo",
            label="Echo",
            parameters=_ECHO_SCHEMA,
            execute=execute,
            background=background,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [
                        faux_tool_call("echo", {"value": "detached"}, {"id": "tool-1"}),
                        faux_tool_call("echo", {"value": "boom"}, {"id": "tool-2"}),
                    ],
                    stop_reason="toolUse",
                ),
                faux_assistant_message("done"),
                faux_assistant_message("and done again"),
            ]
        )

        messages = await run_agent_loop(
            prompts=[_user("both")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=_config(faux),
            emit=_Sink(),
        )

        results = {m.tool_call_id: _text_of(m) for m in _tool_results(messages)}
        assert "in the background" in results["tool-1"], "the predicate did not detach the call"
        # A throwing predicate must not break dispatch: the call runs in the
        # foreground and answers with the tool's own result.
        assert results["tool-2"] == "boom"

    async def test_create_background_placeholder_writes_the_holding_line(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            return AgentToolResult(content=[TextContent(text="eventually")], details={})

        tool = AgentTool(
            name="bg",
            description="Background",
            label="Background",
            parameters={"type": "object"},
            execute=execute,
            background=True,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("bg", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("started"),
                faux_assistant_message("finished"),
            ]
        )

        def create_background_placeholder(tool_call: Any) -> str:
            return f"Delegating to {tool_call.name}"

        config = AgentLoopConfig(
            model=faux.get_model(),
            create_background_placeholder=create_background_placeholder,
        )

        messages = await run_agent_loop(
            prompts=[_user("go")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        assert _text_of(_tool_results(messages)[0]) == "Delegating to bg"

    async def test_create_background_result_message_shapes_the_follow_up(self, faux: Any) -> None:
        from cortex.agent.types import AgentTool, AgentToolResult
        from cortex.ai.types import TextContent as _Text

        async def execute(tool_call_id: str, params: Any, signal: Any, on_update: Any) -> Any:
            return AgentToolResult(content=[_Text(text="raw")], details={})

        def create_background_result_message(result: Any) -> Any:
            return UserMessage(
                content=[TextContent(text=f"custom: {result.tool_call.name}")], timestamp=0
            )

        tool = AgentTool(
            name="bg",
            description="Background",
            label="Background",
            parameters={"type": "object"},
            execute=execute,
            background=True,
        )
        faux.set_responses(
            [
                faux_assistant_message(
                    [faux_tool_call("bg", {}, {"id": "tool-1"})], stop_reason="toolUse"
                ),
                faux_assistant_message("started"),
                faux_assistant_message("finished"),
            ]
        )
        config = AgentLoopConfig(
            model=faux.get_model(),
            create_background_result_message=create_background_result_message,
        )

        messages = await run_agent_loop(
            prompts=[_user("go")],
            context=AgentContext(system_prompt="", messages=[], tools=[tool]),
            config=config,
            emit=_Sink(),
        )

        follow_up = [m for m in messages if getattr(m, "role", "") == "user"][-1]
        assert _text_of(follow_up) == "custom: bg"


# ---------------------------------------------------------------------------
# Public API tests
# ---------------------------------------------------------------------------


class TestPublicAPI:
    async def test_run_agent_loop(self, faux: Any) -> None:
        """run_agent_loop creates and runs an AgentLoop."""
        faux.set_responses([faux_assistant_message("ok")])

        messages = await run_agent_loop(
            prompts=[_user("hi")],
            context=AgentContext(system_prompt="test", messages=[]),
            config=_config(faux),
            emit=_Sink(),
        )

        assert len(messages) == 2

    async def test_run_agent_loop_continue(self) -> None:
        """run_agent_loop_continue creates and runs an AgentLoop."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])

        with pytest.raises(ValueError, match="Cannot continue: no messages"):
            await run_agent_loop_continue(
                context=context,
                config=config,
            )

    async def test_run_agent_loop_continue_answers_a_pending_user_message(self, faux: Any) -> None:
        faux.set_responses([faux_assistant_message("continued")])
        context = AgentContext(system_prompt="", messages=[_user("half a conversation")])

        messages = await run_agent_loop_continue(
            context=context, config=_config(faux), emit=_Sink()
        )

        assert [getattr(m, "role", "") for m in messages] == ["assistant"]
        assert _text_of(messages[0]) == "continued"
