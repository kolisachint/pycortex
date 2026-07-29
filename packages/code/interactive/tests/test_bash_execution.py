"""Tests for the `!command` block and the controller that drives it."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from cortex.code.interactive.bash_execution_controller import BashExecutionController
from cortex.code.interactive.components.bash_execution import (
    PREVIEW_LINES,
    BashExecutionComponent,
)
from cortex.tui.render import Container


def plain(lines: list[str]) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(line.rstrip() for line in lines))


class FakeUI:
    """Enough of a TUI for the loader to hold and the controller to poke."""

    def __init__(self) -> None:
        self.renders = 0

    def request_render(self, force: bool = False) -> None:
        self.renders += 1


def component(command: str = "ls -la", **kwargs: Any) -> BashExecutionComponent:
    return BashExecutionComponent(command, FakeUI(), **kwargs)


class TestBashExecutionComponent:
    def test_shows_the_command_with_a_prompt(self):
        assert "$ ls -la" in plain(component().render(80))

    def test_output_appears_as_it_arrives(self):
        block = component()
        block.append_output("first\n")
        assert "first" in plain(block.render(80))

    def test_a_chunk_that_ends_mid_line_continues_that_line(self):
        # A shell writes when it writes; treating each chunk as whole lines
        # would turn a progress bar into a paragraph.
        block = component()
        block.append_output("half")
        block.append_output(" a line\n")
        assert "half a line" in plain(block.render(80))

    def test_ansi_from_the_command_is_stripped(self):
        block = component()
        block.append_output("\x1b[31mred\x1b[0m\n")
        # The *raw* lines, not the stripped ones: a command painting its own
        # colours would otherwise fight the theme, and the block's own styling
        # would be the thing that lost.
        assert "red" in plain(block.render(80))
        assert "\x1b[31m" not in "".join(block.render(80))
        assert block.get_output().strip() == "red"

    def test_carriage_returns_become_newlines(self):
        block = component()
        block.append_output("one\r\ntwo\rthree\n")
        assert plain(block.render(80)).count("t") >= 2
        assert block.get_output().splitlines()[:3] == ["one", "two", "three"]

    def test_a_clean_exit_reports_nothing_extra(self):
        block = component()
        block.append_output("done\n")
        block.set_complete(0, False)
        rendered = plain(block.render(80))
        assert "exit" not in rendered
        assert "cancelled" not in rendered

    def test_a_failure_shows_its_exit_code(self):
        block = component()
        block.set_complete(2, False)
        assert "(exit 2)" in plain(block.render(80))

    def test_a_cancelled_command_says_so(self):
        block = component()
        block.set_complete(None, True)
        assert "(cancelled)" in plain(block.render(80))

    def test_a_nonzero_exit_on_a_cancelled_command_still_reads_as_cancelled(self):
        block = component()
        block.set_complete(130, True)
        rendered = plain(block.render(80))
        assert "(cancelled)" in rendered
        assert "(exit 130)" not in rendered

    def test_only_the_tail_shows_while_collapsed(self):
        block = component()
        block.append_output("\n".join(f"line {i}" for i in range(1, 60)) + "\n")
        block.set_complete(0, False)
        rendered = plain(block.render(80))
        assert "line 59" in rendered
        assert "line 1\n" not in rendered
        assert "more lines" in rendered

    def test_expanding_shows_the_rest(self):
        block = component()
        block.append_output("\n".join(f"line {i}" for i in range(1, 60)) + "\n")
        block.set_complete(0, False)
        block.set_expanded(True)
        rendered = plain(block.render(80))
        assert "line 1" in rendered
        assert "line 59" in rendered

    def test_short_output_is_never_advertised_as_truncated(self):
        block = component()
        block.append_output("only this\n")
        block.set_complete(0, False)
        assert "more lines" not in plain(block.render(80))

    def test_the_preview_is_counted_in_visual_lines(self):
        # One logical line that wraps four times costs four of the preview's
        # rows, not one — the whole reason the truncation renders to count.
        block = component()
        block.append_output("\n".join("x" * 200 for _ in range(10)) + "\n")
        block.set_complete(0, False)
        assert len(block.render(40)) <= PREVIEW_LINES + 6

    def test_a_truncated_run_points_at_the_full_output(self):
        block = component()
        block.append_output("\n".join(f"line {i}" for i in range(1, 3000)) + "\n")
        block.set_complete(0, False, None, "/tmp/full.log")
        assert "/tmp/full.log" in plain(block.render(80))

    def test_the_raw_output_is_kept_for_the_session(self):
        block = component()
        block.append_output("one\ntwo\n")
        assert block.get_output() == "one\ntwo\n"
        assert block.get_command() == "ls -la"


class FakeSession:
    def __init__(self, *, streaming: bool = False, fail: bool = False) -> None:
        self.is_streaming = streaming
        self.fail = fail
        self.recorded: list[tuple[str, bool]] = []

    async def execute_bash(
        self,
        command: str,
        on_chunk: Any = None,
        *,
        exclude_from_context: bool = False,
    ) -> Any:
        if self.fail:
            raise RuntimeError("no such shell")
        if on_chunk is not None:
            on_chunk("streamed output\n")
        self.recorded.append((command, exclude_from_context))

        class Result:
            exit_code = 0
            cancelled = False
            truncated = False
            full_output_path = None

        return Result()


class FakeDeps:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.ui = FakeUI()
        self.pending_messages_container = Container()
        self.chat_container = Container()
        self.errors: list[str] = []

    def show_error(self, error_message: str) -> None:
        self.errors.append(error_message)


class TestBashExecutionController:
    def test_an_idle_session_puts_the_block_straight_in_the_chat(self):
        deps = FakeDeps(FakeSession())
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi"))
        assert len(deps.chat_container.children) == 1
        assert not deps.pending_messages_container.children

    def test_a_streaming_session_parks_the_block_in_the_pending_area(self):
        deps = FakeDeps(FakeSession(streaming=True))
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi"))
        # Mid-turn the transcript is not a safe place for a bash row: it would
        # land between a tool call and its result.
        assert len(deps.pending_messages_container.children) == 1
        assert not deps.chat_container.children

    def test_flushing_moves_parked_blocks_into_the_chat(self):
        deps = FakeDeps(FakeSession(streaming=True))
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi"))
        controller.flush_pending_bash_components()
        assert not deps.pending_messages_container.children
        assert len(deps.chat_container.children) == 1

    def test_flushing_twice_moves_nothing_twice(self):
        deps = FakeDeps(FakeSession(streaming=True))
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi"))
        controller.flush_pending_bash_components()
        controller.flush_pending_bash_components()
        assert len(deps.chat_container.children) == 1

    def test_the_output_streams_into_the_block(self):
        deps = FakeDeps(FakeSession())
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi"))
        block = deps.chat_container.children[0]
        assert isinstance(block, BashExecutionComponent)
        assert "streamed output" in plain(block.render(80))

    def test_exclude_from_context_reaches_the_session(self):
        session = FakeSession()
        controller = BashExecutionController(FakeDeps(session))  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi", True))
        assert session.recorded == [("echo hi", True)]

    def test_a_failure_becomes_an_error_line_and_a_finished_block(self):
        deps = FakeDeps(FakeSession(fail=True))
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("nope"))
        assert deps.errors and "Bash command failed" in deps.errors[0]
        block = deps.chat_container.children[0]
        assert isinstance(block, BashExecutionComponent)
        # Not left spinning: a block whose loader never stops holds a timer on
        # the loop for the rest of the session.
        assert block.status != "running"

    def test_the_controller_holds_no_block_between_commands(self):
        deps = FakeDeps(FakeSession())
        controller = BashExecutionController(deps)  # type: ignore[arg-type]
        asyncio.run(controller.handle_bash_command("echo hi"))
        assert controller.bash_component is None


@pytest.mark.parametrize("cancelled", [True, False])
def test_completion_stops_the_spinner(cancelled: bool):
    block = component()
    block.set_complete(0, cancelled)
    assert block.status in ("complete", "cancelled")
