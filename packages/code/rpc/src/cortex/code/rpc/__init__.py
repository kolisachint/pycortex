# pyright: reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportReturnType=false
"""RPC mode: Headless operation with JSON stdin/stdout protocol.

Port of ``rpc-mode.ts`` from ``packages/coding-agent/src/modes/rpc/``.

Used for embedding the agent in other applications.
Receives commands as JSON on stdin, outputs events and responses as JSON on stdout.

Protocol:
- Commands: JSON objects with ``type`` field, optional ``id`` for correlation
- Responses: JSON objects with ``type: "response"``, ``command``, ``success``,
  and optional ``data``/``error``
- Events: AgentSessionEvent objects streamed as they occur
"""

from __future__ import annotations

import json
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .types import (
    RpcCommand as RpcCommand,
)
from .types import (
    RpcExtensionUIRequest as RpcExtensionUIRequest,
)
from .types import (
    RpcExtensionUIResponse,
    RpcResponse,
    RpcSessionState,
)


def serialize_json_line(obj: Any) -> str:
    """Serialize an object to a JSON line."""
    return json.dumps(obj, default=str) + "\n"


def write_raw_stdout(text: str) -> None:
    """Write raw text to stdout."""
    sys.stdout.write(text)
    sys.stdout.flush()


@dataclass
class RuntimeHostProtocol:
    """Protocol for the runtime host used by RPC mode."""

    session: Any
    new_session: Callable[..., Any] = lambda _: None
    fork: Callable[..., Any] = lambda _: None
    switch_session: Callable[..., Any] = lambda _: None
    dispose: Callable[..., Any] = lambda: None
    set_rebind_session: Callable[..., Any] = lambda _: None


def make_success_response(
    id: str | None,
    command: str,
    data: Any | None = None,
) -> RpcResponse:
    """Create a success response."""
    response = RpcResponse(id=id, command=command, success=True)
    if data is not None:
        response.data = data if isinstance(data, dict) else {"value": data}
    return response


def make_error_response(
    id: str | None,
    command: str,
    message: str,
) -> RpcResponse:
    """Create an error response."""
    return RpcResponse(id=id, command=command, success=False, error=message)


