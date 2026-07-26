"""Tests for agents formatting.

Tests verify:
- Agents are formatted as XML
- Empty agents list returns empty string
"""

from __future__ import annotations

from cortex.code.prompts.agents import format_agents_for_prompt
from cortex.code.prompts.types import AgentDefinition


class TestFormatAgentsForPrompt:
    def test_empty_agents_returns_empty_string(self):
        result = format_agents_for_prompt([])
        assert result == ""

    def test_formats_agents_as_xml(self):
        agents = [
            AgentDefinition(
                name="explore",
                description="An exploration agent",
                prompt="Explore the codebase",
                source="builtin",
            )
        ]
        result = format_agents_for_prompt(agents)

        assert "<available_agents>" in result
        assert "</available_agents>" in result
        assert "<agent>" in result
        assert "</agent>" in result
        assert "<name>explore</name>" in result
        assert "<description>An exploration agent</description>" in result

    def test_includes_tools_when_present(self):
        agents = [
            AgentDefinition(
                name="test-agent",
                description="An agent with tools",
                prompt="Test agent",
                source="builtin",
                tools=["read", "grep"],
            )
        ]
        result = format_agents_for_prompt(agents)

        assert "<tools>read, grep</tools>" in result

    def test_escapes_xml_in_description(self):
        agents = [
            AgentDefinition(
                name="test-agent",
                description='An agent with <special> & "chars"',
                prompt="Test agent",
                source="builtin",
            )
        ]
        result = format_agents_for_prompt(agents)

        assert "&lt;special&gt;" in result
        assert "&amp;" in result
        assert "&quot;" in result

    def test_multiple_agents(self):
        agents = [
            AgentDefinition(
                name="agent-1",
                description="First agent",
                prompt="Agent 1",
                source="builtin",
            ),
            AgentDefinition(
                name="agent-2",
                description="Second agent",
                prompt="Agent 2",
                source="builtin",
            ),
        ]
        result = format_agents_for_prompt(agents)

        assert "agent-1" in result
        assert "agent-2" in result
        assert result.count("<agent>") == 2
