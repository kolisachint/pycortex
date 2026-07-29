"""ProcessTerminal's Windows console path, driven on any OS.

On Windows there is no pty to point the class at — and this is the path that was
missing entirely, so it needs to be tested somewhere other than a Windows box.
The three kernel32 calls and ``msvcrt`` are the only OS surface it touches, and
both are behind seams (`cortex.tui.terminal._windows.std_handle` and friends,
`import msvcrt`), so `sys.platform` plus a fake console is the whole harness.

What these pin, in the order the bug happened:
`start()` must put the console in raw mode at all; a keystroke must reach the
input handler; it must arrive on the loop thread rather than the reader's; and
`stop()` must hand the console back exactly as it was found.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import threading
import types
from collections import deque
from collections.abc import Iterator
from typing import IO, Any, cast

import pytest
from cortex.tui.terminal import _windows
from cortex.tui.terminal.terminal import ProcessTerminal

STDIN_HANDLE = 1000
STDOUT_HANDLE = 2000

#: A console's mode as a fresh cmd.exe hands it over.
DEFAULT_STDIN_MODE = (
    _windows.ENABLE_PROCESSED_INPUT | _windows.ENABLE_LINE_INPUT | _windows.ENABLE_ECHO_INPUT
)
DEFAULT_STDOUT_MODE = 0x0003  # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT


def utf16_units(text: str) -> list[str]:
    """Split ``text`` into UTF-16 code units, which is what `getwch` returns.

    Decoding the whole encoding back would rejoin the pair — the halves have to
    be decoded one at a time to arrive as the lone surrogates a console reports.
    """
    data = text.encode("utf-16-le", "surrogatepass")
    return [data[i : i + 2].decode("utf-16-le", "surrogatepass") for i in range(0, len(data), 2)]


class FakeConsole:
    """The three kernel32 calls `_windows` makes, as a recording fake."""

    def __init__(
        self,
        *,
        stdin_is_console: bool = True,
        stdout_is_console: bool = True,
        stdout_mode: int = DEFAULT_STDOUT_MODE,
        vt_input_supported: bool = True,
    ) -> None:
        self.modes: dict[int, int] = {}
        if stdin_is_console:
            self.modes[STDIN_HANDLE] = DEFAULT_STDIN_MODE
        if stdout_is_console:
            self.modes[STDOUT_HANDLE] = stdout_mode
        self.vt_input_supported = vt_input_supported
        self.set_calls: list[tuple[int, int]] = []

    # -- the seams ------------------------------------------------------
    def std_handle(self, which: int) -> int | None:
        if which == _windows.STD_INPUT_HANDLE:
            return STDIN_HANDLE
        if which == _windows.STD_OUTPUT_HANDLE:
            return STDOUT_HANDLE
        return None

    def get_console_mode(self, handle: int) -> int | None:
        return self.modes.get(handle)

    def set_console_mode(self, handle: int, mode: int) -> bool:
        if handle not in self.modes:
            return False
        if (
            handle == STDIN_HANDLE
            and mode & _windows.ENABLE_VIRTUAL_TERMINAL_INPUT
            and not self.vt_input_supported
        ):
            return False
        self.modes[handle] = mode
        self.set_calls.append((handle, mode))
        return True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeConsole:
        monkeypatch.setattr(_windows, "std_handle", self.std_handle)
        monkeypatch.setattr(_windows, "get_console_mode", self.get_console_mode)
        monkeypatch.setattr(_windows, "set_console_mode", self.set_console_mode)
        return self


class FakeKeyboard:
    """`msvcrt.kbhit`/`getwch` over a queue a test can push code units into."""

    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self.reads = 0

    def feed(self, text: str) -> None:
        """Queue ``text`` the way the console would: one UTF-16 code unit a read."""
        with self._lock:
            self._queue.extend(utf16_units(text))

    def feed_units(self, units: str) -> None:
        """Queue the units verbatim (for the legacy scan-code pairs)."""
        with self._lock:
            self._queue.extend(units)

    def kbhit(self) -> bool:
        with self._lock:
            return bool(self._queue)

    def getwch(self) -> str:
        with self._lock:
            self.reads += 1
            return self._queue.popleft()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeKeyboard:
        module = types.ModuleType("msvcrt")
        fake = cast(Any, module)
        fake.kbhit = self.kbhit
        fake.getwch = self.getwch
        monkeypatch.setitem(sys.modules, "msvcrt", module)
        return self


class RecordingStream:
    """Stands in for `sys.stdout`; the console mode is what the tests assert on."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, data: str) -> int:
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    @property
    def text(self) -> str:
        return "".join(self.written)


