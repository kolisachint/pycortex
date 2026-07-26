"""Tests for skill loading and formatting.

Mechanical port of hoocode's ``packages/agent/test/harness/skills.test.ts``.
"""

from __future__ import annotations

from cortex.agent.harness.skills import format_skill_invocation
from cortex.agent.harness.types import Skill


class TestFormatSkillInvocation:
    """Tests for format_skill_invocation."""

    def test_basic_invocation(self) -> None:
        """Basic skill invocation format."""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="Use this skill for testing.",
            file_path="/skills/test-skill/SKILL.md",
        )
        result = format_skill_invocation(skill)

        assert '<skill name="test-skill"' in result
        assert 'location="/skills/test-skill/SKILL.md"' in result
        assert "References are relative to /skills" in result
        assert "Use this skill for testing." in result
        assert "</skill>" in result

    def test_invocation_with_additional_instructions(self) -> None:
        """Skill invocation with additional instructions."""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="Use this skill.",
            file_path="/skills/test-skill/SKILL.md",
        )
        result = format_skill_invocation(skill, "Additional instructions here")

        assert "Use this skill." in result
        assert "Additional instructions here" in result
        assert result.endswith("Additional instructions here")

    def test_invocation_without_additional_instructions(self) -> None:
        """Skill invocation without additional instructions."""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="Use this skill.",
            file_path="/skills/test-skill/SKILL.md",
        )
        result = format_skill_invocation(skill)

        assert result.endswith("</skill>")

    def test_empty_content(self) -> None:
        """Skill with empty content."""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="",
            file_path="/skills/test-skill/SKILL.md",
        )
        result = format_skill_invocation(skill)

        assert '<skill name="test-skill"' in result
        assert "</skill>" in result

    def test_nested_path(self) -> None:
        """Skill with nested path."""
        skill = Skill(
            name="nested-skill",
            description="A nested skill",
            content="Nested content.",
            file_path="/skills/group/nested-skill/SKILL.md",
        )
        result = format_skill_invocation(skill)

        assert 'location="/skills/group/nested-skill/SKILL.md"' in result
        assert "References are relative to /skills/group" in result
