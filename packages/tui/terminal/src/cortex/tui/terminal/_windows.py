"""Windows console plumbing for :class:`~cortex.tui.terminal.terminal.ProcessTerminal`.

POSIX hands the terminal two things this module has to reproduce: a tty in raw
mode (``termios`` + ``tty.setraw``) and a readable file descriptor that ``select``
and the event loop can watch. A Windows console has neither — ``msvcrt`` and
``SetConsoleMode`` are the whole interface, ``termios``/``tty``/``select`` do not
exist, and neither asyncio event loop can watch stdin (the proactor loop has no
``add_reader`` at all, and the selector one only accepts sockets).

So: raw mode is ``SetConsoleMode`` with ``ENABLE_LINE_INPUT`` /
``ENABLE_ECHO_INPUT`` / ``ENABLE_PROCESSED_INPUT`` cleared — the third is what
turns Ctrl+C from a CTRL_C_EVENT back into a plain ``\\x03`` keystroke, which is
what ``handle_ctrl_c`` reads — plus ``ENABLE_VIRTUAL_TERMINAL_INPUT`` so the
console encodes arrows, Home/End and friends as the escape sequences
``StdinBuffer`` and ``keys`` already parse. On stdout,
``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` is what makes the renderer's cursor moves
and colours mean anything. The prior modes are returned so ``stop()`` can put
them back.

Every entry point is a no-op off Windows, and the three ctypes calls are isolated
in :func:`std_handle`, :func:`get_console_mode` and :func:`set_console_mode` so
the logic above them can be driven from a test on any OS.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

# GetStdHandle ids.
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11

# Console input modes (wincon.h).
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Console output modes (wincon.h).
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

#: Cleared together to get the equivalent of ``tty.setraw``: no line buffering,
#: no local echo, and no signal/BREAK handling by the console driver.
RAW_INPUT_MODE_MASK = ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT

#: How often the reader asks ``msvcrt.kbhit()`` whether a key is waiting. There
#: is nothing to block on that also stays interruptible, so this is the floor on
#: keystroke latency — small enough to be imperceptible, coarse enough that the
#: poll costs nothing.
KEY_POLL_INTERVAL_S = 0.005

#: What ``msvcrt.getwch`` returns before the scan code of an extended key, when
#: ``ENABLE_VIRTUAL_TERMINAL_INPUT`` could not be turned on.
LEGACY_EXTENDED_PREFIXES = ("\x00", "\xe0")

#: Scan code → escape sequence, for the same fallback. Only the keys the editor
#: binds: a console old enough to refuse the VT input flag is a console the rest
#: of the TUI is already rendering to on a best-effort basis.
LEGACY_EXTENDED_KEYS = {
    "H": "\x1b[A",  # up
    "P": "\x1b[B",  # down
    "M": "\x1b[C",  # right
    "K": "\x1b[D",  # left
    "G": "\x1b[H",  # home
    "O": "\x1b[F",  # end
    "R": "\x1b[2~",  # insert
    "S": "\x1b[3~",  # delete
    "I": "\x1b[5~",  # page up
    "Q": "\x1b[6~",  # page down
}


@dataclass
class SavedConsole:
    """The console modes ``start()`` changed, and how to describe what it got.

    Only the handles whose mode was actually written are recorded, so restoring
    never touches a mode this process did not set.
    """

    stdin_handle: int | None = None
    stdin_mode: int | None = None
    stdout_handle: int | None = None
    stdout_mode: int | None = None
    #: Whether ``ENABLE_VIRTUAL_TERMINAL_INPUT`` took. When it did, keystrokes
    #: arrive already encoded as escape sequences and need no translation.
    vt_input: bool = False

    @property
    def active(self) -> bool:
        """Whether stdin is a console that was successfully put in raw mode.

        False for a redirected stdin, which is the Windows form of POSIX's
        "not a tty — leave it alone": there is nothing to read and nothing to
        restore.
        """
        return self.stdin_handle is not None


_kernel32_dll: Any | None = None
_kernel32_resolved = False


def _kernel32() -> Any | None:
    """``kernel32``, with the signatures ctypes needs declared. None off Windows."""
    global _kernel32_dll, _kernel32_resolved
    if _kernel32_resolved:
        return _kernel32_dll
    _kernel32_resolved = True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        try:
            dll = ctypes.WinDLL("kernel32", use_last_error=True)
        except (OSError, AttributeError):
            # No kernel32 to load. Reached by a test that claims to be Windows
            # on a machine that is not; the console then reports "not a console"
            # and the terminal takes the redirected-stdin path.
            return None
        # Without these, GetStdHandle comes back through a c_int and a handle
        # above 2**31 arrives negative — i.e. indistinguishable from failure.
        dll.GetStdHandle.argtypes = [wintypes.DWORD]
        dll.GetStdHandle.restype = wintypes.HANDLE
        dll.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        dll.GetConsoleMode.restype = wintypes.BOOL
        dll.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        dll.SetConsoleMode.restype = wintypes.BOOL
        _kernel32_dll = dll
    return _kernel32_dll


def _msvcrt() -> Any | None:
    """The ``msvcrt`` module, or None when it is not importable."""
    try:
        import msvcrt
    except ImportError:
        return None
    return msvcrt


def std_handle(which: int) -> int | None:
    """A standard handle, or None if there is none (detached process, no Windows)."""
    dll = _kernel32()
    if dll is None:
        return None
    handle = dll.GetStdHandle(which)
    if not handle or handle == -1 or handle == 0xFFFFFFFFFFFFFFFF or handle == 0xFFFFFFFF:
        return None
    return int(handle)


def get_console_mode(handle: int) -> int | None:
    """The console mode of ``handle``, or None if it is not a console.

    A redirected stdin/stdout is a pipe or a file, and ``GetConsoleMode`` fails
    on both — the check that keeps this off anything that is not a terminal.
    """
    dll = _kernel32()
    if dll is None:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        mode = wintypes.DWORD()
        if not dll.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)):
            return None
        return int(mode.value)
    return None


def set_console_mode(handle: int, mode: int) -> bool:
    """Set the console mode of ``handle``. False if the console refused it."""
    dll = _kernel32()
    if dll is None:
        return False
    if sys.platform == "win32":
        import ctypes

        return bool(dll.SetConsoleMode(ctypes.c_void_p(handle), mode))
    return False


def enable_raw_console() -> SavedConsole:
    """Put the console in raw mode and turn VT input/output on.

    Returns what to hand :func:`restore_console` on the way out. Every step is
    optional and independently recorded: a console can accept the raw-mode bits
    and refuse the VT input flag (conhost before Windows 10 1809 does), and stdout
    can be a file while stdin is a console.
    """
    saved = SavedConsole()

    handle = std_handle(STD_INPUT_HANDLE)
    if handle is not None:
        mode = get_console_mode(handle)
        if mode is not None:
            raw = mode & ~RAW_INPUT_MODE_MASK
            if set_console_mode(handle, raw | ENABLE_VIRTUAL_TERMINAL_INPUT):
                saved.stdin_handle = handle
                saved.stdin_mode = mode
                saved.vt_input = True
            elif set_console_mode(handle, raw):
                # Raw, but the console will report arrows and friends as scan
                # codes rather than escape sequences — see LEGACY_EXTENDED_KEYS.
                saved.stdin_handle = handle
                saved.stdin_mode = mode

    handle = std_handle(STD_OUTPUT_HANDLE)
    if handle is not None:
        mode = get_console_mode(handle)
        # Recorded only when this process is the one that turned it on, so a
        # terminal that already had it keeps it after stop().
        if mode is not None and not mode & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            if set_console_mode(handle, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING):
                saved.stdout_handle = handle
                saved.stdout_mode = mode

    return saved


def restore_console(saved: SavedConsole) -> None:
    """Put back the modes :func:`enable_raw_console` changed."""
    if saved.stdin_handle is not None and saved.stdin_mode is not None:
        set_console_mode(saved.stdin_handle, saved.stdin_mode)
    if saved.stdout_handle is not None and saved.stdout_mode is not None:
        set_console_mode(saved.stdout_handle, saved.stdout_mode)


def wait_readable(timeout_s: float, poll_s: float = KEY_POLL_INTERVAL_S) -> bool:
    """Whether a keystroke is waiting, within ``timeout_s``.

    The stand-in for ``select.select([fd], [], [], timeout)``. There is no
    waitable object behind ``msvcrt.kbhit()``, so this polls; a blocking
    ``WaitForSingleObject`` on the console handle would also wake for focus,
    mouse and buffer-size records, which ``kbhit`` filters out for us.
    """
    module = _msvcrt()
    if module is None:
        return False
    deadline = time.monotonic() + timeout_s
    while True:
        if module.kbhit():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_s, remaining))


def read_available(vt_input: bool = True, limit: int = 4096) -> str:
    """Read every keystroke the console has buffered, without blocking.

    ``msvcrt.getwch`` is only called while ``kbhit`` says something is there, so
    this returns promptly on an idle console instead of parking in a read the
    reader thread could not be woken out of.
    """
    module = _msvcrt()
    if module is None:
        return ""
    out: list[str] = []
    while len(out) < limit and module.kbhit():
        char = module.getwch()
        if not vt_input and char in LEGACY_EXTENDED_PREFIXES:
            # An extended key: the scan code is the next read, and the pair is
            # queued together, so kbhit is the guard against a lone prefix
            # (Ctrl+Space also reports \x00) turning into a blocking read.
            scan = module.getwch() if module.kbhit() else ""
            translated = LEGACY_EXTENDED_KEYS.get(scan)
            if translated is not None:
                out.append(translated)
            continue
        out.append(char)
    return "".join(out)
