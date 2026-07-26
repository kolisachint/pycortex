# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Bash tool for shell command execution.

Mechanical port of ``core/tools/bash.ts`` (execute path). The 100ms streaming
update throttle is replaced with immediate update emission (there is no event
loop timer in the port's synchronous exec), and the TUI rendering is not ported.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cortex.agent.types import AgentTool, AgentToolResult
from cortex.ai.types import TextContent
from cortex.code.tools.output_accumulator import OutputAccumulator
from cortex.code.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    format_size,
)


@dataclass
class BashToolDetails:
    truncation: TruncationResult | None = None
    full_output_path: str | None = None


@dataclass
class BashExecResult:
    exit_code: int | None


OnData = Callable[[bytes], None]


class BashOperations(Protocol):
    """Pluggable operations for the bash tool."""

    def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: OnData,
        signal: Any = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> BashExecResult: ...


def _get_shell_config(custom_shell_path: str | None = None) -> tuple[str, list[str]]:
    """Resolve the shell binary and its args (port of ``getShellConfig``)."""
    if custom_shell_path:
        if os.path.exists(custom_shell_path):
            return custom_shell_path, ["-c"]
        raise RuntimeError(f"Custom shell path not found: {custom_shell_path}")
    shell = os.environ.get("SHELL", "/bin/bash")
    return shell, ["-c"]


def _get_shell_env() -> dict[str, str]:
    return dict(os.environ)


class _LocalBashOperations:
    def __init__(self, shell_path: str | None = None) -> None:
        self._shell_path = shell_path

    def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: OnData,
        signal: Any = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> BashExecResult:
        shell, args = _get_shell_config(self._shell_path)
        if not os.path.exists(cwd):
            raise RuntimeError(
                f"Working directory does not exist: {cwd}\nCannot execute bash commands."
            )

        try:
            proc = subprocess.Popen(
                [shell, *args, command],
                cwd=cwd,
                env=env if env is not None else _get_shell_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
            )
        except FileNotFoundError as e:
            # Shell binary missing → surface an ENOENT-style error.
            raise RuntimeError(f"spawn {shell} ENOENT") from e

        timed_out = threading.Event()
        aborted = threading.Event()

        def _kill_tree() -> None:
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(proc.pid), 9)
                else:
                    proc.kill()
            except (ProcessLookupError, OSError):
                pass

        timer: threading.Timer | None = None
        if timeout is not None and timeout > 0:

            def _on_timeout() -> None:
                timed_out.set()
                _kill_tree()

            timer = threading.Timer(timeout, _on_timeout)
            timer.start()

        # Abort polling thread: kills the tree when the signal is aborted.
        stop_poll = threading.Event()

        def _poll_abort() -> None:
            while not stop_poll.wait(0.01):
                if signal is not None and getattr(signal, "aborted", False):
                    aborted.set()
                    _kill_tree()
                    return

        poller: threading.Thread | None = None
        if signal is not None:
            if getattr(signal, "aborted", False):
                aborted.set()
                _kill_tree()
            else:
                poller = threading.Thread(target=_poll_abort, daemon=True)
                poller.start()

        def _pump(stream: Any) -> None:
            for chunk in iter(lambda: stream.read(4096), b""):
                if chunk:
                    on_data(chunk)

        threads = []
        if proc.stdout is not None:
            t = threading.Thread(target=_pump, args=(proc.stdout,), daemon=True)
            t.start()
            threads.append(t)
        if proc.stderr is not None:
            t = threading.Thread(target=_pump, args=(proc.stderr,), daemon=True)
            t.start()
            threads.append(t)

        code = proc.wait()
        for t in threads:
            t.join()
        if timer is not None:
            timer.cancel()
        stop_poll.set()
        if poller is not None:
            poller.join(timeout=0.1)

        if signal is not None and getattr(signal, "aborted", False):
            raise RuntimeError("aborted")
        if timed_out.is_set():
            raise RuntimeError(f"timeout:{int(timeout) if timeout is not None else 0}")
        return BashExecResult(exit_code=code)


def create_local_bash_operations(shell_path: str | None = None) -> BashOperations:
    """Create bash operations using the built-in local shell backend."""
    return _LocalBashOperations(shell_path)


@dataclass
class BashToolOptions:
    operations: BashOperations | None = None
    command_prefix: str | None = None
    shell_path: str | None = None
    allowed_commands: list[str] | None = None
    denied_commands: list[str] | None = None
    max_output_bytes: int | None = None
    max_output_lines: int | None = None


