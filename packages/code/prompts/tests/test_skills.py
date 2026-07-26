"""Tests for skills formatting.

Tests verify:
- Skills are formatted as XML
- Skills with disable_model_invocation are excluded
- Empty skills list returns empty string
"""

from __future__ import annotations

from cortex.code.prompts.skills import format_skills_for_prompt
from cortex.code.prompts.types import Skill


class TestFormatSkillsForPrompt:
    def test_empty_skills_returns_empty_string(self):
        result = format_skills_for_prompt([])
        assert result == ""

    def test_formats_skills_as_xml(self):
        skills = [
            Skill(
                name="test-skill",
                description="A test skill",
                file_path="/path/to/SKILL.md",
                base_dir="/path/to",
            )
        ]
        result = format_skills_for_prompt(skills)

        assert "<available_skills>" in result
        assert "</available_skills>" in result
        assert "<skill>" in result
        assert "</skill>" in result
        assert "<name>test-skill</name>" in result
        assert "<description>A test skill</description>" in result
        assert "<location>/path/to/SKILL.md</location>" in result

    def test_excludes_skills_with_disable_model_invocation(self):
        skills = [
            Skill(
                name="visible-skill",
                description="This skill is visible",
                file_path="/path/to/SKILL.md",
                base_dir="/path/to",
                disable_model_invocation=False,
            ),
            Skill(
                name="hidden-skill",
                description="This skill is hidden",
                file_path="/path/to/hidden/SKILL.md",
                base_dir="/path/to/hidden",
                disable_model_invocation=True,
            ),
        ]
        result = format_skills_for_prompt(skills)

        assert "visible-skill" in result
        assert "hidden-skill" not in result

    def test_includes_allowed_tools_when_present(self):
        skills = [
            Skill(
                name="test-skill",
                description="A test skill",
                file_path="/path/to/SKILL.md",
                base_dir="/path/to",
                allowed_tools=["read", "bash"],
            )
        ]
        result = format_skills_for_prompt(skills)

        assert "<tools>read, bash</tools>" in result

    def test_escapes_xml_in_description(self):
        skills = [
            Skill(
                name="test-skill",
                description='A skill with <special> & "chars"',
                file_path="/path/to/SKILL.md",
                base_dir="/path/to",
            )
        ]
        result = format_skills_for_prompt(skills)

        assert "&lt;special&gt;" in result
        assert "&amp;" in result
        assert "&quot;" in result

    def test_multiple_skills(self):
        skills = [
            Skill(
                name="skill-1",
                description="First skill",
                file_path="/path/1/SKILL.md",
                base_dir="/path/1",
            ),
            Skill(
                name="skill-2",
                description="Second skill",
                file_path="/path/2/SKILL.md",
                base_dir="/path/2",
            ),
        ]
        result = format_skills_for_prompt(skills)

        assert "skill-1" in result
        assert "skill-2" in result
        assert result.count("<skill>") == 2
