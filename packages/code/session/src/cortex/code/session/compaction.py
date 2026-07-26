# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnusedCallResult=false, reportCallIssue=false, reportReturnType=false, reportMissingTypeArgument=false
"""Compaction controller for AgentSession.

Port of ``agent-session-compaction.ts`` from ``packages/coding-agent/src/core/``.
Owns manual compaction (the /compact flow) and automatic compaction (overflow
recovery and threshold-triggered).
"""

from __future__ import annotations

import asyncio
from typing import Any


class CompactionController:
    """Compaction controller for AgentSession."""

    def __init__(self, deps: Any) -> None:
        self._deps = deps
        self._compaction_abort: asyncio.Event | None = None
        self._auto_compaction_abort: asyncio.Event | None = None
        self._overflow_recovery_attempted = False

    @property
    def is_compacting(self) -> bool:
        """Whether manual or auto compaction is currently running."""
        return self._auto_compaction_abort is not None or self._compaction_abort is not None

    @property
    def auto_compaction_enabled(self) -> bool:
        """Whether auto-compaction is enabled."""
        settings = self._deps.settings_manager.get_compaction_settings()
        return settings.get("enabled", True)

    def set_auto_compaction_enabled(self, enabled: bool) -> None:
        """Toggle auto-compaction setting."""
        self._deps.settings_manager.set_compaction_enabled(enabled)

    def reset_overflow_recovery(self) -> None:
        """Clear the one-shot overflow-recovery guard."""
        self._overflow_recovery_attempted = False

    def abort_compaction(self) -> None:
        """Cancel in-progress compaction (manual or auto)."""
        if self._compaction_abort:
            self._compaction_abort.set()
        if self._auto_compaction_abort:
            self._auto_compaction_abort.set()

    async def compact(self, custom_instructions: str | None = None) -> dict[str, Any]:
        """Manually compact the session context.

        Aborts current agent operation first.
        """
        self._deps.disconnect_from_agent()
        await self._deps.abort_session()
        self._compaction_abort = asyncio.Event()
        self._deps.emit({"type": "compaction_start", "reason": "manual"})

        try:
            model = self._deps.get_model()
            if not model:
                raise ValueError("No model selected")

            auth_result = await self._deps.get_required_request_auth(model)
            api_key = auth_result.get("apiKey", "")
            headers = auth_result.get("headers")

            path_entries = self._deps.session_manager.get_branch()
            settings = self._deps.settings_manager.get_compaction_settings()

            preparation = self._prepare_compaction(path_entries, settings)
            if not preparation:
                last_entry = path_entries[-1] if path_entries else None
                if last_entry and last_entry.get("type") == "compaction":
                    raise ValueError("Already compacted")
                raise ValueError("Nothing to compact (session too small)")

            result = await self._apply_compaction(
                preparation=preparation,
                branch_entries=path_entries,
                model=model,
                api_key=api_key,
                headers=headers,
                custom_instructions=custom_instructions,
                signal=self._compaction_abort,
            )

            if result["status"] == "cancelled":
                raise ValueError("Compaction cancelled")

            compaction_result = result["result"]
            self._deps.emit(
                {
                    "type": "compaction_end",
                    "reason": "manual",
                    "result": compaction_result,
                    "aborted": False,
                    "willRetry": False,
                }
            )
            return compaction_result
        except Exception as error:
            message = str(error)
            aborted = "cancelled" in message.lower()
            self._deps.emit(
                {
                    "type": "compaction_end",
                    "reason": "manual",
                    "result": None,
                    "aborted": aborted,
                    "willRetry": False,
                    "errorMessage": None if aborted else f"Compaction failed: {message}",
                }
            )
            raise
        finally:
            self._compaction_abort = None
            self._deps.reconnect_to_agent()

    async def check_compaction(
        self,
        assistant_message: dict[str, Any],
        skip_aborted_check: bool = True,
    ) -> None:
        """Check if compaction is needed and run it.

        Called after agent_end and before prompt submission.
        """
        settings = self._deps.settings_manager.get_compaction_settings()
        if not settings.get("enabled", True):
            return

        if skip_aborted_check and assistant_message.get("stopReason") == "aborted":
            return

        model = self._deps.get_model()
        context_window = getattr(model, "context_window", 0) or 0

        # Skip overflow check if message came from different model
        same_model = (
            model
            and assistant_message.get("provider") == getattr(model, "provider", None)
            and assistant_message.get("model") == getattr(model, "id", None)
        )

        # Skip if assistant is from before compaction
        branch_entries = self._deps.session_manager.get_branch()
        compaction_entry = self._get_latest_compaction_entry(branch_entries)
        if compaction_entry:
            try:
                from datetime import datetime

                compaction_ts = (
                    datetime.fromisoformat(compaction_entry.get("timestamp", "")).timestamp() * 1000
                )
                assistant_ts = assistant_message.get("timestamp", 0)
                if isinstance(assistant_ts, str):
                    assistant_ts = datetime.fromisoformat(assistant_ts).timestamp() * 1000
                if assistant_ts <= compaction_ts:
                    return
            except (ValueError, TypeError):
                pass

        # Case 1: Overflow - LLM returned context overflow error
        if same_model and self._is_context_overflow(assistant_message, context_window):
            if self._overflow_recovery_attempted:
                self._deps.emit(
                    {
                        "type": "compaction_end",
                        "reason": "overflow",
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                        "errorMessage": (
                            "Context overflow recovery failed after one compact-and-retry attempt."
                        ),
                    }
                )
                return

            self._overflow_recovery_attempted = True
            # Remove error message from agent state
            messages = self._deps.get_agent_messages()
            if messages and messages[-1].get("role") == "assistant":
                self._deps.set_agent_messages(messages[:-1])
            await self._run_auto_compaction("overflow", will_retry=True)
            return

        # Case 2: Threshold - context is getting large
        context_tokens = self._calculate_context_tokens(assistant_message)
        if self._should_compact(context_tokens, context_window, settings):
            await self._run_auto_compaction("threshold", will_retry=False)

    def _is_context_overflow(self, message: dict[str, Any], context_window: int) -> bool:
        """Check if error is context overflow."""
        error_msg = message.get("errorMessage", "").lower()
        return "context" in error_msg and ("overflow" in error_msg or "length" in error_msg)

    def _calculate_context_tokens(self, message: dict[str, Any]) -> int:
        """Calculate context tokens from assistant message."""
        usage = message.get("usage", {})
        return usage.get("input", 0) + usage.get("output", 0)

    def _should_compact(
        self, context_tokens: int, context_window: int, settings: dict[str, Any]
    ) -> bool:
        """Check if compaction should be triggered based on threshold."""
        if context_window <= 0:
            return False
        threshold = settings.get("threshold", 0.8)
        return (context_tokens / context_window) > threshold

    def _get_latest_compaction_entry(self, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Get the latest compaction entry."""
        for entry in reversed(entries):
            if entry.get("type") == "compaction":
                return entry
        return None

    def _prepare_compaction(
        self, entries: list[dict[str, Any]], settings: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Prepare compaction - simplified version."""
        messages = [e for e in entries if e.get("type") == "message"]
        if len(messages) < 10:
            return None

        # Simple preparation: mark first half as to-be-compacted
        first_kept = messages[len(messages) // 2]
        return {
            "messages": messages,
            "firstKeptEntryId": first_kept.get("id"),
            "estimatedTokens": sum(
                m.get("message", {}).get("usage", {}).get("input", 0) for m in messages
            ),
        }

    async def _apply_compaction(
        self,
        preparation: dict[str, Any],
        branch_entries: list[dict[str, Any]],
        model: Any,
        api_key: str,
        headers: dict[str, str] | None = None,
        custom_instructions: str | None = None,
        signal: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        """Shared core for manual and auto compaction."""
        # Check for abort
        if signal and signal.is_set():
            return {"status": "cancelled"}

        # Generate summary (simplified - in real implementation this calls LLM)
        summary = f"Compacted {len(branch_entries)} entries"
        first_kept_entry_id = preparation.get("firstKeptEntryId", "")
        tokens_before = preparation.get("estimatedTokens", 0)
        tokens_after = tokens_before // 2

        # Persist compaction
        self._deps.session_manager.append_compaction(
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

        # Update agent messages
        new_entries = self._deps.session_manager.get_entries()
        self._deps.set_agent_messages(new_entries)

        return {
            "status": "ok",
            "result": {
                "summary": summary,
                "firstKeptEntryId": first_kept_entry_id,
                "tokensBefore": tokens_before,
                "tokensAfter": tokens_after,
                "details": None,
            },
        }

    async def _run_auto_compaction(self, reason: str, will_retry: bool) -> None:
        """Run auto-compaction with events."""
        settings = self._deps.settings_manager.get_compaction_settings()
        self._deps.emit({"type": "compaction_start", "reason": reason})
        self._auto_compaction_abort = asyncio.Event()

        try:
            model = self._deps.get_model()
            if not model:
                self._deps.emit(
                    {
                        "type": "compaction_end",
                        "reason": reason,
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                    }
                )
                return

            auth_result = await self._deps.model_registry.get_api_key_and_headers(model)
            if not auth_result.get("ok") or not auth_result.get("apiKey"):
                self._deps.emit(
                    {
                        "type": "compaction_end",
                        "reason": reason,
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                    }
                )
                return

            path_entries = self._deps.session_manager.get_branch()
            preparation = self._prepare_compaction(path_entries, settings)
            if not preparation:
                self._deps.emit(
                    {
                        "type": "compaction_end",
                        "reason": reason,
                        "result": None,
                        "aborted": False,
                        "willRetry": False,
                    }
                )
                return

            result = await self._apply_compaction(
                preparation=preparation,
                branch_entries=path_entries,
                model=model,
                api_key=auth_result["apiKey"],
                headers=auth_result.get("headers"),
                signal=self._auto_compaction_abort,
            )

            if result["status"] == "cancelled":
                self._deps.emit(
                    {
                        "type": "compaction_end",
                        "reason": reason,
                        "result": None,
                        "aborted": True,
                        "willRetry": False,
                    }
                )
                return

            compaction_result = result["result"]
            self._deps.emit(
                {
                    "type": "compaction_end",
                    "reason": reason,
                    "result": compaction_result,
                    "aborted": False,
                    "willRetry": will_retry,
                }
            )

            if will_retry:
                messages = self._deps.get_agent_messages()
                if messages and messages[-1].get("role") == "assistant":
                    self._deps.set_agent_messages(messages[:-1])
                await asyncio.sleep(0.1)
                self._deps.continue_agent()
            elif self._deps.has_queued_messages():
                await asyncio.sleep(0.1)
                self._deps.continue_agent()

        except Exception as error:
            error_message = str(error)
            self._deps.emit(
                {
                    "type": "compaction_end",
                    "reason": reason,
                    "result": None,
                    "aborted": False,
                    "willRetry": False,
                    "errorMessage": (
                        f"Context overflow recovery failed: {error_message}"
                        if reason == "overflow"
                        else f"Auto-compaction failed: {error_message}"
                    ),
                }
            )
        finally:
            self._auto_compaction_abort = None
