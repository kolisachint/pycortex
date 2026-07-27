"""ProcessTerminal against a real pty.

`start()` used to say, in a comment, that "in a real implementation, we would set
up stdin reading here". Nothing did, so the TUI rendered and then ignored every
key — invisible to a unit test that only ever asks about dimensions, and fatal to
the product. These drive the class through an actual pty because the contract is
the tty: raw mode, bytes in, SIGWINCH, and putting all of it back on the way out.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import sys
import termios
from collections.abc import Callable, Iterator
from typing import IO, Any, cast

import pytest
from cortex.tui.terminal.terminal import ProcessTerminal


class _PtyStream:
    """Stands in for `sys.stdin`/`sys.stdout`: a file object over a pty slave."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._file = os.fdopen(fd, "w", buffering=1, closefd=False)

    def fileno(self) -> int:
        return self._fd

    def write(self, data: str) -> int:
        return self._file.write(data)

    def flush(self) -> None:
        self._file.flush()


class Pty:
    """A pty pair plus the plumbing to point a ProcessTerminal at it."""

    def __init__(self) -> None:
        self.master, self.slave = pty.openpty()
        self.resize(80, 24)
        self._stream = _PtyStream(self.slave)

    def resize(self, columns: int, rows: int) -> None:
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def read_all(self) -> str:
        import select

        out = b""
        while True:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                break
            chunk = os.read(self.master, 65536)
            if not chunk:
                break
            out += chunk
        return out.decode(errors="replace")

    def close(self) -> None:
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.fixture
def fake_tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[Pty]:
    device = Pty()
    monkeypatch.setattr(sys, "stdin", cast(IO[Any], device._stream))  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(sys, "stdout", cast(IO[Any], device._stream))  # pyright: ignore[reportPrivateUsage]
    yield device
    device.close()


def _noop() -> None:
    pass


async def _settle(times: int = 4) -> None:
    """Let the loop's readability callback and the stdin buffer's timer run."""
    for _ in range(times):
        await asyncio.sleep(0.03)