def create_bash_tool(
    cwd: str,
    options: BashToolOptions | None = None,
) -> AgentTool[dict[str, Any], BashToolDetails | None]:
    """Create a bash tool."""
    ops: BashOperations = (
        options.operations
        if options and options.operations
        else create_local_bash_operations(options.shell_path if options else None)
    )
    command_prefix = options.command_prefix if options else None
    max_bytes = (
        options.max_output_bytes
        if options and options.max_output_bytes is not None
        else DEFAULT_MAX_BYTES
    )
    max_lines = (
        options.max_output_lines
        if options and options.max_output_lines is not None
        else DEFAULT_MAX_LINES
    )

    async def execute(
        tool_call_id: str,
        params: dict[str, Any],
        signal: Any = None,
        on_update: Callable[..., Any] | None = None,
    ) -> AgentToolResult[BashToolDetails | None]:
        command = params.get("command", "")
        timeout = params.get("timeout")

        denied_commands = options.denied_commands if options else None
        allowed_commands = options.allowed_commands if options else None

        if denied_commands:
            for pattern in denied_commands:
                try:
                    matches = re.search(pattern, command) is not None
                except re.error:
                    matches = False
                if matches:
                    raise RuntimeError(f'Command blocked: matches denied pattern "{pattern}"')

        if allowed_commands:
            permitted = False
            for pattern in allowed_commands:
                try:
                    if re.search(pattern, command) is not None:
                        permitted = True
                        break
                except re.error:
                    continue
            if not permitted:
                raise RuntimeError("Command blocked: does not match any allowed command pattern")

        resolved_command = f"{command_prefix}\n{command}" if command_prefix else command
        output = OutputAccumulator(
            temp_file_prefix="hoocode-bash", max_bytes=max_bytes, max_lines=max_lines
        )

        update_count = 0

        def emit_output_update() -> None:
            nonlocal update_count
            if on_update is None or not pending_update.is_set():
                return
            pending_update.clear()
            snapshot = output.snapshot(persist_if_truncated=True)
            update_count += 1
            on_update(
                AgentToolResult(
                    content=[TextContent(text=snapshot.content or "")],
                    details=BashToolDetails(
                        truncation=snapshot.truncation if snapshot.truncation.truncated else None,
                        full_output_path=snapshot.fullOutputPath,
                    ),
                )
            )

        if on_update is not None:
            on_update(AgentToolResult(content=[], details=None))

        pending_update = threading.Event()

        def handle_data(data: bytes) -> None:
            output.append(data)
            pending_update.set()

        def finish_output() -> Any:
            output.finish()
            emit_output_update()
            snapshot = output.snapshot(persist_if_truncated=True)
            output.close_temp_file()
            return snapshot

        def format_output(
            snapshot: Any, empty_text: str = "(no output)"
        ) -> tuple[str, BashToolDetails | None]:
            truncation = snapshot.truncation
            text = snapshot.content or empty_text
            details: BashToolDetails | None = None
            if truncation.truncated:
                details = BashToolDetails(
                    truncation=truncation, full_output_path=snapshot.fullOutputPath
                )
                start_line = truncation.total_lines - truncation.output_lines + 1
                end_line = truncation.total_lines
                if truncation.last_line_partial:
                    last_line_size = format_size(output.get_last_line_bytes())
                    text += (
                        f"\n\n[Showing last {format_size(truncation.output_bytes)} of line "
                        f"{end_line} (line is {last_line_size}). "
                        f"Full output: {snapshot.fullOutputPath}]"
                    )
                elif truncation.truncated_by == "lines":
                    text += (
                        f"\n\n[Showing lines {start_line}-{end_line} of "
                        f"{truncation.total_lines}. Full output: {snapshot.fullOutputPath}]"
                    )
                else:
                    text += (
                        f"\n\n[Showing lines {start_line}-{end_line} of "
                        f"{truncation.total_lines} ({format_size(max_bytes)} limit). "
                        f"Full output: {snapshot.fullOutputPath}]"
                    )
            return text, details

        def append_status(text: str, status: str) -> str:
            return f"{text}\n\n{status}" if text else status

        try:
            exit_code = ops.exec(
                resolved_command,
                cwd,
                on_data=handle_data,
                signal=signal,
                timeout=timeout,
                env=None,
            ).exit_code
        except RuntimeError as err:
            snapshot = finish_output()
            text, _ = format_output(snapshot, "")
            message = str(err)
            if message == "aborted":
                raise RuntimeError(append_status(text, "Command aborted")) from err
            if message.startswith("timeout:"):
                timeout_secs = message.split(":")[1]
                raise RuntimeError(
                    append_status(text, f"Command timed out after {timeout_secs} seconds")
                ) from err
            raise

        snapshot = finish_output()
        output_text, details = format_output(snapshot)
        if exit_code != 0 and exit_code is not None:
            raise RuntimeError(append_status(output_text, f"Command exited with code {exit_code}"))
        return AgentToolResult(content=[TextContent(text=output_text)], details=details)

    return AgentTool(
        name="bash",
        label="bash",
        description=(
            "Execute a bash command in the current working directory. Returns stdout "
            f"and stderr. Output is truncated to last {max_lines} lines or "
            f"{round(max_bytes / 1024)}KB (whichever is hit first). If truncated, full "
            "output is saved to a temp file. Optionally provide a timeout in seconds."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds (optional, no default timeout)",
                },
            },
            "required": ["command"],
        },
        execute=execute,
    )
