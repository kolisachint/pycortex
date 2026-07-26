"""Cortex Code Prompts - System prompt construction and template management.

This package provides:
- System prompt building with tools, guidelines, and context
- Built-in mode prompts (ask/plan/build/debug)
- Prompt template loading and expansion
- Skills and agents formatting for prompt injection
"""

from __future__ import annotations

# Agents formatting
from .agents import format_agents_for_prompt

# Mode prompts
from .mode_prompts import DEFAULT_MODE, DEFAULT_MODE_PROMPTS

# Prompt templates
from .prompt_templates import (
    LoadPromptTemplatesOptions,
    expand_prompt_template,
    load_prompt_templates,
    parse_command_args,
    substitute_args,
    try_expand_prompt_template,
)

# Skills formatting
from .skills import format_skills_for_prompt

# Source info
from .source_info import SourceInfo, create_synthetic_source_info

# System prompt
from .system_prompt import BuildSystemPromptOptions, build_system_prompt

# Types
from .types import (
    TASK_TOOL_NAME,
    TODO_WRITE_TOOL_NAME,
    AgentDefinition,
    PromptTemplate,
    PromptTemplateExpansion,
    Skill,
)

__all__ = [
    # Agents
    "format_agents_for_prompt",
    # Mode prompts
    "DEFAULT_MODE",
    "DEFAULT_MODE_PROMPTS",
    # Prompt templates
    "LoadPromptTemplatesOptions",
    "expand_prompt_template",
    "load_prompt_templates",
    "parse_command_args",
    "substitute_args",
    "try_expand_prompt_template",
    # Source info
    "SourceInfo",
    "create_synthetic_source_info",
    # System prompt
    "BuildSystemPromptOptions",
    "build_system_prompt",
    # Skills
    "format_skills_for_prompt",
    # Types
    "TASK_TOOL_NAME",
    "TODO_WRITE_TOOL_NAME",
    "AgentDefinition",
    "PromptTemplate",
    "PromptTemplateExpansion",
    "Skill",
]