class TestRawMode:
    def test_start_enables_raw_mode_and_stop_restores_it(self, fake_tty: Pty):
        before = termios.tcgetattr(fake_tty.slave)
        terminal = ProcessTerminal()
        terminal.start(lambda _data: None, _noop)
        try:
            during = termios.tcgetattr(fake_tty.slave)
            assert during[3] != before[3], "local flags unchanged — raw mode was never entered"
            assert not during[3] & termios.ICANON, "still canonical: input waits for Enter"
            assert not during[3] & termios.ISIG, "ISIG left on: Ctrl+C would be a signal, not a key"
        finally:
            terminal.stop()
        assert termios.tcgetattr(fake_tty.slave) == before

    def test_a_non_tty_stdin_is_left_alone(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
        path = tmp_path / "not-a-tty"
        path.write_text("")
        with open(path) as handle:
            monkeypatch.setattr(sys, "stdin", handle)
            terminal = ProcessTerminal()
            terminal.start(lambda _data: None, _noop)
            terminal.stop()  # must not raise


class TestInput:
    async def test_keystrokes_reach_the_handler(self, fake_tty: Pty):
        seen: list[str] = []
        terminal = ProcessTerminal()
        terminal.start(seen.append, _noop)
        try:
            fake_tty.send(b"hi")
            await _settle()
            assert seen == ["h", "i"]
        finally:
            terminal.stop()

    async def test_ctrl_c_arrives_as_a_keystroke(self, fake_tty: Pty):
        """The whole reason raw mode matters: \x03 is a key, not a SIGINT."""
        seen: list[str] = []
        terminal = ProcessTerminal()
        terminal.start(seen.append, _noop)
        try:
            fake_tty.send(b"\x03")
            await _settle()
            assert seen == ["\x03"]
        finally:
            terminal.stop()

    async def test_an_escape_sequence_arrives_as_one_event(self, fake_tty: Pty):
        seen: list[str] = []
        terminal = ProcessTerminal()
        terminal.start(seen.append, _noop)
        try:
            fake_tty.send(b"\x1b[A")
            await _settle()
            assert seen == ["\x1b[A"]
        finally:
            terminal.stop()

    async def test_a_split_multibyte_character_is_reassembled(self, fake_tty: Pty):
        """A read can land mid-codepoint; the halves must not decode separately."""
        seen: list[str] = []
        terminal = ProcessTerminal()
        terminal.start(seen.append, _noop)
        try:
            encoded = "é".encode()
            fake_tty.send(encoded[:1])
            await _settle(2)
            fake_tty.send(encoded[1:])
            await _settle()
            assert "".join(seen) == "é"
        finally:
            terminal.stop()

    async def test_no_input_is_delivered_after_stop(self, fake_tty: Pty):
        seen: list[str] = []
        terminal = ProcessTerminal()
        terminal.start(seen.append, _noop)
        terminal.stop()
        fake_tty.send(b"x")
        await _settle()
        assert seen == []


class TestResize:
    def test_sigwinch_calls_the_resize_handler(self, fake_tty: Pty):
        calls: list[int] = []
        terminal = ProcessTerminal()
        terminal.start(lambda _data: None, lambda: calls.append(1))
        try:
            # `start` itself raises one to refresh dimensions stale after a
            # suspend/resume, so there is already at least one call here.
            assert calls, "start() did not refresh the terminal size"
            calls.clear()
            os.kill(os.getpid(), signal.SIGWINCH)
            assert calls
        finally:
            terminal.stop()

    def test_stop_restores_the_previous_handler(self, fake_tty: Pty):
        marker: Callable[..., object] = lambda *_a: None  # noqa: E731
        previous = signal.signal(signal.SIGWINCH, marker)
        try:
            terminal = ProcessTerminal()
            terminal.start(lambda _data: None, _noop)
            assert signal.getsignal(signal.SIGWINCH) is not marker
            terminal.stop()
            assert signal.getsignal(signal.SIGWINCH) is marker
        finally:
            signal.signal(signal.SIGWINCH, previous)


class TestDrainInput:
    def test_returns_as_soon_as_stdin_goes_quiet(self, fake_tty: Pty):
        import time

        terminal = ProcessTerminal()
        terminal.start(lambda _data: None, _noop)
        try:
            started = time.monotonic()
            terminal.drain_input(max_ms=1000, idle_ms=20)
            elapsed = (time.monotonic() - started) * 1000
            assert elapsed < 500, f"drain slept through its budget instead of draining: {elapsed}ms"
        finally:
            terminal.stop()

    def test_swallows_pending_input_rather_than_forwarding_it(self, fake_tty: Pty):
        seen: list[str] = []
        terminal = ProcessTerminal()
        terminal.start(seen.append, _noop)
        try:
            fake_tty.send(b"leftovers")
            terminal.drain_input(max_ms=300, idle_ms=20)
            assert seen == [], f"drained input still reached the app: {seen!r}"
        finally:
            terminal.stop()


class _RecordingStream:
    """Records writes and flushes in order. Asserted against, not read from."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def write(self, data: str) -> int:
        self.events.append(("write", data))
        return len(data)

    def flush(self) -> None:
        self.events.append(("flush", ""))


class TestWrite:
    def test_output_is_flushed_rather_than_buffered(self, monkeypatch: pytest.MonkeyPatch):
        # A frame carries no newline, so a line-buffered stream would hold it
        # until the next one — the screen would update a bufferful at a time.
        stream = _RecordingStream()
        monkeypatch.setattr(sys, "stdout", cast(IO[Any], stream))
        ProcessTerminal().write("\x1b[2Kframe")
        assert stream.events == [("write", "\x1b[2Kframe"), ("flush", "")]

    def test_control_sequences_are_flushed_too(self, monkeypatch: pytest.MonkeyPatch):
        stream = _RecordingStream()
        monkeypatch.setattr(sys, "stdout", cast(IO[Any], stream))
        terminal = ProcessTerminal()
        terminal.hide_cursor()
        terminal.set_title("t")
        assert [kind for kind, _ in stream.events] == ["write", "flush", "write", "flush"]
