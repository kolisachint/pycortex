# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Auto-retry controller for AgentSession.

Port of ``agent-session-retry.ts`` from ``packages/coding-agent/src/core/``.
Owns the retry lifecycle for transient assistant errors (overloaded, rate
limit, server/network/transport failures).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

# Retryable error signatures (compiled once at module load)
_RETRYABLE_ERROR_PATTERN = re.compile(
    r"overloaded|provider.?returned.?error|rate.?limit|too many requests|"
    r"429|500|502|503|504|service.?unavailable|server.?error|internal.?error|"
    r"network.?error|connection.?error|connection.?refused|connection.?lost|"
    r"websocket.?closed|websocket.?error|other side closed|fetch failed|"
    r"upstream.?connect|reset before headers|socket hang up|ended without|"
    r"http2 request did not get a response|timed? out|timeout|terminated|retry delay",
    re.IGNORECASE,
)


class AutoRetryController:
    """Auto-retry controller for transient assistant errors."""

    def __init__(self, deps: Any) -> None:
        self._deps = deps
        self._abort_event = asyncio.Event()
        self._attempt = 0
        self._promise: asyncio.Future[None] | None = None
        self._resolve: Callable[[], None] | None = None

    @property
    def attempt(self) -> int:
        """Current retry attempt (0 if not retrying)."""
        return self._attempt

    @property
    def is_retrying(self) -> bool:
        """Whether a retry is currently in progress."""
        return self._promise is not None

    def is_retryable_error(self, message: dict[str, Any]) -> bool:
        """Check if an error is retryable.

        Context overflow errors are NOT retryable (handled by compaction instead).
        """
        if message.get("stopReason") != "error" or not message.get("errorMessage"):
            return False

        # Context overflow is handled by compaction, not retry
        model = self._deps.get_model()
        context_window = getattr(model, "context_window", 0) or 0
        if self._is_context_overflow(message, context_window):
            return False

        return bool(_RETRYABLE_ERROR_PATTERN.search(message.get("errorMessage", "")))

    def _is_context_overflow(self, message: dict[str, Any], context_window: int) -> bool:
        """Check if error is context overflow."""
        error_msg = message.get("errorMessage", "").lower()
        return "context" in error_msg and ("overflow" in error_msg or "length" in error_msg)

    def create_promise_for_agent_end(self, messages: list[dict[str, Any]]) -> None:
        """Create the retry promise synchronously when an agent_end carries a retryable error."""
        if self._promise is not None:
            return

        settings = self._deps.get_retry_settings()
        if not settings.get("enabled", False):
            return

        last_assistant = self._find_last_assistant(messages)
        if not last_assistant or not self.is_retryable_error(last_assistant):
            return

        self._promise = asyncio.get_event_loop().create_future()
        self._resolve = lambda: (
            self._promise.set_result(None) if self._promise and not self._promise.done() else None
        )

    def _find_last_assistant(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Find the last assistant message in messages."""
        for message in reversed(messages):
            if message.get("role") == "assistant":
                return message
        return None

    def on_successful_assistant_response(self) -> None:
        """Reset the attempt counter after a successful assistant response."""
        if self._attempt > 0:
            self._deps.emit(
                {
                    "type": "auto_retry_end",
                    "success": True,
                    "attempt": self._attempt,
                }
            )
            self._attempt = 0

    def resolve(self) -> None:
        """Resolve the pending retry promise."""
        if self._resolve:
            self._resolve()
            self._resolve = None
            self._promise = None

    async def handle_retryable_error(self, message: dict[str, Any]) -> bool:
        """Handle retryable errors with exponential backoff.

        Returns True if retry was initiated, False if max retries exceeded or disabled.
        """
        settings = self._deps.get_retry_settings()
        if not settings.get("enabled", False):
            self.resolve()
            return False

        # Create promise if not already created
        if self._promise is None:
            self._promise = asyncio.get_event_loop().create_future()
            self._resolve = lambda: (
                self._promise.set_result(None)
                if self._promise and not self._promise.done()
                else None
            )

        self._attempt += 1

        if self._attempt > settings.get("maxRetries", 3):
            # Max retries exceeded
            self._deps.emit(
                {
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": self._attempt - 1,
                    "finalError": message.get("errorMessage"),
                }
            )
            self._attempt = 0
            self.resolve()
            return False

        delay_ms = settings.get("baseDelayMs", 1000) * (2 ** (self._attempt - 1))

        self._deps.emit(
            {
                "type": "auto_retry_start",
                "attempt": self._attempt,
                "maxAttempts": settings.get("maxRetries", 3),
                "delayMs": delay_ms,
                "errorMessage": message.get("errorMessage") or "Unknown error",
            }
        )

        # Remove error message from agent state (keep in session for history)
        messages = self._deps.get_agent_messages()
        if messages and messages[-1].get("role") == "assistant":
            self._deps.set_agent_messages(messages[:-1])

        # Wait with exponential backoff (abortable)
        self._abort_event.clear()
        try:
            await asyncio.wait_for(
                self._abort_event.wait(),
                timeout=delay_ms / 1000,
            )
            # Aborted during sleep
            attempt = self._attempt
            self._attempt = 0
            self._deps.emit(
                {
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": attempt,
                    "finalError": "Retry cancelled",
                }
            )
            self.resolve()
            return False
        except TimeoutError:
            # Timeout expired - proceed with retry
            pass

        # Retry via continue()
        asyncio.get_event_loop().call_soon(self._deps.continue_agent)
        return True

    def abort(self) -> None:
        """Cancel in-progress retry."""
        self._abort_event.set()
        self.resolve()

    async def wait_for_retry(self) -> None:
        """Wait for any in-progress retry to complete."""
        if not self._promise:
            return
        await self._promise
        await self._deps.wait_for_agent_idle()
