"""Tests for system prompt formatting.

Mechanical port of hoocode's ``packages/agent/test/harness/system-prompt.test.ts``.
"""

from __future__ import annotations

import pytest
from cortex.agent.harness.system_prompt import format_skills_for_system_prompt
from cortex.agent.harness.types import Skill


@pytest.fixture
def visible_skill() -> Skill:
    """A visible skill for testing."""
    return Skill(
        name="visible",
        description="Use <this> & that",
        content="visible content",
        file_path="/skills/visible/SKILL.md",
    )


@pytest.fixture
def second_skill() -> Skill:
    """A second visible skill for testing."""
    return Skill(
        name="second",
        description="Second skill",
        content="second content",
        file_path="/skills/second/SKILL.md",
    )


@pytest.fixture
def disabled_skill() -> Skill:
    """A disabled skill for testing."""
    return Skill(
        name="hidden",
        description="Hidden",
        content="hidden content",
        file_path="/skills/hidden/SKILL.md",
        disable_model_invocation=True,
    )


class TestFormatSkillsForSystemPrompt:
    """Tests for format_skills_for_system_prompt."""

    def test_formats_visible_skills_in_order_and_skips_model_disabled_skills(
        self,
        visible_skill: Skill,
        second_skill: Skill,
        disabled_skill: Skill,
    ) -> None:
        """Visible skills are formatted in order; disabled skills are skipped."""
        result = format_skills_for_system_prompt([visible_skill, disabled_skill, second_skill])

        expected = (
            "The following skills provide specialized instructions for specific tasks.\n"
            "Read the full skill file when the task matches its description.\n"
            "When a skill file references a relative path, resolve it against the skill"
            " directory (parent of SKILL.md / dirname of the path) and use that absolute"
            " path in tool commands.\n"
            "\n"
            "<available_skills>\n"
            "  <skill>\n"
            "    <name>visible</name>\n"
            "    <description>Use &lt;this&gt; &amp; that</description>\n"
            "    <location>/skills/visible/SKILL.md</location>\n"
            "  </skill>\n"
            "  <skill>\n"
            "    <name>second</name>\n"
            "    <description>Second skill</description>\n"
            "    <location>/skills/second/SKILL.md</location>\n"
            "  </skill>\n"
            "</available_skills>"
        )
        assert result == expected

    def test_returns_empty_string_when_no_skills_are_model_visible(
        self,
        disabled_skill: Skill,
    ) -> None:
        """Returns empty string when all skills are disabled."""
        result = format_skills_for_system_prompt([disabled_skill])
        assert result == ""

    def test_escapes_xml_in_all_model_visible_skill_fields(self) -> None:
        """XML characters are properly escaped in skill fields."""
        skill = Skill(
            name="a&b",
            description="Quote \"double\" and 'single'",
            content="content",
            file_path='/skills/<bad>&"quote"/SKILL.md',
        )
        result = format_skills_for_system_prompt([skill])

        assert "<name>a&amp;b</name>" in result
        assert (
            "<description>Quote &quot;double&quot; and &apos;single&apos;</description>" in result
        )
        assert "<location>/skills/&lt;bad&gt;&amp;&quot;quote&quot;/SKILL.md</location>" in result

    def test_empty_list_returns_empty_string(self) -> None:
        """Empty skill list returns empty string."""
        result = format_skills_for_system_prompt([])
        assert result == ""
