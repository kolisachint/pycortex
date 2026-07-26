# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for subagent management.

Tests verify:
- Subagent task creation
- Subagent result handling
- Task evaluation
- Subagent pool management
- Subagent depth management
"""

from __future__ import annotations

import os

import pytest
from cortex.code.subagents import (
    SubagentPool,
    SubagentPoolOptions,
    SubagentResult,
    SubagentSlot,
    SubagentTask,
    TaskResult,
    can_spawn_subagent,
    evaluate_task,
    get_current_subagent_depth,
    resolve_max_subagent_depth,
)


class TestSubagentTask:
    def test_default_values(self):
        task = SubagentTask(
            task_id="task-123",
            agent_type="explore",
            task="Explore the codebase",
        )
        assert task.task_id == "task-123"
        assert task.agent_type == "explore"
        assert task.task == "Explore the codebase"
        assert task.context is None
        assert task.token_budget is None
        assert task.cwd is None
        assert task.model is None
        assert task.provider is None
        assert task.session_file is None
        assert task.use_inherited_model_fallback is False

    def test_create_task(self):
        task = SubagentTask.create(
            agent_type="explore",
            task="Explore the codebase",
            context="Focus on the main module",
            token_budget=10000,
        )
        assert task.task_id.startswith("dispatch-")
        assert task.agent_type == "explore"
        assert task.task == "Explore the codebase"
        assert task.context == "Focus on the main module"
        assert task.token_budget == 10000

    def test_create_task_with_kwargs(self):
        task = SubagentTask.create(
            agent_type="explore",
            task="Explore",
            model="claude-sonnet-4-5-20250514",
            provider="anthropic",
        )
        assert task.model == "claude-sonnet-4-5-20250514"
        assert task.provider == "anthropic"


class TestSubagentSlot:
    def test_default_values(self):
        slot = SubagentSlot(
            pid=12345,
            agent_type="explore",
            task_id="task-123",
            spawned_at=1234567890.0,
            token_budget=10000,
        )
        assert slot.pid == 12345
        assert slot.agent_type == "explore"
        assert slot.task_id == "task-123"
        assert slot.spawned_at == 1234567890.0
        assert slot.token_budget == 10000
        assert slot.process is None


class TestSubagentResult:
    def test_default_values(self):
        result = SubagentResult(
            task_id="task-123",
            ok=True,
            stdout="Success",
            stderr="",
            exit_code=0,
        )
        assert result.task_id == "task-123"
        assert result.ok is True
        assert result.stdout == "Success"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.error is None
        assert result.budget_exceeded is False
        assert result.status is None
        assert result.result_data is None
        assert result.used_inherited_model_fallback is False

    def test_with_error(self):
        result = SubagentResult(
            task_id="task-123",
            ok=False,
            stdout="",
            stderr="Error occurred",
            exit_code=1,
            error="Connection failed",
            status="failed",
        )
        assert result.ok is False
        assert result.error == "Connection failed"
        assert result.status == "failed"

    def test_with_status(self):
        result = SubagentResult(
            task_id="task-123",
            ok=True,
            stdout="Partial result",
            stderr="",
            exit_code=0,
            status="partial",
            result_data={"key": "value"},
        )
        assert result.status == "partial"
        assert result.result_data == {"key": "value"}


class TestTaskResult:
    def test_handled_inline(self):
        result = TaskResult(handled_inline=True, reason="Simple task")
        assert result.handled_inline is True
        assert result.reason == "Simple task"
        assert result.task_id is None
        assert result.result is None

    def test_delegated(self):
        task = SubagentTask.create(agent_type="explore", task="Explore")
        subagent_result = SubagentResult(
            task_id=task.task_id,
            ok=True,
            stdout="Done",
            stderr="",
            exit_code=0,
        )
        result = TaskResult(
            handled_inline=False,
            task_id=task.task_id,
            agent_type="explore",
            reason="Complex task",
            result=subagent_result,
            duration=1500.0,
        )
        assert result.handled_inline is False
        assert result.task_id == task.task_id
        assert result.agent_type == "explore"
        assert result.result == subagent_result
        assert result.duration == 1500.0


class TestSubagentDepth:
    def test_get_current_depth(self):
        # Clear env var if set
        os.environ.pop("CORTEX_SUBAGENT_DEPTH", None)

        depth = get_current_subagent_depth()
        assert depth == 0

    def test_get_depth_from_env(self):
        os.environ["CORTEX_SUBAGENT_DEPTH"] = "3"
        depth = get_current_subagent_depth()
        os.environ.pop("CORTEX_SUBAGENT_DEPTH", None)

        assert depth == 3

    def test_resolve_max_depth(self):
        max_depth = resolve_max_subagent_depth()
        assert max_depth == 5  # Default

    def test_resolve_max_depth_from_settings(self):
        settings = {"maxSubagentDepth": 3}
        max_depth = resolve_max_subagent_depth(settings)
        assert max_depth == 3

    def test_can_spawn_subagent(self):
        os.environ.pop("CORTEX_SUBAGENT_DEPTH", None)
        assert can_spawn_subagent() is True

    def test_cannot_spawn_at_max_depth(self):
        os.environ["CORTEX_SUBAGENT_DEPTH"] = "5"
        can_spawn = can_spawn_subagent()
        os.environ.pop("CORTEX_SUBAGENT_DEPTH", None)

        assert can_spawn is False


class TestEvaluateTask:
    def test_simple_task(self):
        result = evaluate_task("Read file")
        assert result.handled_inline is True
        assert result.reason is not None

    def test_complex_task(self):
        result = evaluate_task(
            "Implement a new feature that adds user authentication with OAuth2 "
            "support including Google and GitHub providers, with proper error handling "
            "and session management"
        )
        assert result.handled_inline is False
        assert result.reason is not None


class TestSubagentPool:
    def test_initialization(self):
        options = SubagentPoolOptions(executable="/bin/test")
        pool = SubagentPool(options)
        assert pool.get_active_count() == 0
        assert pool.can_dispatch() is True

    def test_register_task(self):
        options = SubagentPoolOptions(executable="/bin/test", max_concurrency=2)
        pool = SubagentPool(options)
        task = SubagentTask.create(agent_type="explore", task="Explore")

        slot = pool.register_task(task)
        assert slot.agent_type == "explore"
        assert slot.task_id == task.task_id
        assert pool.get_active_count() == 1
        assert pool.can_dispatch() is True

    def test_register_multiple_tasks(self):
        options = SubagentPoolOptions(executable="/bin/test", max_concurrency=2)
        pool = SubagentPool(options)
        task1 = SubagentTask.create(agent_type="explore", task="Task 1")
        task2 = SubagentTask.create(agent_type="explore", task="Task 2")

        pool.register_task(task1)
        pool.register_task(task2)

        assert pool.get_active_count() == 2
        assert pool.can_dispatch() is False

    def test_register_task_at_capacity(self):
        options = SubagentPoolOptions(executable="/bin/test", max_concurrency=1)
        pool = SubagentPool(options)
        task1 = SubagentTask.create(agent_type="explore", task="Task 1")
        task2 = SubagentTask.create(agent_type="explore", task="Task 2")

        pool.register_task(task1)

        with pytest.raises(RuntimeError, match="Pool at capacity"):
            pool.register_task(task2)

    def test_complete_task(self):
        options = SubagentPoolOptions(executable="/bin/test")
        pool = SubagentPool(options)
        task = SubagentTask.create(agent_type="explore", task="Explore")
        pool.register_task(task)

        result = SubagentResult(
            task_id=task.task_id,
            ok=True,
            stdout="Done",
            stderr="",
            exit_code=0,
        )
        pool.complete_task(task.task_id, result)

        assert pool.get_active_count() == 0
        assert pool.get_result(task.task_id) == result

    def test_cancel_task(self):
        options = SubagentPoolOptions(executable="/bin/test")
        pool = SubagentPool(options)
        task = SubagentTask.create(agent_type="explore", task="Explore")
        pool.register_task(task)

        cancelled = pool.cancel_task(task.task_id)
        assert cancelled is True
        assert pool.get_active_count() == 0
        assert pool.get_result(task.task_id) is not None

    def test_cancel_nonexistent_task(self):
        options = SubagentPoolOptions(executable="/bin/test")
        pool = SubagentPool(options)

        cancelled = pool.cancel_task("nonexistent")
        assert cancelled is False

    def test_get_status(self):
        options = SubagentPoolOptions(executable="/bin/test", max_concurrency=5)
        pool = SubagentPool(options)

        status = pool.get_status()
        assert status["active_count"] == 0
        assert status["max_concurrency"] == 5
        assert status["can_dispatch"] is True
        assert status["completed_count"] == 0
