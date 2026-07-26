"""RPC protocol types for headless operation.

Port of ``rpc-types.ts`` from ``packages/coding-agent/src/modes/rpc/``.

Commands are sent as JSON lines on stdin.
Responses and events are emitted as JSON lines on stdout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Thinking levels
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# Queue modes
QueueMode = Literal["all", "one-at-a-time"]


# ============================================================================
# RPC Commands (stdin)
# ============================================================================


@dataclass
class PromptCommand:
    """Send a prompt to the agent."""

    type: Literal["prompt"] = "prompt"
    id: str | None = None
    message: str = ""
    images: list[dict[str, Any]] = field(default_factory=list)
    streaming_behavior: str | None = None


@dataclass
class SteerCommand:
    """Steer the current conversation."""

    type: Literal["steer"] = "steer"
    id: str | None = None
    message: str = ""
    images: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FollowUpCommand:
    """Send a follow-up message."""

    type: Literal["follow_up"] = "follow_up"
    id: str | None = None
    message: str = ""
    images: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AbortCommand:
    """Abort the current operation."""

    type: Literal["abort"] = "abort"
    id: str | None = None


@dataclass
class NewSessionCommand:
    """Create a new session."""

    type: Literal["new_session"] = "new_session"
    id: str | None = None
    parent_session: str | None = None


@dataclass
class GetStateCommand:
    """Get the current session state."""

    type: Literal["get_state"] = "get_state"
    id: str | None = None


@dataclass
class SetModelCommand:
    """Set the model to use."""

    type: Literal["set_model"] = "set_model"
    id: str | None = None
    provider: str = ""
    model_id: str = ""


@dataclass
class CycleModelCommand:
    """Cycle to the next model."""

    type: Literal["cycle_model"] = "cycle_model"
    id: str | None = None


@dataclass
class GetAvailableModelsCommand:
    """Get list of available models."""

    type: Literal["get_available_models"] = "get_available_models"
    id: str | None = None


@dataclass
class SetThinkingLevelCommand:
    """Set the thinking level."""

    type: Literal["set_thinking_level"] = "set_thinking_level"
    id: str | None = None
    level: ThinkingLevel = "off"


@dataclass
class CycleThinkingLevelCommand:
    """Cycle to the next thinking level."""

    type: Literal["cycle_thinking_level"] = "cycle_thinking_level"
    id: str | None = None


@dataclass
class SetSteeringModeCommand:
    """Set the steering mode."""

    type: Literal["set_steering_mode"] = "set_steering_mode"
    id: str | None = None
    mode: QueueMode = "all"


@dataclass
class SetFollowUpModeCommand:
    """Set the follow-up mode."""

    type: Literal["set_follow_up_mode"] = "set_follow_up_mode"
    id: str | None = None
    mode: QueueMode = "all"


@dataclass
class CompactCommand:
    """Compact the conversation."""

    type: Literal["compact"] = "compact"
    id: str | None = None
    custom_instructions: str | None = None


@dataclass
class SetAutoCompactionCommand:
    """Enable or disable auto-compaction."""

    type: Literal["set_auto_compaction"] = "set_auto_compaction"
    id: str | None = None
    enabled: bool = True


@dataclass
class SetAutoRetryCommand:
    """Enable or disable auto-retry."""

    type: Literal["set_auto_retry"] = "set_auto_retry"
    id: str | None = None
    enabled: bool = True


@dataclass
class AbortRetryCommand:
    """Abort the current retry."""

    type: Literal["abort_retry"] = "abort_retry"
    id: str | None = None


@dataclass
class BashCommand:
    """Execute a bash command."""

    type: Literal["bash"] = "bash"
    id: str | None = None
    command: str = ""


@dataclass
class AbortBashCommand:
    """Abort the current bash execution."""

    type: Literal["abort_bash"] = "abort_bash"
    id: str | None = None


@dataclass
class GetSessionStatsCommand:
    """Get session statistics."""

    type: Literal["get_session_stats"] = "get_session_stats"
    id: str | None = None


@dataclass
class ExportHtmlCommand:
    """Export session to HTML."""

    type: Literal["export_html"] = "export_html"
    id: str | None = None
    output_path: str | None = None


@dataclass
class SwitchSessionCommand:
    """Switch to a different session."""

    type: Literal["switch_session"] = "switch_session"
    id: str | None = None
    session_path: str = ""


@dataclass
class ForkCommand:
    """Fork the current session."""

    type: Literal["fork"] = "fork"
    id: str | None = None
    entry_id: str = ""


@dataclass
class CloneCommand:
    """Clone the current session."""

    type: Literal["clone"] = "clone"
    id: str | None = None


@dataclass
class GetForkMessagesCommand:
    """Get messages for forking."""

    type: Literal["get_fork_messages"] = "get_fork_messages"
    id: str | None = None


@dataclass
class GetLastAssistantTextCommand:
    """Get the last assistant message text."""

    type: Literal["get_last_assistant_text"] = "get_last_assistant_text"
    id: str | None = None


@dataclass
class SetSessionNameCommand:
    """Set the session name."""

    type: Literal["set_session_name"] = "set_session_name"
    id: str | None = None
    name: str = ""


@dataclass
class GetMessagesCommand:
    """Get all messages in the session."""

    type: Literal["get_messages"] = "get_messages"
    id: str | None = None


@dataclass
class GetCommandsCommand:
    """Get available slash commands."""

    type: Literal["get_commands"] = "get_commands"
    id: str | None = None


# Union of all command types
RpcCommand = (
    PromptCommand
    | SteerCommand
    | FollowUpCommand
    | AbortCommand
    | NewSessionCommand
    | GetStateCommand
    | SetModelCommand
    | CycleModelCommand
    | GetAvailableModelsCommand
    | SetThinkingLevelCommand
    | CycleThinkingLevelCommand
    | SetSteeringModeCommand
    | SetFollowUpModeCommand
    | CompactCommand
    | SetAutoCompactionCommand
    | SetAutoRetryCommand
    | AbortRetryCommand
    | BashCommand
    | AbortBashCommand
    | GetSessionStatsCommand
    | ExportHtmlCommand
    | SwitchSessionCommand
    | ForkCommand
    | CloneCommand
    | GetForkMessagesCommand
    | GetLastAssistantTextCommand
    | SetSessionNameCommand
    | GetMessagesCommand
    | GetCommandsCommand
)


# ============================================================================
# RPC Responses (stdout)
# ============================================================================


@dataclass
class RpcResponse:
    """Base RPC response."""

    type: Literal["response"] = "response"
    id: str | None = None
    command: str = ""
    success: bool = True
    data: dict[str, Any] | None = None
    error: str | None = None


# ============================================================================
# Extension UI Events (stdout)
# ============================================================================


@dataclass
class RpcExtensionUIRequest:
    """Extension UI request."""

    type: Literal["extension_ui_request"] = "extension_ui_request"
    id: str = ""
    method: str = ""
    title: str = ""
    message: str = ""
    options: list[str] = field(default_factory=list)
    placeholder: str | None = None
    timeout: int | None = None
    notify_type: str | None = None
    status_key: str = ""
    status_text: str | None = None
    widget_key: str = ""
    widget_lines: list[str] | None = None
    widget_placement: str | None = None
    text: str = ""


# ============================================================================
# Extension UI Commands (stdin)
# ============================================================================


@dataclass
class RpcExtensionUIResponse:
    """Response to an extension UI request."""

    type: Literal["extension_ui_response"] = "extension_ui_response"
    id: str = ""
    value: str | None = None
    confirmed: bool | None = None
    cancelled: bool = False


# ============================================================================
# Session State
# ============================================================================


@dataclass
class RpcSessionState:
    """Session state for RPC."""

    model: dict[str, Any] | None = None
    thinking_level: ThinkingLevel = "off"
    is_streaming: bool = False
    is_compacting: bool = False
    steering_mode: QueueMode = "all"
    follow_up_mode: QueueMode = "all"
    session_file: str | None = None
    session_id: str = ""
    session_name: str | None = None
    auto_compaction_enabled: bool = True
    message_count: int = 0
    pending_message_count: int = 0


# ============================================================================
# Slash Command
# ============================================================================


@dataclass
class RpcSlashCommand:
    """A command available for invocation via prompt."""

    name: str = ""
    description: str = ""
    source: Literal["extension", "prompt", "skill"] = "extension"
    source_info: dict[str, Any] | None = None