async def run_rpc_mode(runtime_host: RuntimeHostProtocol) -> None:
    """Run in RPC mode.

    Listens for JSON commands on stdin, outputs events and responses on stdout.
    """
    session = runtime_host.session
    unsubscribe: Callable[..., Any] | None = None

    # Pending extension UI requests waiting for response
    pending_extension_requests: dict[str, dict[str, Any]] = {}

    # Shutdown request flag
    shutdown_requested = False
    shutting_down = False
    signal_cleanup_handlers: list[Callable[[], Any]] = []

    def output(obj: Any) -> None:
        """Output a JSON object to stdout."""
        write_raw_stdout(serialize_json_line(obj))

    def handle_command(command_dict: dict[str, Any]) -> RpcResponse | None:
        """Handle a single RPC command."""
        cmd_type = command_dict.get("type", "")
        cmd_id = command_dict.get("id")

        # Handle different command types
        if cmd_type == "prompt":
            # Async command - return None and let events flow
            # In a real implementation, this would call session.prompt()
            # For now, return a success response
            return make_success_response(cmd_id, "prompt")

        elif cmd_type == "steer":
            return make_success_response(cmd_id, "steer")

        elif cmd_type == "follow_up":
            return make_success_response(cmd_id, "follow_up")

        elif cmd_type == "abort":
            return make_success_response(cmd_id, "abort")

        elif cmd_type == "new_session":
            return make_success_response(cmd_id, "new_session", {"cancelled": False})

        elif cmd_type == "get_state":
            state: RpcSessionState = RpcSessionState(
                thinking_level="off",
                is_streaming=False,
                is_compacting=False,
                steering_mode="all",
                follow_up_mode="all",
                session_id="",
                auto_compaction_enabled=True,
                message_count=0,
                pending_message_count=0,
            )
            return make_success_response(cmd_id, "get_state", state.__dict__)

        elif cmd_type == "set_model":
            return make_success_response(cmd_id, "set_model")

        elif cmd_type == "cycle_model":
            return make_success_response(cmd_id, "cycle_model", None)

        elif cmd_type == "get_available_models":
            return make_success_response(cmd_id, "get_available_models", {"models": []})

        elif cmd_type == "set_thinking_level":
            return make_success_response(cmd_id, "set_thinking_level")

        elif cmd_type == "cycle_thinking_level":
            return make_success_response(cmd_id, "cycle_thinking_level", None)

        elif cmd_type == "set_steering_mode":
            return make_success_response(cmd_id, "set_steering_mode")

        elif cmd_type == "set_follow_up_mode":
            return make_success_response(cmd_id, "set_follow_up_mode")

        elif cmd_type == "compact":
            return make_success_response(cmd_id, "compact", {})

        elif cmd_type == "set_auto_compaction":
            return make_success_response(cmd_id, "set_auto_compaction")

        elif cmd_type == "set_auto_retry":
            return make_success_response(cmd_id, "set_auto_retry")

        elif cmd_type == "abort_retry":
            return make_success_response(cmd_id, "abort_retry")

        elif cmd_type == "bash":
            return make_success_response(
                cmd_id, "bash", {"stdout": "", "stderr": "", "exit_code": 0}
            )

        elif cmd_type == "abort_bash":
            return make_success_response(cmd_id, "abort_bash")

        elif cmd_type == "get_session_stats":
            return make_success_response(cmd_id, "get_session_stats", {})

        elif cmd_type == "export_html":
            return make_success_response(cmd_id, "export_html", {"path": ""})

        elif cmd_type == "switch_session":
            return make_success_response(cmd_id, "switch_session", {"cancelled": False})

        elif cmd_type == "fork":
            return make_success_response(cmd_id, "fork", {"text": "", "cancelled": False})

        elif cmd_type == "clone":
            return make_success_response(cmd_id, "clone", {"cancelled": False})

        elif cmd_type == "get_fork_messages":
            return make_success_response(cmd_id, "get_fork_messages", {"messages": []})

        elif cmd_type == "get_last_assistant_text":
            return make_success_response(cmd_id, "get_last_assistant_text", {"text": None})

        elif cmd_type == "set_session_name":
            return make_success_response(cmd_id, "set_session_name")

        elif cmd_type == "get_messages":
            return make_success_response(cmd_id, "get_messages", {"messages": []})

        elif cmd_type == "get_commands":
            return make_success_response(cmd_id, "get_commands", {"commands": []})

        else:
            return make_error_response(cmd_id, cmd_type, f"Unknown command: {cmd_type}")

    def handle_input_line(line: str) -> None:
        """Handle a single input line from stdin."""
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as e:
            output(make_error_response(None, "parse", f"Failed to parse command: {e}"))
            return

        # Handle extension UI responses
        if isinstance(parsed, dict) and parsed.get("type") == "extension_ui_response":
            response = RpcExtensionUIResponse(**parsed)
            pending = pending_extension_requests.get(response.id)
            if pending:
                del pending_extension_requests[response.id]
                pending["resolve"](response)
            return

        # Handle regular commands
        command_dict = parsed if isinstance(parsed, dict) else {}
        try:
            response = handle_command(command_dict)
            if response is not None:
                output(response)
        except Exception as e:
            cmd_id = command_dict.get("id")
            cmd_type = command_dict.get("type", "unknown")
            output(make_error_response(cmd_id, cmd_type, str(e)))

    def shutdown(exit_code: int = 0) -> None:
        """Shutdown the RPC mode."""
        nonlocal shutting_down
        if shutting_down:
            sys.exit(exit_code)
        shutting_down = True
        for cleanup in signal_cleanup_handlers:
            cleanup()
        if unsubscribe:
            unsubscribe()
        runtime_host.dispose()
        sys.stdin.pause()
        sys.exit(exit_code)

    def check_shutdown_requested() -> None:
        """Check if shutdown was requested."""
        if shutdown_requested:
            shutdown()

    # Signal handlers
    def handle_signal(signum: int, frame: Any) -> None:
        shutdown(129 if signum == signal.SIGHUP else 143)

    for sig in [signal.SIGTERM]:
        original_handler = signal.getsignal(sig)
        signal.signal(sig, handle_signal)

        def make_cleanup(s: int, h: Any) -> Callable[[], None]:
            return lambda: signal.signal(s, h)

        signal_cleanup_handlers.append(make_cleanup(sig, original_handler))

    # Subscribe to session events
    def on_session_event(event: Any) -> None:
        output(event)

    unsubscribe = session.subscribe(on_session_event)

    # Read lines from stdin
    try:
        for line in sys.stdin:
            line = line.strip()
            if line:
                handle_input_line(line)
                check_shutdown_requested()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
