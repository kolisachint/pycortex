"""Tests for harness types.

Mechanical port of hoocode's ``packages/agent/test/harness/types.test.ts``.
"""

from __future__ import annotations

from cortex.agent.harness.types import (
    AgentHarnessResources,
    ExecutionEnv,
    FileError,
    FileInfo,
    PromptTemplate,
    Skill,
)


class TestSkill:
    """Tests for Skill dataclass."""

    def test_creation(self) -> None:
        """Skill can be created with required fields."""
        skill = Skill(
            name="test",
            description="Test skill",
            content="Content",
            file_path="/path/to/SKILL.md",
        )
        assert skill.name == "test"
        assert skill.description == "Test skill"
        assert skill.content == "Content"
        assert skill.file_path == "/path/to/SKILL.md"
        assert skill.disable_model_invocation is False

    def test_with_disabled_invocation(self) -> None:
        """Skill can be created with disabled model invocation."""
        skill = Skill(
            name="hidden",
            description="Hidden skill",
            content="Content",
            file_path="/path/to/SKILL.md",
            disable_model_invocation=True,
        )
        assert skill.disable_model_invocation is True


class TestPromptTemplate:
    """Tests for PromptTemplate dataclass."""

    def test_creation(self) -> None:
        """PromptTemplate can be created with required fields."""
        template = PromptTemplate(
            name="test",
            content="Content",
        )
        assert template.name == "test"
        assert template.content == "Content"
        assert template.description == ""

    def test_with_description(self) -> None:
        """PromptTemplate can be created with description."""
        template = PromptTemplate(
            name="test",
            content="Content",
            description="A test template",
        )
        assert template.description == "A test template"


class TestAgentHarnessResources:
    """Tests for AgentHarnessResources dataclass."""

    def test_creation(self) -> None:
        """AgentHarnessResources can be created with defaults."""
        resources = AgentHarnessResources()
        assert resources.skills == []
        assert resources.prompt_templates == []

    def test_with_skills(self) -> None:
        """AgentHarnessResources can be created with skills."""
        skill = Skill(
            name="test",
            description="Test",
            content="Content",
            file_path="/path",
        )
        resources = AgentHarnessResources(skills=[skill])
        assert len(resources.skills) == 1
        assert resources.skills[0].name == "test"


class TestFileError:
    """Tests for FileError exception."""

    def test_creation(self) -> None:
        """FileError can be created."""
        error = FileError(code="not_found", message="File not found")
        assert error.code == "not_found"
        assert str(error) == "File not found"
        assert error.path is None

    def test_with_path(self) -> None:
        """FileError can be created with path."""
        error = FileError(
            code="permission_denied",
            message="Permission denied",
            path="/secret/file",
        )
        assert error.path == "/secret/file"


class TestFileInfo:
    """Tests for FileInfo dataclass."""

    def test_creation(self) -> None:
        """FileInfo can be created."""
        info = FileInfo(
            name="test.txt",
            path="/path/to/test.txt",
            kind="file",
            size=1024,
            mtime_ms=1234567890.0,
        )
        assert info.name == "test.txt"
        assert info.kind == "file"
        assert info.size == 1024


class TestExecutionEnv:
    """Tests for ExecutionEnv base class."""

    def test_creation(self) -> None:
        """ExecutionEnv can be created."""
        env = ExecutionEnv(cwd="/tmp")
        assert env.cwd == "/tmp"
