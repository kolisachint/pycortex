"""Agent formatting for system prompt injection.

Port of ``formatAgentsForPrompt`` from ``agent-registry.ts``.
"""

from __future__ import annotations

from .types import AgentDefinition


def _escape_xml(s: str) -> str:
    """Escape XML special characters including quotes."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def format_agents_for_prompt(agents: list[AgentDefinition]) -> str:
    """Format agents for inclusion in a system prompt.

    Agents are emitted as ``<available_agents>`` XML so the model knows which
    agents exist without re-reading the agent registry each turn.
    """
    if not agents:
        return ""

    lines = [
        "",
        "The following specialized agents are available for delegation via the Task tool.",
        "Choose the agent whose description best matches the task and pass it as `subagent_type`.",
        "",
        "<available_agents>",
    ]

    for agent in agents:
        lines.append("  <agent>")
        lines.append(f"    <name>{_escape_xml(agent.name)}</name>")
        lines.append(f"    <description>{_escape_xml(agent.description)}</description>")
        if agent.tools:
            lines.append(f"    <tools>{_escape_xml(', '.join(agent.tools))}</tools>")
        lines.append("  </agent>")

    lines.append("</available_agents>")

    return "\n".join(lines)
