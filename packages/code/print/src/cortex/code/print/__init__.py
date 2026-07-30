"""Print mode (single-shot): Send prompts, output result, exit.

Port of ``print-mode.ts`` from ``packages/coding-agent/src/modes/``.

Used for:
- ``hoocode -p "prompt"`` - text output
- ``hoocode --mode json "prompt"`` - JSON event stream
"""

from __future__ import annotations

import json
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from cortex.ai.types import ImageContent
from cortex.code.session import PromptOptions


@dataclass
class PrintModeOptions:
    """Options for print mode."""

    # Output mode: "text" for final response only, "json" for all events
    mode: Literal["text", "json"] = "text"
    # Array of additional prompts to send after initial_message
    messages: list[str] = field(default_factory=list)
    # First message to send (may contain @file content)
    initial_message: str | None = None
    # Images to attach to the initial message
    initial_images: list[ImageContent] = field(default_factory=list)
    # Internal: set when this process is a spawned subagent. Enables heartbeats and result.json.
    task_id: str | None = None
    # Hard cap on assistant turns. Near the cap the agent is asked to wrap up;
    # at the cap it is aborted.
    max_turns: int | None = None


@runtime_checkable
class SessionProtocol(Protocol):
    """What print mode needs of a session. The TS's `AgentSession` interface.

    Structural, as the TS type is: `code/main` hands this the real
    `AgentSession` and the tests hand it a mock, and neither inherits from
    anything. It was a `@dataclass` until 7.12 — nothing could satisfy it
    without *being* it, so the first real caller was a type error.
    """

    session_manager: Any
    agent: Any
    state: Any

    def subscribe(self, *args: Any, **kwargs: Any) -> Any: ...
    def prompt(self, *args: Any, **kwargs: Any) -> Any: ...
    def abort(self, *args: Any, **kwargs: Any) -> Any: ...
    def steer(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class RuntimeHostProtocol(Protocol):
    """What print mode needs of a runtime host: the session, and teardown."""

    @property
    def session(self) -> Any: ...

    def dispose(self) -> None: ...


def write_raw_stdout(text: str) -> None:
    """Write raw text to stdout."""
    sys.stdout.write(text)
    sys.stdout.flush()


def flush_raw_stdout() -> None:
    """Flush stdout."""
    sys.stdout.flush()


async def run_print_mode(
    runtime_host: RuntimeHostProtocol,
    options: PrintModeOptions,
) -> int:
    """Run in print (single-shot) mode.

    Sends prompts to the agent and outputs the result.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    mode = options.mode
    messages = options.messages
    initial_message = options.initial_message
    initial_images = options.initial_images

    exit_code = 0
    session = runtime_host.session
    unsubscribe: Callable[..., Any] | None = None

    # Signal handlers for cleanup
    original_handlers: dict[int, Any] = {}

    def cleanup() -> None:
        """Clean up resources."""
        nonlocal unsubscribe
        if unsubscribe:
            unsubscribe()
            unsubscribe = None
        runtime_host.dispose()
        flush_raw_stdout()

    def handle_signal(signum: int, frame: Any) -> None:
        """Handle termination signals."""
        cleanup()
        sys.exit(143 if signum == signal.SIGTERM else 129)

    # Register signal handlers
    for sig in [signal.SIGTERM]:
        original_handlers[sig] = signal.getsignal(sig)
        signal.signal(sig, handle_signal)

    try:
        if mode == "json":
            header = session.session_manager.get_header()
            if header:
                write_raw_stdout(json.dumps(header) + "\n")

        # Subscribe to session events for JSON mode
        if mode == "json":

            def on_event(event: Any) -> None:
                write_raw_stdout(json.dumps(event) + "\n")

            unsubscribe = session.subscribe(on_event)

        # Send initial message. The TS passes `{ images: initialImages }`, and
        # this passed the *list* — so `prompt` read `options.preflight_result`
        # off a list and every `-p` run with the real session died there. It
        # could not have been caught before 7.12: print mode had no caller.
        if initial_message:
            await session.prompt(initial_message, PromptOptions(images=list(initial_images)))

        # Send additional messages
        for message in messages:
            await session.prompt(message)

        # In text mode, output the final assistant message
        if mode == "text":
            state = session.state
            messages_list = state.messages if hasattr(state, "messages") else []
            if messages_list:
                last_message = messages_list[-1]
                if hasattr(last_message, "role") and last_message.role == "assistant":
                    assistant_msg = last_message
                    stop_reason = getattr(assistant_msg, "stop_reason", "stop")
                    if stop_reason in ("error", "aborted"):
                        error_msg = getattr(assistant_msg, "error_message", None)
                        print(error_msg or f"Request {stop_reason}", file=sys.stderr)
                        exit_code = 1
                    else:
                        content = getattr(assistant_msg, "content", [])
                        for block in content:
                            if hasattr(block, "type") and block.type == "text":
                                write_raw_stdout(block.text + "\n")

        return exit_code

    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    finally:
        # Restore signal handlers
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)

        cleanup()
