"""Collection rules for the terminal leaf's tests.

`test_process_terminal_io.py` drives ProcessTerminal through a real pty, so it
imports `pty`, `fcntl` and `termios` at module scope — none of which exist on
Windows, where importing the module is a collection error rather than a skip.
The Windows half of the same contract lives in `test_windows_console.py`, which
fakes the console and so runs everywhere.
"""

from __future__ import annotations

import sys

collect_ignore: list[str] = []

if sys.platform == "win32":
    collect_ignore.append("test_process_terminal_io.py")
