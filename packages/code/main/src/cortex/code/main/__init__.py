"""Cortex Code Main - CLI entry point.

Port of ``main.ts`` from ``packages/coding-agent/src/``.

This module provides the main CLI entry point for the hoocode coding assistant,
and is what the ``pycortex`` console script (declared on the ``cortexcode-code``
umbrella) runs.

The TS ``main.ts`` is 1,104 lines, almost all of it building the agent-session
runtime that the other modes need. Step 7.2 wires the one branch that does not:
with no flags, ``pycortex`` starts interactive mode against the real terminal.
The flags that *do* need the runtime — ``--list-models``, ``--export``,
``--print-token-surface``, ``--resume`` — say which migration step delivers them
and exit non-zero rather than printing a placeholder and reporting success; a
script that pipes ``--export`` somewhere should find out that nothing was
exported.

Step 7.10 added the session-file half of ``resolveSessionManager``:
``--continue`` reopens the most recent session for this directory, ``--session``
opens a named file, ``--no-session`` runs without one, and the app draws
whichever it is handed. Only the two branches that need a picker or an id lookup
are still out.
"""

from __future__ import annotations

import os
import sys

from cortex.code.config import APP_NAME, VERSION
from cortex.code.interactive import (
    InteractiveModeOptions,
    resolve_session_manager,
    run_interactive_mode,
)

from .args import Args as Args
from .args import parse_args, print_help

#: Flags whose behaviour needs a subsystem this port has not reached, and the
#: migration step that delivers each. Kept as data so the list shrinks visibly.
_UNAVAILABLE_FLAGS = {
    "--list-models": ("the model registry", "7.11"),
    "--export": ("the HTML/JSONL exporters", "7.12"),
    "--print-token-surface": ("the agent session", "7.4"),
    "--resume": ("the startup session picker", "7.11"),
}


def _unavailable(flag: str) -> int:
    """Report a flag whose subsystem has not been ported, and fail."""
    subsystem, step = _UNAVAILABLE_FLAGS[flag]
    print(
        f"{APP_NAME}: {flag} needs {subsystem}, which this port reaches at migration step {step}.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI.

    Args:
        argv: Command line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)

    # Handle help
    if args.help:
        print_help()
        return 0

    # Handle version
    if args.version:
        print(f"{APP_NAME} {VERSION}")
        return 0

    if args.list_models is not None:
        return _unavailable("--list-models")

    if args.export:
        return _unavailable("--export")

    if args.print_token_surface:
        return _unavailable("--print-token-surface")

    # `--resume` opens the session picker *before* the TUI exists, over a
    # standalone renderer (`selectSession`) that this port has not reached.
    # `/resume` inside the app does the same job over the same list.
    if args.resume:
        return _unavailable("--resume")

    if args.print:
        # Non-interactive mode
        if args.messages:
            print(f"Would process message: {args.messages[0]}")
            if args.model:
                print(f"Using model: {args.model}")
            if args.provider:
                print(f"Using provider: {args.provider}")
        else:
            print("No message provided for print mode")
            return 1
        return 0

    # `--session` takes a path *or* a partial session id in the TS; resolving an
    # id means listing every session and matching, which is the same machinery
    # `--resume` waits on. A path that is not there is reported rather than
    # silently starting a new session under that name.
    if args.session and not os.path.exists(args.session):
        print(
            f"{APP_NAME}: no session file at '{args.session}'. Resolving a session by id "
            "needs the startup session picker, which this port reaches at migration step 7.11.",
            file=sys.stderr,
        )
        return 1

    cwd = os.getcwd()
    return run_interactive_mode(
        InteractiveModeOptions(initial_messages=list(args.messages)),
        resolve_session_manager(
            cwd,
            continue_session=args.continue_session,
            session_path=args.session,
            session_dir=args.session_dir,
            no_session=args.no_session,
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
