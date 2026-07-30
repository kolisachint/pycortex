"""Cortex Code Main - CLI entry point.

Port of ``main.ts`` from ``packages/coding-agent/src/``.

This module provides the main CLI entry point for the hoocode coding assistant,
and is what the ``pycortex`` console script (declared on the ``cortexcode-code``
umbrella) runs.

The TS ``main.ts`` is 1,104 lines, almost all of it building the agent-session
runtime that the other modes need. Step 7.2 wired the one branch that does not:
with no flags, ``pycortex`` starts interactive mode against the real terminal.
Step 7.10 added the session-file half of ``resolveSessionManager``:
``--continue`` reopens the most recent session for this directory, ``--session``
opens a named file, ``--no-session`` runs without one.

Step 7.12 — the cutover — wired the two branches that had been standing in for
themselves: ``-p`` ran ``cortex.code.print`` for real instead of printing
``Would process message: …`` and reporting success, and ``--list-models`` grew
the table four error messages elsewhere in the port had been pointing users at.
What is still out says so and exits non-zero rather than pretending: ``--export``
(the HTML exporter is unported; the JSONL half exists), ``--resume``
(``selectSession`` renders before the TUI exists) and ``--print-token-surface``.
"""

from __future__ import annotations

import asyncio
import os
import sys

from cortex.code.config import (
    APP_NAME,
    ENV_SESSION_DIR,
    VERSION,
    SettingsManager,
    expand_tilde_path,
    get_agent_dir,
)
from cortex.code.interactive import (
    InteractiveModeOptions,
    build_model_registry,
    build_startup_session,
    resolve_session_manager,
    run_interactive_mode,
)
from cortex.code.print import PrintModeOptions, run_print_mode
from cortex.code.session import AgentSessionRuntime, AgentSessionServices, SessionManager

from .args import Args as Args
from .args import parse_args, print_help
from .list_models import list_models

#: Flags whose behaviour needs a subsystem this port does not have, and what.
#: Kept as data so the list shrinks visibly.
_UNAVAILABLE_FLAGS = {
    "--export": "the HTML session exporter",
    "--print-token-surface": "the token-surface dump",
    "--resume": "the startup session picker",
}


def _unavailable(flag: str) -> int:
    """Report a flag whose subsystem has not been ported, and fail."""
    subsystem = _UNAVAILABLE_FLAGS[flag]
    print(
        f"{APP_NAME}: {flag} needs {subsystem}, which this port does not have.",
        file=sys.stderr,
    )
    return 2


def resolve_session_dir(cli_session_dir: str | None, cwd: str) -> str | None:
    """Where sessions live. Port of ``main.ts``'s three-way resolution.

    ``--session-dir``, then ``HOOCODE_CODING_AGENT_SESSION_DIR``, then the
    ``sessionDir`` setting — and ``None`` for the default beside the agent
    directory. Only the flag was read here, so a user who had set either of the
    other two got their sessions written somewhere they had not asked for.
    """
    if cli_session_dir:
        return cli_session_dir
    env_dir = os.environ.get(ENV_SESSION_DIR)
    if env_dir:
        return expand_tilde_path(env_dir)
    return SettingsManager.create(cwd).get_session_dir()


def run_print(args: Args, cwd: str, session_manager: SessionManager) -> int:
    """Answer one prompt on stdout and exit. Port of ``main.ts``'s print branch.

    Until 7.12 this printed ``Would process message: <text>`` and returned 0 —
    a placeholder that reported success, which is the one thing a script piping
    ``-p`` somewhere must never be told. ``cortex.code.print`` has been ported
    since Phase 5 and had no caller; it gets the same startup the TUI does, so
    the two modes answer with the same model, session and credentials.
    """
    if not args.messages:
        print("No message provided for print mode")
        return 1

    startup = build_startup_session(cwd=cwd, session_manager=session_manager)
    if startup.error:
        print(f"{APP_NAME}: {startup.error}", file=sys.stderr)
        return 1

    host = AgentSessionRuntime(
        startup.session,
        AgentSessionServices(
            cwd=cwd,
            agent_dir=get_agent_dir(),
            settings_manager=startup.settings,
        ),
    )
    initial, *rest = args.messages
    return asyncio.run(
        run_print_mode(
            host,
            PrintModeOptions(
                mode="json" if args.mode == "json" else "text",
                initial_message=initial,
                messages=list(rest),
            ),
        )
    )


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

    # Handle version. Bare, as `main.ts` prints it (`console.log(VERSION)`): a
    # script reading `pycortex --version` gets a version, not a sentence.
    if args.version:
        print(VERSION)
        return 0

    if args.list_models is not None:
        # `--list-models` takes an optional fuzzy pattern; bare, it is `True`.
        pattern = args.list_models if isinstance(args.list_models, str) else None
        asyncio.run(list_models(build_model_registry(), pattern))
        return 0

    if args.export:
        return _unavailable("--export")

    if args.print_token_surface:
        return _unavailable("--print-token-surface")

    # `--resume` opens the session picker *before* the TUI exists, over a
    # standalone renderer (`selectSession`) that this port has not reached.
    # `/resume` inside the app does the same job over the same list.
    if args.resume:
        return _unavailable("--resume")

    # `--session` takes a path *or* a partial session id in the TS; resolving an
    # id means listing every session and matching, which is the same machinery
    # `--resume` waits on. A path that is not there is reported rather than
    # silently starting a new session under that name.
    if args.session and not os.path.exists(args.session):
        print(
            f"{APP_NAME}: no session file at '{args.session}'. Resolving a session by id "
            "needs the startup session picker, which this port does not have.",
            file=sys.stderr,
        )
        return 1

    cwd = os.getcwd()
    session_manager = resolve_session_manager(
        cwd,
        continue_session=args.continue_session,
        session_path=args.session,
        session_dir=resolve_session_dir(args.session_dir, cwd),
        no_session=args.no_session,
    )

    if args.print:
        return run_print(args, cwd, session_manager)

    return run_interactive_mode(
        InteractiveModeOptions(initial_messages=list(args.messages)),
        session_manager,
    )


if __name__ == "__main__":
    sys.exit(main())
