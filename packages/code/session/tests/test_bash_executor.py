"""Tests for the session's bash executor and the session methods over it."""

from __future__ import annotations

import asyncio
from typing import Any

from cortex.code.session.bash_executor import (
    execute_bash_with_operations,
    sanitize_binary_output,
    strip_ansi,
)


class FakeOperations:
    """A shell that prints what it was told to, then exits."""

    def __init__(
        self, chunks: list[bytes], exit_code: int | None = 0, raises: bool = False
    ) -> None:
        self.chunks = chunks
        self.exit_code = exit_code
        self.raises = raises
        self.commands: list[str] = []
        self.cwds: list[str] = []

    def exec(self, command: str, cwd: str, *, on_data: Any, signal: Any = None, **_: Any) -> Any:
        self.commands.append(command)
        self.cwds.append(cwd)
        for chunk in self.chunks:
            on_data(chunk)
        if self.raises:
            raise RuntimeError("aborted")

        class Result:
            exit_code = self.exit_code

        return Result()


def run(*args: Any, **kwargs: Any) -> Any:
    return asyncio.run(execute_bash_with_operations(*args, **kwargs))


class TestStripAnsi:
    def test_removes_colour_codes(self):
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_removes_osc_sequences(self):
        assert strip_ansi("\x1b]0;title\x07text") == "text"

    def test_leaves_plain_text_alone(self):
        assert strip_ansi("plain") == "plain"


class TestSanitizeBinaryOutput:
    def test_keeps_tab_newline_and_carriage_return(self):
        assert sanitize_binary_output("a\tb\nc\rd") == "a\tb\nc\rd"

    def test_drops_other_control_characters(self):
        assert sanitize_binary_output("a\x00b\x07c") == "abc"

    def test_drops_interlinear_annotation_marks(self):
        assert sanitize_binary_output("a￹b￻c") == "abc"

    def test_keeps_ordinary_unicode(self):
        assert sanitize_binary_output("héllo ✓") == "héllo ✓"


class TestExecuteBashWithOperations:
    def test_returns_what_the_command_printed(self):
        result = run("echo hi", "/w", FakeOperations([b"hi\n"]))
        assert "hi" in result.output
        assert result.exit_code == 0
        assert not result.cancelled

    def test_streams_every_chunk_to_the_callback(self):
        seen: list[str] = []
        run("x", "/w", FakeOperations([b"one\n", b"two\n"]), on_chunk=seen.append)
        assert seen == ["one\n", "two\n"]

    def test_a_multi_byte_character_split_across_chunks_survives(self):
        # "é" is two bytes; a decoder that ran per chunk would produce mojibake.
        seen: list[str] = []
        run("x", "/w", FakeOperations([b"h\xc3", b"\xa9llo\n"]), on_chunk=seen.append)
        assert "".join(seen) == "héllo\n"

    def test_ansi_and_carriage_returns_are_gone_from_the_output(self):
        result = run("x", "/w", FakeOperations([b"\x1b[31mred\x1b[0m\r\ndone\n"]))
        assert "\x1b[" not in result.output
        assert "\r" not in result.output

    def test_the_command_and_cwd_reach_the_operations(self):
        ops = FakeOperations([b""])
        run("echo hi", "/w/project", ops)
        assert ops.commands == ["echo hi"]
        assert ops.cwds == ["/w/project"]

    def test_a_nonzero_exit_comes_back_as_is(self):
        assert run("x", "/w", FakeOperations([b""], exit_code=3)).exit_code == 3

    def test_an_abort_returns_what_was_printed_rather_than_raising(self):
        class Signal:
            aborted = True

        result = run("x", "/w", FakeOperations([b"partial\n"], raises=True), signal=Signal())
        assert result.cancelled
        assert result.exit_code is None
        assert "partial" in result.output

    def test_a_real_failure_still_raises(self):
        class Signal:
            aborted = False

        try:
            run("x", "/w", FakeOperations([b""], raises=True), signal=Signal())
        except RuntimeError as error:
            assert "aborted" in str(error)
        else:
            raise AssertionError("the failure was swallowed")

    def test_a_flood_of_output_is_truncated_and_spilled_to_a_file(self):
        from pathlib import Path

        chunk = ("x" * 200 + "\n").encode()
        result = run("x", "/w", FakeOperations([chunk] * 5000))
        assert result.truncated
        assert result.full_output_path is not None
        assert Path(result.full_output_path).exists()
        Path(result.full_output_path).unlink()

    def test_the_spill_starts_before_the_rolling_buffer_forgets(self):
        """The temp file has to open *while* the command runs, not at the end.

        The in-memory buffer is bounded, so by the time the command exits the
        earliest output is already gone. A spill opened at the end would write a
        "full output" file that is missing its beginning.
        """
        from pathlib import Path

        lines = [f"line-{i}\n".encode() for i in range(80_000)]
        result = run("x", "/w", FakeOperations(lines))
        assert result.full_output_path is not None
        spilled = Path(result.full_output_path).read_text(encoding="utf-8")
        try:
            assert "line-0\n" in spilled, "the start of the output never reached the file"
            assert "line-0\n" not in result.output, "the rolling buffer kept everything"
        finally:
            Path(result.full_output_path).unlink()
