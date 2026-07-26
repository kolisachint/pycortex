"""Resource loading and management for skills, agents, and other resources.

Port of ``{skills,resource-loader}.ts`` from ``packages/coding-agent/src/core/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# Skill Types
# ============================================================================


@dataclass
class Skill:
    """A skill definition loaded from a SKILL.md file."""

    name: str
    description: str
    file_path: str
    base_dir: str
    source: str = "local"
    scope: str = "temporary"
    allowed_tools: list[str] | None = None
    disable_model_invocation: bool = False


@dataclass
class SkillResult:
    """Result of loading skills."""

    skills: list[Skill] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)


# ============================================================================
# Agent Types
# ============================================================================


@dataclass
class Agent:
    """An agent definition loaded from an agent file."""

    name: str
    description: str
    prompt: str
    source: str = "builtin"
    file_path: str | None = None
    tools: list[str] | None = None
    model: str | None = None


@dataclass
class AgentResult:
    """Result of loading agents."""

    agents: list[Agent] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)


# ============================================================================
# Resource Loader
# ============================================================================


@dataclass
class ResourceLoader:
    """Loads and manages resources (skills, agents, etc.)."""

    cwd: str = ""
    agent_dir: str = ""
    skills: list[Skill] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)

    def load_skills(self, paths: list[str] | None = None) -> SkillResult:
        """Load skills from paths or default locations."""
        result = SkillResult()

        # Use provided paths or default locations
        if paths:
            for path in paths:
                if os.path.isdir(path):
                    result.skills.extend(self._load_skills_from_dir(path))
                elif os.path.isfile(path) and path.endswith(".md"):
                    skill = self._load_skill_from_file(path)
                    if skill:
                        result.skills.append(skill)
        else:
            # Load from default locations
            default_dirs = [
                os.path.join(self.agent_dir, "skills") if self.agent_dir else None,
                os.path.join(self.cwd, ".hoocode", "skills"),
            ]
            for dir_path in default_dirs:
                if dir_path and os.path.isdir(dir_path):
                    result.skills.extend(self._load_skills_from_dir(dir_path))

        self.skills = result.skills
        return result

    def load_agents(self, paths: list[str] | None = None) -> AgentResult:
        """Load agents from paths or default locations."""
        result = AgentResult()

        # Use provided paths or default locations
        if paths:
            for path in paths:
                if os.path.isdir(path):
                    result.agents.extend(self._load_agents_from_dir(path))
                elif os.path.isfile(path) and path.endswith(".md"):
                    agent = self._load_agent_from_file(path)
                    if agent:
                        result.agents.append(agent)
        else:
            # Load from default locations
            default_dirs = [
                os.path.join(self.agent_dir, "agents") if self.agent_dir else None,
                os.path.join(self.cwd, ".hoocode", "agents"),
            ]
            for dir_path in default_dirs:
                if dir_path and os.path.isdir(dir_path):
                    result.agents.extend(self._load_agents_from_dir(dir_path))

        self.agents = result.agents
        return result

    def _load_skills_from_dir(self, dir_path: str) -> list[Skill]:
        """Load all skills from a directory."""
        skills = []
        try:
            for entry in os.scandir(dir_path):
                if entry.is_dir():
                    # Check for SKILL.md in subdirectory
                    skill_file = os.path.join(entry.path, "SKILL.md")
                    if os.path.isfile(skill_file):
                        skill = self._load_skill_from_file(skill_file)
                        if skill:
                            skills.append(skill)
                elif entry.is_file() and entry.name.endswith(".md"):
                    skill = self._load_skill_from_file(entry.path)
                    if skill:
                        skills.append(skill)
        except OSError:
            pass
        return skills

    def _load_skill_from_file(self, file_path: str) -> Skill | None:
        """Load a skill from a SKILL.md file."""
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Parse frontmatter
            frontmatter, body = self._parse_frontmatter(content)

            # Use name from frontmatter or parent directory name
            name = frontmatter.get("name", "")
            if not name:
                parent_dir = os.path.dirname(file_path)
                name = os.path.basename(parent_dir)

            description = frontmatter.get("description", "")
            if not description:
                # Use first line of body as description
                first_line = body.split("\n")[0].strip()[:60]
                description = first_line + ("..." if len(body.split("\n")[0].strip()) > 60 else "")

            if not description:
                return None

            return Skill(
                name=name,
                description=description,
                file_path=file_path,
                base_dir=os.path.dirname(file_path),
                source="local",
                scope="project",
                allowed_tools=frontmatter.get("allowed-tools"),
                disable_model_invocation=frontmatter.get("disable-model-invocation", False),
            )
        except Exception:
            return None

    def _load_agents_from_dir(self, dir_path: str) -> list[Agent]:
        """Load all agents from a directory."""
        agents = []
        try:
            for entry in os.scandir(dir_path):
                if entry.is_file() and entry.name.endswith(".md"):
                    agent = self._load_agent_from_file(entry.path)
                    if agent:
                        agents.append(agent)
                elif entry.is_dir():
                    # Recurse into subdirectories
                    agents.extend(self._load_agents_from_dir(entry.path))
        except OSError:
            pass
        return agents

    def _load_agent_from_file(self, file_path: str) -> Agent | None:
        """Load an agent from a .md file."""
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Parse frontmatter
            frontmatter, body = self._parse_frontmatter(content)

            # Use name from frontmatter or filename
            name = frontmatter.get("name", "")
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]

            description = frontmatter.get("description", "")
            if not description:
                return None

            # Parse tools as list if it's a comma-separated string
            tools_raw = frontmatter.get("tools")
            if isinstance(tools_raw, str):
                tools = [t.strip() for t in tools_raw.split(",")]
            else:
                tools = tools_raw

            return Agent(
                name=name,
                description=description,
                prompt=body.strip(),
                source="local",
                file_path=file_path,
                tools=tools,
                model=frontmatter.get("model"),
            )
        except Exception:
            return None

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter from content."""
        import yaml

        normalized = content.replace("\r\n", "\n").replace("\r", "\n")

        if not normalized.startswith("---"):
            return {}, normalized

        end_index = normalized.find("\n---", 3)
        if end_index == -1:
            return {}, normalized

        yaml_string = normalized[4:end_index]
        body = normalized[end_index + 4 :].strip()

        try:
            frontmatter = yaml.safe_load(yaml_string) or {}
        except Exception:
            frontmatter = {}

        return frontmatter, body

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return next((s for s in self.skills if s.name == name), None)

    def get_agent(self, name: str) -> Agent | None:
        """Get an agent by name."""
        return next((a for a in self.agents if a.name == name), None)

    def has_skill(self, name: str) -> bool:
        """Check if a skill exists."""
        return self.get_skill(name) is not None

    def has_agent(self, name: str) -> bool:
        """Check if an agent exists."""
        return self.get_agent(name) is not None
