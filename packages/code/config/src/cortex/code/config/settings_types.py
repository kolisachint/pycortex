"""Settings schema shared across the app.

The `Settings` dataclass and its nested option groups describe the on-disk
global/project settings.json shape. Extracted from settings_manager so the
schema can be imported without pulling in the manager implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000
    max_context_ratio: float = 0.75


@dataclass
class ToolOutputSettings:
    max_bytes: int = 32768
    max_lines: int = 800


@dataclass
class ContextGcSettings:
    enabled: bool = True


@dataclass
class VoiceSettings:
    silence_ms: int = 800


@dataclass
class WebtoolsSettings:
    timeout_secs: int = 15


@dataclass
class BranchSummarySettings:
    reserve_tokens: int = 16384
    skip_prompt: bool = False


@dataclass
class ProviderRetrySettings:
    timeout_ms: int | None = None
    max_retries: int | None = None
    max_retry_delay_ms: int = 60000


@dataclass
class RetrySettings:
    enabled: bool = True
    max_retries: int = 3
    base_delay_ms: int = 2000
    provider: ProviderRetrySettings = field(default_factory=ProviderRetrySettings)


@dataclass
class TerminalSettings:
    show_images: bool = True
    image_width_cells: int = 60
    clear_on_shrink: bool = False
    show_terminal_progress: bool = False
    chime_on_turn_complete: bool = False


@dataclass
class ImageSettings:
    auto_resize: bool = True
    block_images: bool = False


@dataclass
class ThinkingBudgetsSettings:
    minimal: int | None = None
    low: int | None = None
    medium: int | None = None
    high: int | None = None


@dataclass
class MarkdownSettings:
    code_block_indent: str = "  "


@dataclass
class WarningSettings:
    anthropic_extra_usage: bool = True


@dataclass
class ModelCategories:
    fast: str | None = None
    standard: str | None = None
    capable: str | None = None


@dataclass
class PackageSourceFilter:
    source: str
    extensions: list[str] | None = None
    skills: list[str] | None = None
    prompts: list[str] | None = None
    themes: list[str] | None = None


# PackageSource is either a string or a PackageSourceFilter
PackageSource = str | PackageSourceFilter


@dataclass
class Settings:
    last_changelog_version: str | None = None
    default_provider: str | None = None
    default_model: str | None = None
    default_thinking_level: Literal["off", "minimal", "low", "medium", "high", "xhigh"] | None = (
        None
    )
    model_categories: ModelCategories | None = None
    transport: Literal["auto", "websocket", "sse"] = "auto"
    steering_mode: Literal["all", "one-at-a-time"] = "one-at-a-time"
    follow_up_mode: Literal["all", "one-at-a-time"] = "one-at-a-time"
    theme: str | None = None
    compaction: CompactionSettings = field(default_factory=CompactionSettings)
    tool_output: ToolOutputSettings = field(default_factory=ToolOutputSettings)
    context_gc: ContextGcSettings = field(default_factory=ContextGcSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    webtools: WebtoolsSettings = field(default_factory=WebtoolsSettings)
    disabled_tools: list[str] = field(default_factory=list)
    tool_output_display: Literal["collapsed", "peek", "standard"] = "standard"
    flags: dict[str, bool | str] = field(default_factory=dict)
    branch_summary: BranchSummarySettings = field(default_factory=BranchSummarySettings)
    retry: RetrySettings = field(default_factory=RetrySettings)
    hide_thinking_block: bool = False
    shell_path: str | None = None
    quiet_startup: bool = False
    shell_command_prefix: str | None = None
    npm_command: list[str] | None = None
    collapse_changelog: bool = False
    enable_install_telemetry: bool = True
    packages: list[PackageSource] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    slash_commands: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    enable_skill_commands: bool = True
    enable_subagent: bool = True
    warm_subagents: bool = False
    max_subagent_depth: int = 2
    nested_subagent_concurrency: int = 2
    enable_todo_write: bool = True
    enable_plugin_tools: bool = False
    support_platform: str | list[str] | None = None
    defer_mcp_schemas: bool = True
    enable_web_tools: bool = False
    enable_browser_tools: bool = False
    enable_browser_live_preview: bool = False
    enable_file_tools: bool = False
    enable_embsearch_tools: bool = True
    embsearch_binary_path: str | None = None
    embsearch_threshold_bytes: int = 0
    light: bool = False
    terminal: TerminalSettings = field(default_factory=TerminalSettings)
    images: ImageSettings = field(default_factory=ImageSettings)
    enabled_models: list[str] = field(default_factory=list)
    double_escape_action: Literal["fork", "tree", "none"] = "tree"
    tree_filter_mode: Literal["default", "no-tools", "user-only", "labeled-only", "all"] = "default"
    thinking_budgets: ThinkingBudgetsSettings = field(default_factory=ThinkingBudgetsSettings)
    thinking_display: Literal["summarized", "omitted"] | None = None
    editor_padding_x: int = 0
    autocomplete_max_visible: int = 5
    show_hardware_cursor: bool | None = None
    markdown: MarkdownSettings = field(default_factory=MarkdownSettings)
    warnings: WarningSettings = field(default_factory=WarningSettings)
    session_dir: str | None = None
