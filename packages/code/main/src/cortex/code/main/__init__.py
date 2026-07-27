"""Cortex Code Main - CLI entry point.

Port of ``main.ts`` from ``packages/coding-agent/src/``.

This module provides the main CLI entry point for the hoocode coding assistant,
and is what the ``pycortex`` console script (declared on the ``cortexcode-code``
umbrella) runs.

The TS ``main.ts`` is 1,104 lines, almost all of it building the agent-session
runtime that the other modes need. Step 7.2 wires the one branch that does not:
with no flags, ``pycortex`` starts interactive mode against the real terminal.
The flags that *do* need the runtime — ``--list-models``, ``--export``,
``--print-token-surface`` — say which migration step delivers them and exit
non-zero rather than printing a placeholder and reporting success; a script that
pipes ``--export`` somewhere should find out that nothing was exported.
"""

from __future__ import annotations

import sys

from cortex.code.config import APP_NAME, VERSION
from cortex.code.interactive import InteractiveModeOptions, run_interactive_mode

from .args import Args as Args
from .args import parse_args, print_help

#: Flags whose behaviour needs a subsystem this port has not reached, and the
#: migration step that delivers each. Kept as data so the list shrinks visibly.
_UNAVAILABLE_FLAGS = {
    "--list-models": ("the model registry", "7.11"),
    "--export": ("session persistence", "7.10"),
    "--print-token-surface": ("the agent session", "7.4"),
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

    return run_interactive_mode(InteractiveModeOptions(initial_messages=list(args.messages)))


if __name__ == "__main__":
    sys.exit(main())
