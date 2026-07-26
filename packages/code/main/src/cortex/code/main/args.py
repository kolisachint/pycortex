"""CLI argument parsing and help display.

Port of ``cli/args.ts`` from ``packages/coding-agent/src/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

APP_NAME = "hoocode"
CONFIG_DIR_NAME = ".hoocode"

# Valid thinking levels
VALID_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"]

# Output mode type
Mode = Literal["text", "json", "rpc"]


@dataclass
class Args:
    """Parsed CLI arguments."""

    # Provider and model
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    system_prompt: str | None = None
    thinking: str | None = None

    # Session control
    continue_session: bool = False
    resume: bool = False
    session: str | None = None
    fork: str | None = None
    session_dir: str | None = None
    no_session: bool = False
    task_id: str | None = None
    max_turns: int | None = None

    # Output mode
    mode: Mode = "text"
    print: bool = False
    export: str | None = None

    # Tools
    no_tools: bool = False
    no_builtin_tools: bool = False
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)

    # Subagent settings
    subagent: bool | None = None
    warm_subagents: bool = False
    max_subagent_depth: int | None = None
    delegate_allow: list[str] = field(default_factory=list)

    # Feature flags
    todo_write: bool = False
    enable_web_tools: bool = False
    enable_browser_tools: bool = False
    enable_browser_live_preview: bool = False
    enable_file_tools: bool = False
    enable_plugin_tools: bool = False
    enable_embsearch_tools: bool = False

    # Mode and display
    light: bool = False
    print_token_surface: bool = False
    support_platform: list[str] = field(default_factory=list)

    # TLS
    ca_cert: str | None = None
    use_system_ca: bool = False

    # Extensions and skills
    extensions: list[str] = field(default_factory=list)
    no_extensions: bool = False
    skills: list[str] = field(default_factory=list)
    no_skills: bool = False
    agents: list[str] = field(default_factory=list)
    prompt_templates: list[str] = field(default_factory=list)
    no_prompt_templates: bool = False
    slash_commands: list[str] = field(default_factory=list)
    no_slash_commands: bool = False
    themes: list[str] = field(default_factory=list)
    no_themes: bool = False
    mode_paths: list[str] = field(default_factory=list)
    no_context_files: bool = False

    # Other options
    models: list[str] = field(default_factory=list)
    team: str | None = None
    list_models: str | bool | None = None
    offline: bool = False
    verbose: bool = False

    # Input
    messages: list[str] = field(default_factory=list)
    file_args: list[str] = field(default_factory=list)
    unknown_flags: dict[str, bool | str] = field(default_factory=dict)
    diagnostics: list[dict[str, str]] = field(default_factory=list)

    # Help and version
    help: bool = False
    version: bool = False


def is_valid_thinking_level(level: str) -> bool:
    """Check if a string is a valid thinking level."""
    return level in VALID_THINKING_LEVELS


def parse_args(args: list[str]) -> Args:
    """Parse CLI arguments into Args dataclass."""
    result = Args()

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ("--help", "-h"):
            result.help = True
        elif arg in ("--version", "-v"):
            result.version = True
        elif arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            if mode in ("text", "json", "rpc"):
                result.mode = mode  # type: ignore[assignment]
            i += 1
        elif arg in ("--continue", "-c"):
            result.continue_session = True
        elif arg in ("--resume", "-r"):
            result.resume = True
        elif arg == "--provider" and i + 1 < len(args):
            result.provider = args[i + 1]
            i += 1
        elif arg == "--model" and i + 1 < len(args):
            result.model = args[i + 1]
            i += 1
        elif arg == "--api-key" and i + 1 < len(args):
            result.api_key = args[i + 1]
            i += 1
        elif arg == "--system-prompt" and i + 1 < len(args):
            result.system_prompt = args[i + 1]
            i += 1
        elif arg == "--no-session":
            result.no_session = True
        elif arg == "--task-id" and i + 1 < len(args):
            result.task_id = args[i + 1]
            i += 1
        elif arg == "--max-turns" and i + 1 < len(args):
            try:
                n = int(args[i + 1])
                if n > 0:
                    result.max_turns = n
            except ValueError:
                pass
            i += 1
        elif arg == "--session" and i + 1 < len(args):
            result.session = args[i + 1]
            i += 1
        elif arg == "--team" and i + 1 < len(args):
            result.team = args[i + 1]
            i += 1
        elif arg == "--fork" and i + 1 < len(args):
            result.fork = args[i + 1]
            i += 1
        elif arg == "--session-dir" and i + 1 < len(args):
            result.session_dir = args[i + 1]
            i += 1
        elif arg == "--models" and i + 1 < len(args):
            result.models = [s.strip() for s in args[i + 1].split(",")]
            i += 1
        elif arg in ("--no-tools", "-nt"):
            result.no_tools = True
        elif arg in ("--no-builtin-tools", "-nbt"):
            result.no_builtin_tools = True
        elif arg == "--enable-subagents":
            result.subagent = True
        elif arg in ("--no-subagents", "--disable-subagents"):
            result.subagent = False
        elif arg == "--warm-subagents":
            result.warm_subagents = True
        elif arg == "--max-subagent-depth" and i + 1 < len(args):
            try:
                n = int(args[i + 1])
                if n >= 1:
                    result.max_subagent_depth = n
            except ValueError:
                pass
            i += 1
        elif arg == "--delegate-allow" and i + 1 < len(args):
            result.delegate_allow = [s.strip() for s in args[i + 1].split(",") if s.strip()]
            i += 1
        elif arg == "--enable-todowrite":
            result.todo_write = True
        elif arg == "--enable-webtools":
            result.enable_web_tools = True
        elif arg == "--enable-browsertools":
            result.enable_browser_tools = True
        elif arg in ("--enable-search-tool", "--enable-embsearchtools"):
            result.enable_embsearch_tools = True
        elif arg == "--enable-browser-live-preview":
            result.enable_browser_live_preview = True
        elif arg == "--enable-filetools":
            result.enable_file_tools = True
        elif arg == "--enable-plugintools":
            result.enable_plugin_tools = True
        elif arg == "--light":
            result.light = True
        elif arg == "--print-token-surface":
            result.print_token_surface = True
        elif arg == "--support-platform" and i + 1 < len(args):
            result.support_platform.extend(s.strip() for s in args[i + 1].split(",") if s.strip())
            i += 1
        elif arg == "--ca-cert" and i + 1 < len(args):
            result.ca_cert = args[i + 1]
            i += 1
        elif arg == "--use-system-ca":
            result.use_system_ca = True
        elif arg in ("--tools", "-t") and i + 1 < len(args):
            result.tools = [s.strip() for s in args[i + 1].split(",") if s.strip()]
            i += 1
        elif arg == "--disallowed-tools" and i + 1 < len(args):
            result.disallowed_tools = [s.strip() for s in args[i + 1].split(",") if s.strip()]
            i += 1
        elif arg == "--thinking" and i + 1 < len(args):
            level = args[i + 1]
            if is_valid_thinking_level(level):
                result.thinking = level
            else:
                valid_values = ", ".join(VALID_THINKING_LEVELS)
                msg = f'Invalid thinking level "{level}". Valid values: {valid_values}'
                result.diagnostics.append(
                    {
                        "type": "warning",
                        "message": msg,
                    }
                )
            i += 1
        elif arg in ("--print", "-p"):
            result.print = True
            # Check if next arg is a message (not a flag or file arg)
            if i + 1 < len(args):
                next_arg = args[i + 1]
                if not next_arg.startswith("@") and (
                    not next_arg.startswith("-") or next_arg.startswith("---")
                ):
                    result.messages.append(next_arg)
                    i += 1
        elif arg == "--export" and i + 1 < len(args):
            result.export = args[i + 1]
            i += 1
        elif arg in ("--extension", "-e") and i + 1 < len(args):
            result.extensions.append(args[i + 1])
            i += 1
        elif arg in ("--no-extensions", "-ne"):
            result.no_extensions = True
        elif arg == "--skill" and i + 1 < len(args):
            result.skills.append(args[i + 1])
            i += 1
        elif arg == "--agent" and i + 1 < len(args):
            result.agents.append(args[i + 1])
            i += 1
        elif arg == "--prompt-template" and i + 1 < len(args):
            result.prompt_templates.append(args[i + 1])
            i += 1
        elif arg == "--slash-command" and i + 1 < len(args):
            result.slash_commands.append(args[i + 1])
            i += 1
        elif arg == "--theme" and i + 1 < len(args):
            result.themes.append(args[i + 1])
            i += 1
        elif arg == "--mode-path" and i + 1 < len(args):
            result.mode_paths.append(args[i + 1])
            i += 1
        elif arg in ("--no-skills", "-ns"):
            result.no_skills = True
        elif arg in ("--no-prompt-templates", "-np"):
            result.no_prompt_templates = True
        elif arg in ("--no-slash-commands", "-nsc"):
            result.no_slash_commands = True
        elif arg == "--no-themes":
            result.no_themes = True
        elif arg in ("--no-context-files", "-nc"):
            result.no_context_files = True
        elif arg == "--list-models":
            # Check if next arg is a search pattern (not a flag or file arg)
            if (
                i + 1 < len(args)
                and not args[i + 1].startswith("-")
                and not args[i + 1].startswith("@")
            ):
                result.list_models = args[i + 1]
                i += 1
            else:
                result.list_models = True
        elif arg == "--verbose":
            result.verbose = True
        elif arg == "--offline":
            result.offline = True
        elif arg.startswith("@"):
            result.file_args.append(arg[1:])  # Remove @ prefix
        elif arg.startswith("--"):
            # Handle unknown flags
            eq_index = arg.find("=")
            if eq_index != -1:
                result.unknown_flags[arg[2:eq_index]] = arg[eq_index + 1 :]
            else:
                flag_name = arg[2:]
                if (
                    i + 1 < len(args)
                    and not args[i + 1].startswith("-")
                    and not args[i + 1].startswith("@")
                ):
                    result.unknown_flags[flag_name] = args[i + 1]
                    i += 1
                else:
                    result.unknown_flags[flag_name] = True
        elif arg.startswith("-") and not arg.startswith("--"):
            result.diagnostics.append({"type": "error", "message": f"Unknown option: {arg}"})
        elif not arg.startswith("-"):
            result.messages.append(arg)

        i += 1

    return result


def print_help() -> None:
    """Print CLI help text."""
    help_text = f"""{APP_NAME} - AI coding assistant with read, bash, edit, write tools

