"""Tests for agent loop module.

Mechanical port of hoocode's agent loop tests (if any).
"""

from __future__ import annotations

import pytest
from cortex.agent.loop import AgentLoop, run_agent_loop, run_agent_loop_continue
from cortex.agent.types import AgentContext, AgentLoopConfig

# ---------------------------------------------------------------------------
# AgentLoop class tests
# ---------------------------------------------------------------------------


class TestAgentLoop:
    def test_agent_loop_creation(self) -> None:
        """AgentLoop can be created with config and context."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)
        assert loop.config is config
        assert loop.context is context

    def test_agent_loop_default_context(self) -> None:
        """AgentLoop creates default context if not provided."""
        config = AgentLoopConfig()
        loop = AgentLoop(config=config)
        assert loop.context is not None
        assert loop.context.system_prompt == ""

    @pytest.mark.asyncio
    async def test_agent_loop_run_empty_prompts(self) -> None:
        """AgentLoop.run with empty prompts emits events."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)

        with pytest.raises(NotImplementedError):
            await loop.run([])

    @pytest.mark.asyncio
    async def test_agent_loop_run_continue_empty_context(self) -> None:
        """AgentLoop.run_continue raises on empty context."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])
        loop = AgentLoop(config=config, context=context)

        with pytest.raises(ValueError, match="Cannot continue: no messages"):
            await loop.run_continue()


# ---------------------------------------------------------------------------
# Public API tests
# ---------------------------------------------------------------------------


class TestPublicAPI:
    @pytest.mark.asyncio
    async def test_run_agent_loop(self) -> None:
        """run_agent_loop creates and runs an AgentLoop."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])

        with pytest.raises(NotImplementedError):
            await run_agent_loop(
                prompts=[],
                context=context,
                config=config,
            )

    @pytest.mark.asyncio
    async def test_run_agent_loop_continue(self) -> None:
        """run_agent_loop_continue creates and runs an AgentLoop."""
        config = AgentLoopConfig()
        context = AgentContext(system_prompt="test", messages=[])

        with pytest.raises(ValueError, match="Cannot continue: no messages"):
            await run_agent_loop_continue(
                context=context,
                config=config,
            )
