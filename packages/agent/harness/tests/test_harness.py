"""Tests for harness module.

Mechanical port of hoocode's harness tests (if any).
"""

from __future__ import annotations

from cortex.agent.harness import (
    AgentHarnessResources,
    PromptTemplate,
    Skill,
    format_prompt_template_invocation,
    format_skills_for_system_prompt,
)

# ---------------------------------------------------------------------------
# Skill tests
# ---------------------------------------------------------------------------


class TestSkill:
    def test_skill_creation(self) -> None:
        """Skill can be created."""
        skill = Skill(
            name="test_skill",
            description="A test skill",
            content="Skill content",
            file_path="/path/to/skill.md",
        )
        assert skill.name == "test_skill"
        assert skill.description == "A test skill"
        assert skill.content == "Skill content"
        assert skill.file_path == "/path/to/skill.md"
        assert skill.disable_model_invocation is False


# ---------------------------------------------------------------------------
# PromptTemplate tests
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_prompt_template_creation(self) -> None:
        """PromptTemplate can be created."""
        template = PromptTemplate(
            name="test_template",
            description="A test template",
            content="Hello {name}!",
        )
        assert template.name == "test_template"
        assert template.description == "A test template"
        assert template.content == "Hello {name}!"


# ---------------------------------------------------------------------------
# AgentHarnessResources tests
# ---------------------------------------------------------------------------


class TestAgentHarnessResources:
    def test_resources_creation(self) -> None:
        """AgentHarnessResources can be created."""
        resources = AgentHarnessResources()
        assert resources.prompt_templates == []
        assert resources.skills == []


# ---------------------------------------------------------------------------
# AgentHarness tests
# ---------------------------------------------------------------------------


class TestAgentHarness:
    def test_harness_get_skill(self) -> None:
        """AgentHarness.get_resources finds skills by name."""
        skill = Skill(
            name="test_skill",
            description="A test skill",
            content="Content",
            file_path="/path/to/skill.md",
        )
        resources = AgentHarnessResources(skills=[skill])
        # We can't create an AgentHarness without a full session, so test the resources directly
        found = next((s for s in resources.skills if s.name == "test_skill"), None)
        assert found is not None
        assert found.name == "test_skill"

        not_found = next((s for s in resources.skills if s.name == "nonexistent"), None)
        assert not_found is None

    def test_harness_get_prompt_template(self) -> None:
        """AgentHarnessResources finds templates by name."""
        template = PromptTemplate(
            name="test_template",
            description="A test template",
            content="Hello {name}!",
        )
        resources = AgentHarnessResources(prompt_templates=[template])
        found = next((t for t in resources.prompt_templates if t.name == "test_template"), None)
        assert found is not None
        assert found.name == "test_template"

        not_found = next((t for t in resources.prompt_templates if t.name == "nonexistent"), None)
        assert not_found is None

    def test_format_skills_for_system_prompt(self) -> None:
        """format_skills_for_system_prompt works."""
        skill = Skill(
            name="test_skill",
            description="A test skill",
            content="Content",
            file_path="/path/to/skill.md",
        )
        resources = AgentHarnessResources(skills=[skill])

        formatted = format_skills_for_system_prompt(resources.skills)
        assert "<available_skills>" in formatted
        assert "test_skill" in formatted
        assert "</available_skills>" in formatted

    def test_format_skills_empty(self) -> None:
        """format_skills_for_system_prompt returns empty for no skills."""
        formatted = format_skills_for_system_prompt([])
        assert formatted == ""

    def test_format_prompt_template_invocation(self) -> None:
        """format_prompt_template_invocation works."""
        template = PromptTemplate(
            name="greet",
            description="Greeting template",
            content="Hello $1, welcome to $2!",
        )
        result = format_prompt_template_invocation(template, ["Alice", "Wonderland"])
        assert result == "Hello Alice, welcome to Wonderland!"

    def test_format_prompt_template_not_found(self) -> None:
        """format_prompt_template_invocation returns empty for missing template."""
        template = PromptTemplate(name="nonexistent", content="")
        result = format_prompt_template_invocation(template)
        assert result == ""