Usage:
  {APP_NAME} [options] [@files...] [messages...]

Options:
  --provider <name>              Provider name (default: google)
  --model <pattern>              Model pattern or ID
  --api-key <key>                API key (defaults to env vars)
  --system-prompt <text>         System prompt (default: coding assistant prompt)
  --mode <mode>                  Output mode: text (default), json, or rpc
  --print, -p                    Non-interactive mode: process prompt and exit
  --continue, -c                 Continue previous session
  --resume, -r                   Select a session to resume
  --session <path|id>            Use specific session file or partial UUID
  --fork <path|id>               Fork specific session into a new session
  --session-dir <dir>            Directory for session storage and lookup
  --no-session                   Don't save session (ephemeral)
  --models <patterns>            Comma-separated model patterns for Ctrl+P cycling
  --no-tools, -nt                Disable all tools by default
  --no-builtin-tools, -nbt       Disable built-in tools but keep extension tools
  --tools, -t <tools>            Comma-separated allowlist of tool names to enable
  --disallowed-tools <tools>     Comma-separated denylist of tool names to disable
  --max-turns <n>                Hard cap on assistant turns
  --enable-subagents             Enable the subagent tool (on by default)
  --no-subagents                 Disable the subagent tool
  --enable-todowrite             Enable the TodoWrite tool
  --enable-webtools              Enable the webfetch + websearch tools
  --enable-browsertools          Enable the browser_run + browser_continue tools
  --enable-filetools             Enable the document tools
  --enable-search-tool           Enable the semantic index for the search tool
  --enable-plugintools           Enable the autonomous plugin system
  --light                        Minimal low-token preset for small/local models
  --thinking <level>             Set thinking level: off, minimal, low, medium, high, xhigh
  --extension, -e <path>         Load an extension file
  --no-extensions, -ne           Disable extension discovery
  --skill <path>                 Load a skill file or directory
  --no-skills, -ns               Disable skills discovery and loading
  --agent <path>                 Load an agent file or directory
  --prompt-template <path>       Load a prompt template file or directory
  --no-prompt-templates, -np     Disable prompt template discovery
  --no-slash-commands, -nsc      Disable slash command discovery
  --theme <path>                 Load a theme file or directory
  --no-themes                    Disable theme discovery and loading
  --mode-path <dir>              Add a directory to search for mode files
  --no-context-files, -nc        Disable AGENTS.md and CLAUDE.md discovery
  --export <file>                Export session file to HTML and exit
  --list-models [search]         List available models
  --verbose                      Force verbose startup
  --offline                      Disable startup network operations
  --ca-cert <path>               Trust an extra PEM CA bundle for TLS
  --use-system-ca                Trust the OS/system CA store
  --help, -h                     Show this help
  --version, -v                  Show version number

Environment Variables:
  ANTHROPIC_API_KEY              - Anthropic Claude API key
  OPENAI_API_KEY                 - OpenAI GPT API key
  GEMINI_API_KEY                 - Google Gemini API key
  {f"{'HOOCODE_AGENT_DIR':<32} - Config directory (default: ~/{CONFIG_DIR_NAME}/agent)"}"""

    print(help_text)
