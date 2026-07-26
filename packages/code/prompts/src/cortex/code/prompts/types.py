"""Agent-related types for prompts.

Port of agent-related types from ``agent-frontmatter.ts`` and ``skills.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Tool name constants
TASK_TOOL_NAME = "Task"
TODO_WRITE_TOOL_NAME = "TodoWrite"

# Source types
AgentSource = Literal["builtin", "user", "project", "claude-user", "claude-project"]
SourceScope = Literal["user", "project", "temporary"]
SourceOrigin = Literal["package", "top-level", "claude-code"]

# Model inherit sentinel
MODEL_INHERIT = "inherit"


@dataclass
class AgentDefinition:
    """A validated, normalized agent definition."""

    name: str
    description: str
    prompt: str
    source: AgentSource
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    model: str | None = None
    file_path: str | None = None
    max_turns: int | None = None
    background: bool | None = None
    delegate: bool | None = None
    delegate_to: list[str] | None = None
    fork: bool | None = None


@dataclass
class Skill:
    """Skill definition for prompt injection."""

    name: str
    description: str
    file_path: str
    base_dir: str
    disable_model_invocation: bool = False
    allowed_tools: list[str] | None = None


@dataclass
class ResourceDiagnostic:
    """Diagnostic message for resource loading."""

    type: str = "warning"
    message: str = ""
    path: str | None = None


@dataclass
class PromptTemplate:
    """A prompt template loaded from a markdown file."""

    name: str
    description: str
    content: str
    type: str = "user"  # "user" | "system" | "context"
    source_path: str = ""
    base_dir: str = ""
    file_path: str = ""
    argument_hint: str | None = None


@dataclass
class PromptTemplateExpansion:
    """Result of attempting to expand a prompt template."""

    text: str
    template: PromptTemplate | None = None
    args: list[str] = field(default_factory=list)
    args_string: str = ""
