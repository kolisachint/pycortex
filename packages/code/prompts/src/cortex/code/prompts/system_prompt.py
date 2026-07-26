"""System prompt construction and project context loading.

Port of ``system-prompt.ts`` from ``packages/coding-agent/src/core/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .agents import format_agents_for_prompt
from .skills import format_skills_for_prompt
from .types import TASK_TOOL_NAME, AgentDefinition, Skill


@dataclass
class BuildSystemPromptOptions:
    """Options for building the system prompt."""

    # Custom system prompt (replaces default).
    custom_prompt: str | None = None
    # Tools to include in prompt. Default: [read, bash, edit, write, search, grep, find, ls]
    selected_tools: list[str] | None = None
    # Optional one-line tool snippets keyed by tool name.
    tool_snippets: dict[str, str] | None = None
    # Additional guideline bullets appended to the default system prompt guidelines.
    prompt_guidelines: list[str] | None = None
    # Text to append to system prompt.
    append_system_prompt: str | None = None
    # Working directory.
    cwd: str = ""
    # Pre-loaded context files.
    context_files: list[dict[str, str]] | None = None
    # Pre-loaded skills.
    skills: list[Skill] | None = None
    # Available agents for delegation, emitted as ``<available_agents>`` XML.
    agents: list[AgentDefinition] | None = None


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Build the system prompt with tools, guidelines, and context."""
    resolved_cwd = options.cwd
    prompt_cwd = resolved_cwd.replace("\\", "/")

    now = datetime.now()
    date = f"{now.year:04d}-{now.month + 1:02d}-{now.day:02d}"

    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""

    context_files = options.context_files or []
    skills = options.skills or []
    agents = options.agents or []

    if options.custom_prompt:
        prompt = options.custom_prompt

        if append_section:
            prompt += append_section

        # Append project context files
        if context_files:
            prompt += "\n\n# Project Context\n\n"
            prompt += "Project-specific instructions and guidelines:\n\n"
            for file_info in context_files:
                file_path = file_info["path"]
                content = file_info["content"]
                prompt += f"## {file_path}\n\n{content}\n\n"

        # Append skills section (only if read tool is available)
        has_read = not options.selected_tools or "read" in options.selected_tools
        if has_read and skills:
            prompt += format_skills_for_prompt(skills)

        # Append agents section (only when Task tool is active)
        has_task = not options.selected_tools or TASK_TOOL_NAME in options.selected_tools
        if has_task and agents:
            prompt += format_agents_for_prompt(agents)

        # Add date and working directory last
        prompt += f"\nCurrent date: {date}"
        prompt += f"\nCurrent working directory: {prompt_cwd}"

        return prompt

    # Build tools list based on selected tools.
    # A tool appears in Available tools only when the caller provides a one-line snippet.
    tools = options.selected_tools or [
        "read",
        "bash",
        "edit",
        "write",
        "search",
        "grep",
        "find",
        "ls",
    ]
    tool_snippets = options.tool_snippets or {}

    visible_tools = [name for name in tools if name in tool_snippets]
    if visible_tools:
        tools_list = "\n".join(f"- {name}: {tool_snippets[name]}" for name in visible_tools)
    else:
        tools_list = "(none)"

    # Build guidelines based on which tools are actually available
    guidelines_list: list[str] = []
    guidelines_set: set[str] = set()

    def add_guideline(guideline: str) -> None:
        if guideline in guidelines_set:
            return
        guidelines_set.add(guideline)
        guidelines_list.append(guideline)

    has_bash = "bash" in tools
    has_search = "search" in tools
    has_grep = "grep" in tools
    has_find = "find" in tools
    has_ls = "ls" in tools
    has_read = "read" in tools

    # File exploration guidelines
    explore: list[str] = []
    if has_search:
        explore.append("search (find where code lives by concept or identifier)")
    if has_grep:
        explore.append("grep (exact line/regex search)")
    if has_find:
        explore.append("find (locate files by name/glob)")
    if has_ls:
        explore.append("ls (list directory contents)")
    if explore:
        add_guideline(
            f"For file exploration use the dedicated tools — {', '.join(explore)} — "
            "instead of bash; they are faster, and respect .gitignore where applicable"
        )
    elif has_bash:
        add_guideline("Use bash for file exploration (ls, rg/grep, find)")

    # Single source of truth for the search↔grep decision
    if has_search and has_grep:
        add_guideline(
            "Between search and grep: search finds where code lives by concept, behavior, or "
            "half-known name (ranked results); grep enumerates exact matching lines, regexes, "
            "and counts (output proportional to matches)"
        )

    for guideline in options.prompt_guidelines or []:
        normalized = guideline.strip()
        if normalized:
            add_guideline(normalized)

    # Always include these
    add_guideline("Be concise in your responses")
    add_guideline(
        "No preamble or postamble; do not restate the task or summarize what you just did"
    )
    add_guideline('Do not add closers like "Let me know" or "Hope this helps"')
    add_guideline(
        "Do not narrate routine tool calls or results — the permission gate already shows them; "
        "speak when you have the answer or need a decision"
    )
    add_guideline(
        "Match the surrounding code's conventions for comments, docstrings, and types — "
        "do not add or strip them by default"
    )
    add_guideline("Show file paths clearly when working with files")

    guidelines = "\n".join(f"- {g}" for g in guidelines_list)

    prompt = (
        "You are an expert coding assistant operating inside hoocode, "
        "a coding agent harness. You help users by reading files, "
        "executing commands, editing code, and writing new files.\n\n"
        f"Available tools:\n{tools_list}\n\n"
        "In addition to the tools above, you may have access to "
        "other custom tools depending on the project.\n\n"
        f"Guidelines:\n{guidelines}"
    )

    if append_section:
        prompt += append_section

    # Append project context files
    if context_files:
        prompt += "\n\n# Project Context\n\n"
        prompt += "Project-specific instructions and guidelines:\n\n"
        for file_info in context_files:
            file_path = file_info["path"]
            content = file_info["content"]
            prompt += f"## {file_path}\n\n{content}\n\n"

    # Append skills section (only if read tool is available)
    if has_read and skills:
        prompt += format_skills_for_prompt(skills)

    # Append agents section (only when Task tool is active)
    has_task = TASK_TOOL_NAME in tools
    if has_task and agents:
        prompt += format_agents_for_prompt(agents)

    # Add date and working directory last
    prompt += f"\nCurrent date: {date}"
    prompt += f"\nCurrent working directory: {prompt_cwd}"

    return prompt