class WindowsHarness:
    """A ProcessTerminal that believes it is on Windows."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, console: FakeConsole) -> None:
        self.console = console.install(monkeypatch)
        self.keyboard = FakeKeyboard().install(monkeypatch)
        self.stdout = RecordingStream()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "stdout", cast(IO[Any], self.stdout))
        monkeypatch.setattr(_windows, "KEY_POLL_INTERVAL_S", 0.001)
        self.terminal = ProcessTerminal()
        self.keys: list[str] = []
        self.resizes: list[int] = []

    def start(self) -> ProcessTerminal:
        self.terminal.start(self.keys.append, lambda: self.resizes.append(1))
        return self.terminal

    @property
    def reader_thread(self) -> threading.Thread | None:
        """Windows reads keys on a thread, and a thread is not visible in any
        frame — asserting it was never started, or has stopped, means looking."""
        return cast(Any, self.terminal)._reader_thread

    @property
    def resize_thread(self) -> threading.Thread | None:
        return cast(Any, self.terminal)._resize_thread


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> Iterator[WindowsHarness]:
    harness = WindowsHarness(monkeypatch, FakeConsole())
    yield harness
    harness.terminal.stop()


async def _settle(times: int = 4) -> None:
    """Let the reader thread poll, the loop run its callback, and the buffer flush."""
    for _ in range(times):
        await asyncio.sleep(0.03)


class TestRawMode:
    def test_start_clears_line_echo_and_processed_input(self, windows: WindowsHarness):
        windows.start()
        mode = windows.console.modes[STDIN_HANDLE]
        assert not mode & _windows.ENABLE_LINE_INPUT, "still line-buffered: nothing until Enter"
        assert not mode & _windows.ENABLE_ECHO_INPUT, "console would echo every keystroke"
        assert not mode & _windows.ENABLE_PROCESSED_INPUT, (
            "PROCESSED_INPUT left on: Ctrl+C would be a CTRL_C_EVENT, not a \\x03 key"
        )
        assert mode & _windows.ENABLE_VIRTUAL_TERMINAL_INPUT, "arrows would arrive as scan codes"

    def test_start_enables_vt_output_processing(self, windows: WindowsHarness):
        windows.start()
        assert windows.console.modes[STDOUT_HANDLE] & _windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING

    def test_stop_restores_both_modes(self, windows: WindowsHarness):
        windows.start()
        windows.terminal.stop()
        assert windows.console.modes[STDIN_HANDLE] == DEFAULT_STDIN_MODE
        assert windows.console.modes[STDOUT_HANDLE] == DEFAULT_STDOUT_MODE

    def test_stdout_vt_is_left_on_when_it_was_already_on(self, monkeypatch: pytest.MonkeyPatch):
        """Restoring a mode this process did not set would turn VT off under WT."""
        already_on = DEFAULT_STDOUT_MODE | _windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING
        harness = WindowsHarness(monkeypatch, FakeConsole(stdout_mode=already_on))
        harness.start()
        harness.terminal.stop()
        assert harness.console.modes[STDOUT_HANDLE] == already_on

    async def test_a_redirected_stdin_is_left_alone(self, monkeypatch: pytest.MonkeyPatch):
        """`GetConsoleMode` failing is the Windows form of "not a tty"."""
        harness = WindowsHarness(monkeypatch, FakeConsole(stdin_is_console=False))
        harness.start()  # must not raise
        assert not any(handle == STDIN_HANDLE for handle, _ in harness.console.set_calls)
        assert harness.reader_thread is None, "started a reader for a stdin it cannot read"
        harness.keyboard.feed("typed")
        await _settle()
        harness.terminal.stop()
        assert harness.keyboard.reads == 0, "read a console it never put in raw mode"
        assert harness.keys == []

    async def test_stop_leaves_no_threads_running(self, windows: WindowsHarness):
        """A start/stop cycle that leaks its reader or its resize poll leaks one
        of each per cycle, and neither shows up in anything the screen can see."""
        windows.start()
        started = [windows.reader_thread, windows.resize_thread]
        assert all(t is not None for t in started), "input and resize both run on a thread here"
        windows.terminal.stop()
        await _settle(2)
        assert [t for t in started if t is not None and t.is_alive()] == []
        assert windows.reader_thread is None and windows.resize_thread is None


class TestInput:
    async def test_keystrokes_reach_the_handler(self, windows: WindowsHarness):
        windows.start()
        windows.keyboard.feed("hi")
        await _settle()
        assert windows.keys == ["h", "i"]

    async def test_ctrl_c_arrives_as_a_keystroke(self, windows: WindowsHarness):
        """Two of these is how the app exits, so it has to be a key and not a signal."""
        windows.start()
        windows.keyboard.feed("\x03")
        await _settle()
        assert windows.keys == ["\x03"]

    async def test_an_escape_sequence_arrives_as_one_event(self, windows: WindowsHarness):
        """VT input delivers ESC [ A as three reads; the buffer must rejoin them."""
        windows.start()
        windows.keyboard.feed("\x1b[A")
        await _settle()
        assert windows.keys == ["\x1b[A"]

    async def test_a_surrogate_pair_arrives_as_one_character(self, windows: WindowsHarness):
        """getwch reads UTF-16 code units, so an emoji is two of them."""
        windows.start()
        windows.keyboard.feed("😀")
        await _settle()
        assert windows.keys == ["😀"]

    async def test_a_surrogate_pair_split_across_reads_is_rejoined(self, windows: WindowsHarness):
        units = utf16_units("😀")
        assert len(units) == 2
        windows.start()
        windows.keyboard.feed_units(units[0])
        await _settle(2)
        assert windows.keys == [], "delivered half a character"
        windows.keyboard.feed_units(units[1])
        await _settle()
        assert windows.keys == ["😀"]

    async def test_input_is_delivered_on_the_loop_thread(self, windows: WindowsHarness):
        """Rendering is single-threaded: the reader thread must not drive the editor."""
        threads: list[threading.Thread] = []
        windows.terminal.start(
            lambda _seq: threads.append(threading.current_thread()), lambda: None
        )
        windows.keyboard.feed("x")
        await _settle()
        assert threads == [threading.current_thread()]

    async def test_no_input_is_delivered_after_stop(self, windows: WindowsHarness):
        windows.start()
        windows.terminal.stop()
        windows.keyboard.feed("x")
        await _settle()
        assert windows.keys == []

    async def test_drained_input_does_not_reach_the_app(self, windows: WindowsHarness):
        windows.start()
        windows.keyboard.feed("leftovers")
        windows.terminal.drain_input(max_ms=300, idle_ms=20)
        await _settle()
        assert windows.keys == [], f"drained input still reached the app: {windows.keys!r}"


class TestLegacyConsole:
    """A console that refuses ENABLE_VIRTUAL_TERMINAL_INPUT (conhost before 1809)."""

    @pytest.fixture
    def legacy(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[WindowsHarness]:
        harness = WindowsHarness(monkeypatch, FakeConsole(vt_input_supported=False))
        yield harness
        harness.terminal.stop()

    def test_raw_mode_is_still_entered(self, legacy: WindowsHarness):
        legacy.start()
        mode = legacy.console.modes[STDIN_HANDLE]
        assert not mode & _windows.RAW_INPUT_MODE_MASK
        assert not mode & _windows.ENABLE_VIRTUAL_TERMINAL_INPUT

    async def test_an_extended_key_is_translated_to_its_escape_sequence(
        self, legacy: WindowsHarness
    ):
        legacy.start()
        legacy.keyboard.feed_units("\xe0H")  # up arrow, as the CRT reports it
        await _settle()
        assert legacy.keys == ["\x1b[A"]

    async def test_a_lone_prefix_does_not_block_the_reader(self, legacy: WindowsHarness):
        """`\\x00` is also Ctrl+Space, with no scan code behind it."""
        legacy.start()
        legacy.keyboard.feed_units("\x00")
        await _settle(2)
        legacy.keyboard.feed_units("a")
        await _settle()
        assert legacy.keys == ["a"]


class TestResize:
    def test_start_refreshes_the_size(self, windows: WindowsHarness):
        """No SIGWINCH to raise, but the first frame still needs the real size."""
        windows.start()
        assert windows.resizes, "start() did not refresh the terminal size"

    async def test_a_size_change_fires_the_resize_handler(
        self, windows: WindowsHarness, monkeypatch: pytest.MonkeyPatch
    ):
        import cortex.tui.terminal.terminal as terminal_module

        monkeypatch.setattr(terminal_module, "WINDOWS_RESIZE_POLL_INTERVAL_S", 0.01)
        monkeypatch.setenv("COLUMNS", "80")
        monkeypatch.setenv("LINES", "24")
        windows.start()
        windows.resizes.clear()
        monkeypatch.setenv("COLUMNS", "100")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if windows.resizes:
                break
        assert windows.resizes, "the resize poll never noticed the console had changed size"

    async def test_the_poll_stops_with_the_terminal(
        self, windows: WindowsHarness, monkeypatch: pytest.MonkeyPatch
    ):
        import cortex.tui.terminal.terminal as terminal_module

        monkeypatch.setattr(terminal_module, "WINDOWS_RESIZE_POLL_INTERVAL_S", 0.01)
        monkeypatch.setenv("COLUMNS", "80")
        windows.start()
        windows.terminal.stop()
        windows.resizes.clear()
        monkeypatch.setenv("COLUMNS", "100")
        await asyncio.sleep(0.1)
        assert windows.resizes == []

    async def test_a_resize_is_handled_on_the_loop_thread(
        self, windows: WindowsHarness, monkeypatch: pytest.MonkeyPatch
    ):
        import cortex.tui.terminal.terminal as terminal_module

        monkeypatch.setattr(terminal_module, "WINDOWS_RESIZE_POLL_INTERVAL_S", 0.01)
        monkeypatch.setenv("COLUMNS", "80")
        threads: list[threading.Thread] = []
        windows.terminal.start(
            lambda _seq: None, lambda: threads.append(threading.current_thread())
        )
        threads.clear()
        monkeypatch.setenv("COLUMNS", "100")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if threads:
                break
        assert threads == [threading.current_thread()]


class TestEnableRawConsole:
    """`_windows` on its own, including the paths ProcessTerminal cannot reach."""

    def test_nothing_is_recorded_when_there_is_no_console(self, monkeypatch: pytest.MonkeyPatch):
        FakeConsole(stdin_is_console=False, stdout_is_console=False).install(monkeypatch)
        saved = _windows.enable_raw_console()
        assert not saved.active
        assert saved.stdin_mode is None and saved.stdout_mode is None

    def test_vt_input_is_reported(self, monkeypatch: pytest.MonkeyPatch):
        FakeConsole().install(monkeypatch)
        assert _windows.enable_raw_console().vt_input is True

    def test_vt_input_refusal_is_reported(self, monkeypatch: pytest.MonkeyPatch):
        FakeConsole(vt_input_supported=False).install(monkeypatch)
        saved = _windows.enable_raw_console()
        assert saved.active, "gave up on raw mode because one flag was refused"
        assert saved.vt_input is False

    def test_stdout_is_not_recorded_when_vt_was_already_on(self, monkeypatch: pytest.MonkeyPatch):
        """Recording a mode this process did not write is what makes restore wrong.

        The screen looks the same either way — both paths end on a mode with VT
        set — so the contract has to be asserted on what was recorded, not on
        where the console ended up.
        """
        already_on = DEFAULT_STDOUT_MODE | _windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING
        console = FakeConsole(stdout_mode=already_on).install(monkeypatch)
        saved = _windows.enable_raw_console()
        assert saved.stdout_handle is None and saved.stdout_mode is None
        assert not any(handle == STDOUT_HANDLE for handle, _ in console.set_calls)

    def test_restore_only_touches_what_was_changed(self, monkeypatch: pytest.MonkeyPatch):
        console = FakeConsole().install(monkeypatch)
        saved = _windows.enable_raw_console()
        console.set_calls.clear()
        _windows.restore_console(saved)
        assert console.set_calls == [
            (STDIN_HANDLE, DEFAULT_STDIN_MODE),
            (STDOUT_HANDLE, DEFAULT_STDOUT_MODE),
        ]

    def test_off_windows_every_entry_point_is_inert(self):
        """`_windows` is imported unconditionally, so it has to import and no-op."""
        assert _windows.std_handle(_windows.STD_INPUT_HANDLE) is None
        assert _windows.get_console_mode(1) is None
        assert _windows.set_console_mode(1, 0) is False
        assert not _windows.enable_raw_console().active
        assert _windows.read_available() == ""
        assert _windows.wait_readable(0.0) is False


class TestImportability:
    """The first thing that failed on Windows: importing the module at all.

    `terminal.py` imported `select`, `termios` and `tty` unconditionally, none of
    which exist there — so no amount of platform branching inside the class would
    have helped. A subprocess with those three modules made unimportable is the
    only honest way to check that from here.
    """

    def test_the_module_imports_without_select_termios_or_tty(self):
        import subprocess

        program = textwrap.dedent(
            """
            import asyncio, sys  # imported first: asyncio needs select itself
            import importlib.abc, importlib.machinery

            BLOCKED = {"select", "termios", "tty"}

            class Blocker(importlib.abc.MetaPathFinder):
                def find_spec(self, name, path=None, target=None):
                    if name in BLOCKED:
                        raise ImportError(f"no module named {name!r} on this platform")
                    return None

            for name in BLOCKED:
                sys.modules.pop(name, None)
            sys.meta_path.insert(0, Blocker())
            sys.platform = "win32"

            from cortex.tui.terminal.terminal import ProcessTerminal

            terminal = ProcessTerminal()
            terminal.start(lambda _seq: None, lambda: None)
            terminal.stop()
            print("ok")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().endswith("ok")


class TestWaitReadable:
    def test_true_as_soon_as_a_key_is_waiting(self, monkeypatch: pytest.MonkeyPatch):
        keyboard = FakeKeyboard().install(monkeypatch)
        keyboard.feed("a")
        assert _windows.wait_readable(5.0) is True

    def test_false_when_the_timeout_expires(self, monkeypatch: pytest.MonkeyPatch):
        FakeKeyboard().install(monkeypatch)
        assert _windows.wait_readable(0.02, poll_s=0.001) is False


class TestReadAvailable:
    def test_reads_only_what_is_buffered(self, monkeypatch: pytest.MonkeyPatch):
        keyboard = FakeKeyboard().install(monkeypatch)
        keyboard.feed("abc")
        assert _windows.read_available() == "abc"
        assert _windows.read_available() == ""

    def test_the_limit_is_respected(self, monkeypatch: pytest.MonkeyPatch):
        keyboard = FakeKeyboard().install(monkeypatch)
        keyboard.feed("abcdef")
        assert _windows.read_available(limit=2) == "ab"

    def test_vt_input_passes_a_nul_through(self, monkeypatch: pytest.MonkeyPatch):
        """Under VT input `\\x00` is Ctrl+Space, not an extended-key prefix."""
        keyboard = FakeKeyboard().install(monkeypatch)
        keyboard.feed_units("\x00")
        assert _windows.read_available(vt_input=True) == "\x00"

    def test_an_unknown_scan_code_is_dropped(self, monkeypatch: pytest.MonkeyPatch):
        keyboard = FakeKeyboard().install(monkeypatch)
        keyboard.feed_units("\xe0\x99a")
        assert _windows.read_available(vt_input=False) == "a"
