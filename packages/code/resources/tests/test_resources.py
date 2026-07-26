# pyright: reportMissingParameterType=false, reportUnknownParameterType=false
"""Tests for resource loading.

Tests verify:
- Skill loading from files
- Agent loading from files
- Resource loader initialization
"""

from __future__ import annotations

from cortex.code.resources import Agent, AgentResult, ResourceLoader, Skill, SkillResult


class TestSkill:
    def test_default_values(self):
        skill = Skill(
            name="test-skill",
            description="A test skill",
            file_path="/path/to/SKILL.md",
            base_dir="/path/to",
        )
        assert skill.name == "test-skill"
        assert skill.description == "A test skill"
        assert skill.file_path == "/path/to/SKILL.md"
        assert skill.base_dir == "/path/to"
        assert skill.source == "local"
        assert skill.scope == "temporary"
        assert skill.allowed_tools is None
        assert skill.disable_model_invocation is False

    def test_with_allowed_tools(self):
        skill = Skill(
            name="test-skill",
            description="A test skill",
            file_path="/path/to/SKILL.md",
            base_dir="/path/to",
            allowed_tools=["read", "bash"],
        )
        assert skill.allowed_tools == ["read", "bash"]

    def test_with_disable_model_invocation(self):
        skill = Skill(
            name="test-skill",
            description="A test skill",
            file_path="/path/to/SKILL.md",
            base_dir="/path/to",
            disable_model_invocation=True,
        )
        assert skill.disable_model_invocation is True


class TestSkillResult:
    def test_default_values(self):
        result = SkillResult()
        assert result.skills == []
        assert result.diagnostics == []

    def test_with_skills(self):
        skill = Skill(
            name="test",
            description="test",
            file_path="/path",
            base_dir="/path",
        )
        result = SkillResult(skills=[skill])
        assert len(result.skills) == 1


class TestAgent:
    def test_default_values(self):
        agent = Agent(
            name="explore",
            description="An exploration agent",
            prompt="Explore the codebase",
        )
        assert agent.name == "explore"
        assert agent.description == "An exploration agent"
        assert agent.prompt == "Explore the codebase"
        assert agent.source == "builtin"
        assert agent.file_path is None
        assert agent.tools is None
        assert agent.model is None

    def test_with_tools(self):
        agent = Agent(
            name="test",
            description="test",
            prompt="test",
            tools=["read", "grep"],
        )
        assert agent.tools == ["read", "grep"]


class TestAgentResult:
    def test_default_values(self):
        result = AgentResult()
        assert result.agents == []
        assert result.diagnostics == []

    def test_with_agents(self):
        agent = Agent(name="test", description="test", prompt="test")
        result = AgentResult(agents=[agent])
        assert len(result.agents) == 1


class TestResourceLoader:
    def test_initialization(self):
        loader = ResourceLoader(cwd="/test", agent_dir="/home/.hoocode")
        assert loader.cwd == "/test"
        assert loader.agent_dir == "/home/.hoocode"
        assert loader.skills == []
        assert loader.agents == []

    def test_load_skills_from_empty_dir(self, tmp_path):
        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_skills([str(tmp_path)])
        assert result.skills == []

    def test_load_skill_from_file(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\ndescription: A test skill\n---\nThis is a test skill.")

        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_skills([str(skill_dir)])
        assert len(result.skills) == 1
        assert result.skills[0].name == "test-skill"
        assert result.skills[0].description == "A test skill"

    def test_load_skill_with_frontmatter_name(self, tmp_path):
        skill_file = tmp_path / "my-skill.md"
        skill_file.write_text(
            "---\nname: custom-name\ndescription: A custom skill\n---\nCustom skill content."
        )

        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_skills([str(skill_file)])
        assert len(result.skills) == 1
        assert result.skills[0].name == "custom-name"

    def test_load_agent_from_file(self, tmp_path):
        agent_file = tmp_path / "explore.md"
        agent_file.write_text(
            "---\ndescription: An exploration agent\n---\nExplore the codebase thoroughly."
        )

        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_agents([str(tmp_path)])
        assert len(result.agents) == 1
        assert result.agents[0].name == "explore"
        assert result.agents[0].description == "An exploration agent"

    def test_load_agent_with_frontmatter(self, tmp_path):
        agent_file = tmp_path / "agent.md"
        agent_file.write_text(
            "---\n"
            "name: custom-agent\n"
            "description: A custom agent\n"
            "tools: read, bash\n"
            "---\n"
            "Custom agent prompt."
        )

        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_agents([str(tmp_path)])
        assert len(result.agents) == 1
        assert result.agents[0].name == "custom-agent"
        assert result.agents[0].tools == ["read", "bash"]

    def test_get_skill(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\ndescription: A test skill\n---\nTest content.")

        loader = ResourceLoader(cwd=str(tmp_path))
        loader.load_skills([str(skill_dir)])

        skill = loader.get_skill("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"

    def test_get_nonexistent_skill(self):
        loader = ResourceLoader()
        skill = loader.get_skill("nonexistent")
        assert skill is None

    def test_has_skill(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\ndescription: A test skill\n---\nTest content.")

        loader = ResourceLoader(cwd=str(tmp_path))
        loader.load_skills([str(skill_dir)])

        assert loader.has_skill("test-skill") is True
        assert loader.has_skill("nonexistent") is False

    def test_get_agent(self, tmp_path):
        agent_file = tmp_path / "explore.md"
        agent_file.write_text("---\ndescription: An exploration agent\n---\nExplore.")

        loader = ResourceLoader(cwd=str(tmp_path))
        loader.load_agents([str(tmp_path)])

        agent = loader.get_agent("explore")
        assert agent is not None
        assert agent.name == "explore"

    def test_has_agent(self, tmp_path):
        agent_file = tmp_path / "explore.md"
        agent_file.write_text("---\ndescription: An exploration agent\n---\nExplore.")

        loader = ResourceLoader(cwd=str(tmp_path))
        loader.load_agents([str(tmp_path)])

        assert loader.has_agent("explore") is True
        assert loader.has_agent("nonexistent") is False

    def test_load_skills_from_default_locations(self, tmp_path):
        # Create skills in default location
        skills_dir = tmp_path / ".hoocode" / "skills"
        skills_dir.mkdir(parents=True)
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\ndescription: A test skill\n---\nTest content.")

        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_skills()
        assert len(result.skills) == 1

    def test_load_agents_from_default_locations(self, tmp_path):
        # Create agents in default location
        agents_dir = tmp_path / ".hoocode" / "agents"
        agents_dir.mkdir(parents=True)
        agent_file = agents_dir / "explore.md"
        agent_file.write_text("---\ndescription: An exploration agent\n---\nExplore.")

        loader = ResourceLoader(cwd=str(tmp_path))
        result = loader.load_agents()
        assert len(result.agents) == 1
