# pyright: reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportReturnType=false
"""Subagent management for delegated task execution.

Port of ``subagent*.ts`` from ``packages/coding-agent/src/core/``.

Provides types and utilities for managing subagent tasks, results, and pools.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

# ============================================================================
# Subagent Types
# ============================================================================

# Maximum concurrent child processes
DEFAULT_MAX_CONCURRENCY = 5

# Default hard cap on assistant turns for a spawned subagent
DEFAULT_SUBAGENT_MAX_TURNS = 50

# AgentSession event types forwarded from a subagent's json event stream
SUBAGENT_PROGRESS_EVENTS = [
    "message",
    "message_end",
    "tool_execution",
    "tool_result",
    "error",
    "done",
]


@dataclass
class SubagentTask:
    """A task to be executed by a subagent."""

    task_id: str
    agent_type: str
    task: str
    context: str | None = None
    token_budget: int | None = None
    cwd: str | None = None
    model: str | None = None
    provider: str | None = None
    session_file: str | None = None
    use_inherited_model_fallback: bool = False

    @classmethod
    def create(
        cls,
        agent_type: str,
        task: str,
        context: str | None = None,
        token_budget: int | None = None,
        **kwargs: Any,
    ) -> SubagentTask:
        """Create a new task with auto-generated task_id."""
        return cls(
            task_id=f"dispatch-{uuid.uuid4().hex[:12]}",
            agent_type=agent_type,
            task=task,
            context=context,
            token_budget=token_budget,
            **kwargs,
        )


@dataclass
class SubagentSlot:
    """A slot representing an active subagent process."""

    pid: int
    agent_type: str
    task_id: str
    spawned_at: float
    token_budget: int
    process: Any = None


@dataclass
class SubagentResult:
    """Result of a subagent task execution."""

    task_id: str
    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    error: str | None = None
    budget_exceeded: bool = False
    status: Literal["complete", "partial", "failed", "stalled", "timeout", "cancelled"] | None = (
        None
    )
    result_data: dict[str, Any] | None = None
    used_inherited_model_fallback: bool = False


@dataclass
class TaskResult:
    """Result of a task dispatch evaluation."""

    handled_inline: bool
    task_id: str | None = None
    agent_type: str | None = None
    reason: str | None = None
    result: SubagentResult | None = None
    duration: float | None = None


@dataclass
class DispatchOptions:
    """Options for dispatching a subagent task."""

    force_agent: str | None = None
    context: str | None = None
    model: str | None = None
    provider: str | None = None
    session_file: str | None = None
    task_id: str | None = None


@dataclass
class SubagentPoolOptions:
    """Options for creating a subagent pool."""

    executable: str
    prefix_args: list[str] | None = None
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    cwd: str | None = None
    env: dict[str, str] | None = None
    default_token_budget: int = 0
    skill_paths: list[str] | None = None
    settings: dict[str, Any] | None = None
    available_models: list[dict[str, Any]] | None = None


# ============================================================================
# Subagent Depth Management
# ============================================================================

# Environment variable for tracking subagent depth
SUBAGENT_DEPTH_ENV = "CORTEX_SUBAGENT_DEPTH"

# Environment variable for skipping MCP schemas
SUBAGENT_SKIP_MCP_ENV = "CORTEX_SKIP_MCP"

# Environment variable for deferred MCP schemas
DEFER_MCP_SCHEMAS_ENV = "CORTEX_DEFER_MCP_SCHEMAS"


def get_current_subagent_depth() -> int:
    """Get the current subagent depth from environment."""
    return int(os.environ.get(SUBAGENT_DEPTH_ENV, "0"))


def resolve_max_subagent_depth(settings: dict[str, Any] | None = None) -> int:
    """Resolve the maximum subagent depth.

    Args:
        settings: Settings dictionary with maxSubagentDepth.

    Returns:
        Maximum subagent depth.
    """
    if settings and "maxSubagentDepth" in settings:
        return int(settings["maxSubagentDepth"])
    return 5  # Default max depth


def can_spawn_subagent(settings: dict[str, Any] | None = None) -> bool:
    """Check if we can spawn a new subagent based on depth limits.

    Args:
        settings: Settings dictionary.

    Returns:
        True if we can spawn a new subagent.
    """
    current_depth = get_current_subagent_depth()
    max_depth = resolve_max_subagent_depth(settings)
    return current_depth < max_depth


# ============================================================================
# Task Evaluation
# ============================================================================


def evaluate_task(task: str, context: str | None = None) -> TaskResult:
    """Evaluate a task to determine if it should be handled inline or delegated.

    This is a simplified stub. In the real implementation, this would use
    an LLM to evaluate the task complexity.

    Args:
        task: Task description.
        context: Optional context.

    Returns:
        TaskResult indicating whether to handle inline or delegate.
    """
    # Simple heuristic: short tasks are handled inline
    if len(task.split()) < 10:
        return TaskResult(handled_inline=True, reason="Task is simple enough for inline handling")

    return TaskResult(
        handled_inline=False,
        reason="Task is complex enough to warrant delegation",
    )


# ============================================================================
# Subagent Pool
# ============================================================================


class SubagentPool:
    """Pool for managing subagent processes."""

    def __init__(self, options: SubagentPoolOptions):
        """Initialize the subagent pool.

        Args:
            options: Pool configuration options.
        """
        self.options = options
        self.slots: dict[str, SubagentSlot] = {}
        self.results: dict[str, SubagentResult] = {}

    def get_active_count(self) -> int:
        """Get the number of active subagent processes."""
        return len(self.slots)

    def can_dispatch(self) -> bool:
        """Check if we can dispatch a new subagent."""
        return self.get_active_count() < self.options.max_concurrency

    def register_task(self, task: SubagentTask) -> SubagentSlot:
        """Register a new task in the pool.

        Args:
            task: Task to register.

        Returns:
            SubagentSlot for the task.

        Raises:
            RuntimeError: If pool is at capacity.
        """
        if not self.can_dispatch():
            raise RuntimeError(f"Pool at capacity ({self.options.max_concurrency} active)")

        slot = SubagentSlot(
            pid=0,  # Will be set when process is spawned
            agent_type=task.agent_type,
            task_id=task.task_id,
            spawned_at=time.time(),
            token_budget=task.token_budget or self.options.default_token_budget,
        )
        self.slots[task.task_id] = slot
        return slot

    def complete_task(self, task_id: str, result: SubagentResult) -> None:
        """Mark a task as completed and remove from active slots.

        Args:
            task_id: ID of the completed task.
            result: Result of the task execution.
        """
        if task_id in self.slots:
            del self.slots[task_id]
        self.results[task_id] = result

    def get_result(self, task_id: str) -> SubagentResult | None:
        """Get the result of a completed task.

        Args:
            task_id: ID of the task.

        Returns:
            SubagentResult if completed, None otherwise.
        """
        return self.results.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel an active task.

        Args:
            task_id: ID of the task to cancel.

        Returns:
            True if task was cancelled, False if not found.
        """
        if task_id not in self.slots:
            return False

        result = SubagentResult(
            task_id=task_id,
            ok=False,
            stdout="",
            stderr="Task cancelled",
            exit_code=None,
            status="cancelled",
        )
        self.complete_task(task_id, result)
        return True

    def get_status(self) -> dict[str, Any]:
        """Get pool status.

        Returns:
            Dictionary with pool status information.
        """
        return {
            "active_count": self.get_active_count(),
            "max_concurrency": self.options.max_concurrency,
            "can_dispatch": self.can_dispatch(),
            "completed_count": len(self.results),
        }
