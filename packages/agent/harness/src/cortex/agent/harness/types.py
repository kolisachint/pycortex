# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false, reportRedeclaration=false, reportMissingTypeArgument=false
"""Harness types.

Mechanical port of hoocode's ``packages/agent/src/harness/types.ts``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from cortex.agent.types import AgentMessage, AgentTool, ThinkingLevel
from cortex.ai.types import ImageContent, Model, TextContent

# Type variables for generic types
TSkill = TypeVar("TSkill", bound="Skill")
TPromptTemplate = TypeVar("TPromptTemplate", bound="PromptTemplate")
TTool = TypeVar("TTool", bound="AgentTool")

__all__ = [
    "AbortEvent",
    "AbortResult",
    "AgentHarnessEvent",
    "AgentHarnessEventResultMap",
    "AgentHarnessOptions",
    "AgentHarnessOwnEvent",
    "AgentHarnessPhase",
    "AgentHarnessPromptOptions",
    "AgentHarnessResources",
    "AgentHarnessTurnState",
    "BeforeAgentStartEvent",
    "BeforeAgentStartResult",
    "BeforeProviderRequestEvent",
    "BeforeProviderRequestResult",
    "AfterProviderResponseEvent",
    "BranchSummaryEntry",
    "CompactResult",
    "CompactionEntry",
    "ContextEvent",
    "ContextResult",
    "CustomEntry",
    "CustomMessageEntry",
    "ExecutionEnv",
    "ExecutionEnvExecOptions",
    "FileErrorCode",
    "FileError",
    "FileKind",
    "FileInfo",
    "LabelEntry",
    "MessageEntry",
    "ModelChangeEntry",
    "ModelSelectEvent",
    "NavigateTreeResult",
    "PendingSessionWrite",
    "PromptTemplate",
    "QueueUpdateEvent",
    "ResourcesUpdateEvent",
    "SavePointEvent",
    "SessionContext",
    "SessionCreateOptions",
    "SessionForkOptions",
    "SessionInfoEntry",
    "SessionMetadata",
    "SessionStorage",
    "SessionTreeEntry",
    "SessionTreeEntryBase",
    "SessionBeforeCompactEvent",
    "SessionBeforeCompactResult",
    "SessionBeforeTreeEvent",
    "SessionBeforeTreeResult",
    "SessionCompactEvent",
    "SessionTreeEvent",
    "SettledEvent",
    "Skill",
    "SystemPromptFn",
    "ThinkingLevelSelectEvent",
    "ToolCallEvent",
    "ToolCallResult",
    "ToolResultEvent",
    "ToolResultPatch",
    "TreePreparation",
    "JsonlSessionCreateOptions",
    "JsonlSessionListOptions",
    "JsonlSessionMetadata",
    "JsonlSessionRepoApi",
]


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """Skill loaded from a SKILL.md file or provided by an application.

    ``name``, ``description``, and ``file_path`` are inserted into the system
    prompt in an XML-formatted block as suggested by agentskills.io.
    Use :func:`format_skills_for_system_prompt` to generate the spec-compatible
    system prompt block.
    """

    name: str
    """Stable skill name used for lookup and model-visible listings."""

    description: str
    """Short model-visible description of when to use the skill."""

    content: str
    """Full skill instructions."""

    file_path: str
    """Absolute path to the skill file. Used for model-visible location and
    resolving relative references."""

    disable_model_invocation: bool = False
    """Exclude this skill from model-visible skill lists while still allowing
    explicit application invocation."""


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------


@dataclass
class PromptTemplate:
    """Prompt template that can be formatted into a prompt for explicit invocation."""

    name: str
    """Stable template name used for lookup or application command routing."""

    content: str
    """Template content. Argument placeholders are formatted by
    ``format_prompt_template_invocation``."""

    description: str = ""
    """Optional description for command lists or autocomplete."""


# ---------------------------------------------------------------------------
# Agent harness resources
# ---------------------------------------------------------------------------


@dataclass
class AgentHarnessResources:
    """Resources made available to explicit invocation methods and system-prompt callbacks."""

    prompt_templates: list[PromptTemplate] = field(default_factory=list)
    """Prompt templates available for explicit invocation."""

    skills: list[Skill] = field(default_factory=list)
    """Skills available to the model and explicit skill invocation."""


# ---------------------------------------------------------------------------
# File kind / error
# ---------------------------------------------------------------------------

FileKind = Literal["file", "directory", "symlink"]
"""Kind of filesystem object as addressed by an ExecutionEnv."""

FileErrorCode = Literal[
    "not_found",
    "permission_denied",
    "not_directory",
    "is_directory",
    "invalid",
    "not_supported",
    "unknown",
]
"""Stable, backend-independent file error codes thrown by ExecutionEnv file operations."""


class FileError(Exception):
    """Error thrown by ExecutionEnv file operations."""

    def __init__(
        self,
        code: FileErrorCode,
        message: str,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.name = "FileError"
        self.code = code
        self.path = path


# ---------------------------------------------------------------------------
# File info
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    """Metadata for one filesystem object in an ExecutionEnv."""

    name: str
    """Basename of path."""

    path: str
    """Absolute, syntactically normalized addressed path in the execution environment.
    Symlinks are not followed."""

    kind: FileKind
    """Object kind. Symlink targets are not followed."""

    size: int
    """Size in bytes for the addressed filesystem object."""

    mtime_ms: float
    """Modification time as milliseconds since Unix epoch."""


# ---------------------------------------------------------------------------
# Execution env exec options
# ---------------------------------------------------------------------------


@dataclass
class ExecutionEnvExecOptions:
    """Options for ExecutionEnv.exec."""

    cwd: str | None = None
    """Working directory for the command."""

    env: dict[str, str] | None = None
    """Additional environment variables for the command."""

    timeout: int | None = None
    """Timeout in seconds."""

    signal: Any = None
    """Abort signal used to terminate the command."""

    on_stdout: Callable[[str], None] | None = None
    """Called with stdout chunks as they are produced."""

    on_stderr: Callable[[str], None] | None = None
    """Called with stderr chunks as they are produced."""


# ---------------------------------------------------------------------------
# Execution env result
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    """Result of executing a command."""

    stdout: str
    stderr: str
    exit_code: int


# ---------------------------------------------------------------------------
# Execution env (protocol)
# ---------------------------------------------------------------------------


class ExecutionEnv:
    """Filesystem and process execution environment used by the harness.

    Paths passed to methods may be absolute or relative to ``cwd``.
    """

    cwd: str
    """Current working directory for relative paths and command execution."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd

    async def exec(
        self,
        command: str,
        options: ExecutionEnvExecOptions | None = None,
    ) -> ExecResult:
        """Execute a shell command in ``cwd`` unless ``options.cwd`` is provided."""
        raise NotImplementedError

    async def read_text_file(self, path: str) -> str:
        """Read a UTF-8 text file."""
        raise NotImplementedError

    async def read_binary_file(self, path: str) -> bytes:
        """Read a binary file."""
        raise NotImplementedError

    async def write_file(self, path: str, content: str | bytes) -> None:
        """Create or overwrite a file."""
        raise NotImplementedError

    async def file_info(self, path: str) -> FileInfo:
        """Return metadata for the addressed path without following symlinks."""
        raise NotImplementedError

    async def list_dir(self, path: str) -> list[FileInfo]:
        """List direct children of a directory without following symlinks."""
        raise NotImplementedError

    async def real_path(self, path: str) -> str:
        """Return the canonical path for a path, following symlinks."""
        raise NotImplementedError

    async def exists(self, path: str) -> bool:
        """Return false for missing paths."""
        raise NotImplementedError

    async def create_dir(
        self,
        path: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Create a directory."""
        raise NotImplementedError

    async def remove(
        self,
        path: str,
        options: dict[str, Any] | None = None,
    ) -> None:
        """Remove a file or directory."""
        raise NotImplementedError

    async def create_temp_dir(self, prefix: str | None = None) -> str:
        """Create a temporary directory and return its absolute path."""
        raise NotImplementedError

    async def create_temp_file(
        self,
        options: dict[str, str] | None = None,
    ) -> str:
        """Create a temporary file and return its absolute path."""
        raise NotImplementedError

    async def cleanup(self) -> None:
        """Release resources owned by the environment."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Session tree entry types
# ---------------------------------------------------------------------------


@dataclass
class SessionTreeEntryBase:
    """Base class for session tree entries."""

    type: str = ""
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""


@dataclass
class MessageEntry(SessionTreeEntryBase):
    """A message entry in the session tree."""

    type: str = "message"
    message: AgentMessage = None  # type: ignore[assignment]


@dataclass
class ThinkingLevelChangeEntry(SessionTreeEntryBase):
    """A thinking level change entry."""

    type: str = "thinking_level_change"
    thinking_level: str = ""


@dataclass
class ModelChangeEntry(SessionTreeEntryBase):
    """A model change entry."""

    type: str = "model_change"
    provider: str = ""
    model_id: str = ""


@dataclass
class CompactionEntry:
    """A compaction entry in the session tree."""

    type: str = "compaction"
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    summary: str = ""
    first_kept_entry_id: str = ""
    tokens_before: int = 0
    tokens_after: int | None = None
    details: Any = None
    from_hook: bool = False


@dataclass
class BranchSummaryEntry:
    """A branch summary entry."""

    type: str = "branch_summary"
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    from_id: str = ""
    summary: str = ""
    details: Any = None
    from_hook: bool = False


@dataclass
class CustomEntry:
    """A custom entry."""

    type: str = "custom"
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    custom_type: str = ""
    details: Any = None


@dataclass
class CustomMessageEntry:
    """A custom message entry."""

    type: str = "custom_message"
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    custom_type: str = ""
    content: str | list[TextContent | ImageContent] = ""
    details: Any = None
    display: bool = False


@dataclass
class LabelEntry:
    """A label entry."""

    type: str = "label"
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    target_id: str = ""
    label: str | None = None


@dataclass
class SessionInfoEntry:
    """A session info entry (legacy name, kept for backwards compatibility)."""

    type: str = "session_info"
    id: str = ""
    parent_id: str | None = None
    timestamp: str = ""
    name: str = ""


# Union type for all session tree entries
SessionTreeEntry = (
    MessageEntry
    | ThinkingLevelChangeEntry
    | ModelChangeEntry
    | CompactionEntry
    | BranchSummaryEntry
    | CustomEntry
    | CustomMessageEntry
    | LabelEntry
    | SessionInfoEntry
)


# ---------------------------------------------------------------------------
# Session context / metadata
# ---------------------------------------------------------------------------


@dataclass
class SessionContext:
    """Session context with messages and state."""

    messages: list[AgentMessage] = field(default_factory=list)
    thinking_level: str = "off"
    model: dict[str, str] | None = None


@dataclass
class SessionMetadata:
    """Basic session metadata."""

    id: str = ""
    created_at: str = ""


@dataclass
class JsonlSessionMetadata(SessionMetadata):
    """JSONL session metadata."""

    cwd: str = ""
    path: str = ""
    parent_session_path: str | None = None


# ---------------------------------------------------------------------------
# Session storage (protocol)
# ---------------------------------------------------------------------------


class SessionStorage:
    """Session storage interface."""

    async def get_metadata(self) -> SessionMetadata:
        raise NotImplementedError

    async def get_leaf_id(self) -> str | None:
        raise NotImplementedError

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        raise NotImplementedError

    async def create_entry_id(self) -> str:
        raise NotImplementedError

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        raise NotImplementedError

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        raise NotImplementedError

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        raise NotImplementedError

    async def get_label(self, id: str) -> str | None:
        raise NotImplementedError

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        raise NotImplementedError

    async def get_entries(self) -> list[SessionTreeEntry]:
        raise NotImplementedError


class Session:
    """Session class that wraps SessionStorage."""

    def __init__(self, storage: SessionStorage) -> None:
        self._storage = storage

    async def get_metadata(self) -> SessionMetadata:
        return await self._storage.get_metadata()

    def get_storage(self) -> SessionStorage:
        return self._storage

    async def get_leaf_id(self) -> str | None:
        return await self._storage.get_leaf_id()

    async def set_leaf_id(self, leaf_id: str | None) -> None:
        await self._storage.set_leaf_id(leaf_id)

    async def create_entry_id(self) -> str:
        return await self._storage.create_entry_id()

    async def append_entry(self, entry: SessionTreeEntry) -> None:
        await self._storage.append_entry(entry)

    async def get_entry(self, id: str) -> SessionTreeEntry | None:
        return await self._storage.get_entry(id)

    async def find_entries(self, type: str) -> list[SessionTreeEntry]:
        return await self._storage.find_entries(type)

    async def get_label(self, id: str) -> str | None:
        return await self._storage.get_label(id)

    async def get_path_to_root(self, leaf_id: str | None) -> list[SessionTreeEntry]:
        return await self._storage.get_path_to_root(leaf_id)

    async def get_entries(self) -> list[SessionTreeEntry]:
        return await self._storage.get_entries()

    async def build_context(self) -> SessionContext:
        """Build session context from path entries."""
        entries = await self.get_path_to_root(await self.get_leaf_id())
        return build_session_context(entries)

    async def append_message(self, message: AgentMessage) -> None:
        """Append a message to the session."""
        entry_id = await self.create_entry_id()
        entry = MessageEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            message=message,
        )
        await self.append_entry(entry)

    async def append_model_change(self, provider: str, model_id: str) -> None:
        """Append a model change to the session."""
        entry_id = await self.create_entry_id()
        entry = ModelChangeEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            provider=provider,
            model_id=model_id,
        )
        await self.append_entry(entry)

    async def append_thinking_level_change(self, level: str) -> None:
        """Append a thinking level change to the session."""
        entry_id = await self.create_entry_id()
        entry = ThinkingLevelChangeEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            thinking_level=level,
        )
        await self.append_entry(entry)

    async def append_custom_entry(self, custom_type: str, data: Any = None) -> None:
        """Append a custom entry to the session."""
        entry_id = await self.create_entry_id()
        entry = CustomEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            custom_type=custom_type,
            details=data,
        )
        await self.append_entry(entry)

    async def append_custom_message_entry(
        self,
        custom_type: str,
        content: str | list[TextContent | ImageContent],
        display: bool,
        details: Any = None,
    ) -> None:
        """Append a custom message entry to the session."""
        entry_id = await self.create_entry_id()
        entry = CustomMessageEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            custom_type=custom_type,
            content=content,
            display=display,
            details=details,
        )
        await self.append_entry(entry)

    async def append_label(self, target_id: str, label: str | None) -> None:
        """Append a label to the session."""
        entry_id = await self.create_entry_id()
        entry = LabelEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            target_id=target_id,
            label=label,
        )
        await self.append_entry(entry)

    async def append_session_name(self, name: str) -> None:
        """Append a session name to the session."""
        entry_id = await self.create_entry_id()
        entry = SessionInfoEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            name=name,
        )
        await self.append_entry(entry)

    async def append_compaction(
        self,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Any = None,
        from_hook: bool = False,
        tokens_after: int | None = None,
    ) -> str:
        """Append a compaction entry to the session."""
        entry_id = await self.create_entry_id()
        entry = CompactionEntry(
            id=entry_id,
            parent_id=await self.get_leaf_id(),
            timestamp=str(int(time.time() * 1000)),
            summary=summary,
            first_kept_entry_id=first_kept_entry_id,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            details=details,
            from_hook=from_hook,
        )
        await self.append_entry(entry)
        return entry_id

    async def get_branch(self) -> list[SessionTreeEntry]:
        """Get the current branch of entries."""
        leaf_id = await self.get_leaf_id()
        return await self.get_path_to_root(leaf_id)

    async def move_to(
        self,
        new_leaf_id: str | None,
        summary: dict[str, Any] | None = None,
    ) -> str | None:
        """Move to a new leaf in the session tree."""
        if summary:
            entry_id = await self.create_entry_id()
            entry = BranchSummaryEntry(
                id=entry_id,
                parent_id=new_leaf_id,
                timestamp=str(int(time.time() * 1000)),
                summary=summary.get("summary", ""),
                details=summary.get("details"),
                from_hook=summary.get("fromHook", False),
            )
            await self.append_entry(entry)
            await self.set_leaf_id(entry_id)
            return entry_id
        else:
            await self.set_leaf_id(new_leaf_id)
            return None


def build_session_context(entries: list[SessionTreeEntry]) -> SessionContext:
    """Build session context from path entries."""
    thinking_level = "off"
    model: dict[str, str] | None = None
    compaction: CompactionEntry | None = None

    for entry in entries:
        if isinstance(entry, ThinkingLevelChangeEntry):
            thinking_level = entry.thinking_level
        elif isinstance(entry, ModelChangeEntry):
            model = {"provider": entry.provider, "modelId": entry.model_id}
        elif (
            isinstance(entry, MessageEntry)
            and hasattr(entry.message, "role")
            and entry.message.role == "assistant"
        ):
            model = {"provider": entry.message.provider, "modelId": entry.message.model}
        elif isinstance(entry, CompactionEntry):
            compaction = entry

    messages: list[AgentMessage] = []

    def append_message(entry: SessionTreeEntry) -> None:
        if isinstance(entry, MessageEntry):
            messages.append(entry.message)
        elif isinstance(entry, CustomMessageEntry):
            from .messages import create_custom_message

            messages.append(
                create_custom_message(
                    entry.custom_type,
                    entry.content,
                    entry.display,
                    entry.details,
                    entry.timestamp,
                )
            )
        elif isinstance(entry, BranchSummaryEntry) and entry.summary:
            from .messages import create_branch_summary_message

            messages.append(
                create_branch_summary_message(entry.summary, entry.from_id, entry.timestamp)
            )

    if compaction:
        from .messages import create_compaction_summary_message

        messages.append(
            create_compaction_summary_message(
                compaction.summary,
                compaction.tokens_before,
                compaction.timestamp,
                compaction.tokens_after,
            )
        )
        # Find entries after compaction
        compaction_idx = next(
            (
                i
                for i, e in enumerate(entries)
                if isinstance(e, CompactionEntry) and e.id == compaction.id
            ),
            -1,
        )
        if compaction_idx >= 0:
            found_first_kept = False
            for i in range(compaction_idx):
                entry = entries[i]
                if entry.id == compaction.first_kept_entry_id:
                    found_first_kept = True
                if found_first_kept:
                    append_message(entry)
            for i in range(compaction_idx + 1, len(entries)):
                append_message(entries[i])
    else:
        for entry in entries:
            append_message(entry)

    return SessionContext(messages=messages, thinking_level=thinking_level, model=model)


# ---------------------------------------------------------------------------
# Session options
# ---------------------------------------------------------------------------


@dataclass
class SessionCreateOptions:
    """Options for creating a session."""

    id: str | None = None


@dataclass
class SessionForkOptions:
    """Options for forking a session."""

    entry_id: str | None = None
    position: str | None = None  # "before" | "at"
    id: str | None = None


# ---------------------------------------------------------------------------
# Session repo (protocol)
# ---------------------------------------------------------------------------


class SessionRepo:
    """Session repository interface."""

    async def create(self, options: SessionCreateOptions | None = None) -> Any:
        raise NotImplementedError

    async def open(self, metadata: SessionMetadata) -> Any:
        raise NotImplementedError

    async def list(self, options: Any = None) -> list[SessionMetadata]:
        raise NotImplementedError

    async def delete(self, metadata: SessionMetadata) -> None:
        raise NotImplementedError

    async def fork(
        self,
        source: SessionMetadata,
        options: SessionForkOptions | None = None,
    ) -> Any:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# JSONL session options
# ---------------------------------------------------------------------------


@dataclass
class JsonlSessionCreateOptions(SessionCreateOptions):
    """JSONL session create options."""

    cwd: str = ""
    parent_session_path: str | None = None


@dataclass
class JsonlSessionListOptions:
    """JSONL session list options."""

    cwd: str | None = None


class JsonlSessionRepoApi(SessionRepo):
    """JSONL session repository API."""


# ---------------------------------------------------------------------------
# Agent harness phase
# ---------------------------------------------------------------------------

AgentHarnessPhase = Literal["idle", "turn", "compaction", "branch_summary", "retry"]


# ---------------------------------------------------------------------------
# Pending session write
# ---------------------------------------------------------------------------

# This is a complex type in TS that represents a session tree entry without id/parentId/timestamp
# In Python, we represent it as a dict
PendingSessionWrite = dict[str, Any]


# ---------------------------------------------------------------------------
# Agent harness turn state
# ---------------------------------------------------------------------------


@dataclass
class AgentHarnessTurnState:
    """Turn state for the agent harness."""

    messages: list[AgentMessage] = field(default_factory=list)
    resources: AgentHarnessResources = field(default_factory=AgentHarnessResources)
    system_prompt: str = ""
    model: Model[Any] | None = None
    thinking_level: ThinkingLevel = "off"
    tools: list[AgentTool[Any, Any]] = field(default_factory=list)
    active_tools: list[AgentTool[Any, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass
class QueueUpdateEvent:
    """Queue update event."""

    type: str = "queue_update"
    steer: list[AgentMessage] = field(default_factory=list)
    follow_up: list[AgentMessage] = field(default_factory=list)
    next_turn: list[AgentMessage] = field(default_factory=list)


@dataclass
class SavePointEvent:
    """Save point event."""

    type: str = "save_point"
    had_pending_mutations: bool = False


@dataclass
class AbortEvent:
    """Abort event."""

    type: str = "abort"
    cleared_steer: list[AgentMessage] = field(default_factory=list)
    cleared_follow_up: list[AgentMessage] = field(default_factory=list)


@dataclass
class SettledEvent:
    """Settled event."""

    type: str = "settled"
    next_turn_count: int = 0


@dataclass
class BeforeAgentStartEvent:
    """Before agent start event."""

    type: str = "before_agent_start"
    prompt: str = ""
    images: list[ImageContent] | None = None
    system_prompt: str = ""
    resources: AgentHarnessResources = field(default_factory=AgentHarnessResources)


@dataclass
class ContextEvent:
    """Context event."""

    type: str = "context"
    messages: list[AgentMessage] = field(default_factory=list)


@dataclass
class BeforeProviderRequestEvent:
    """Before provider request event."""

    type: str = "before_provider_request"
    payload: Any = None


@dataclass
class AfterProviderResponseEvent:
    """After provider response event."""

    type: str = "after_provider_response"
    status: int = 0
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ToolCallEvent:
    """Tool call event."""

    type: str = "tool_call"
    tool_call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResultEvent:
    """Tool result event."""

    type: str = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    content: list[TextContent | ImageContent] = field(default_factory=list)
    details: Any = None
    is_error: bool = False


@dataclass
class SessionBeforeCompactEvent:
    """Session before compact event."""

    type: str = "session_before_compact"
    preparation: Any = None  # CompactionPreparation
    branch_entries: list[SessionTreeEntry] = field(default_factory=list)
    custom_instructions: str | None = None
    signal: Any = None  # AbortSignal


@dataclass
class SessionCompactEvent:
    """Session compact event."""

    type: str = "session_compact"
    compaction_entry: CompactionEntry = field(default_factory=CompactionEntry)
    from_hook: bool = False


@dataclass
class TreePreparation:
    """Preparation for tree navigation."""

    target_id: str = ""
    old_leaf_id: str | None = None
    common_ancestor_id: str | None = None
    entries_to_summarize: list[SessionTreeEntry] = field(default_factory=list)
    user_wants_summary: bool = False
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


@dataclass
class SessionBeforeTreeEvent:
    """Session before tree event."""

    type: str = "session_before_tree"
    preparation: TreePreparation = field(default_factory=TreePreparation)
    signal: Any = None  # AbortSignal


@dataclass
class SessionTreeEvent:
    """Session tree event."""

    type: str = "session_tree"
    new_leaf_id: str | None = None
    old_leaf_id: str | None = None
    summary_entry: BranchSummaryEntry | None = None
    from_hook: bool = False


@dataclass
class ModelSelectEvent:
    """Model select event."""

    type: str = "model_select"
    model: Model[Any] | None = None
    previous_model: Model[Any] | None = None
    source: str = ""  # "set" | "restore"


@dataclass
class ThinkingLevelSelectEvent:
    """Thinking level select event."""

    type: str = "thinking_level_select"
    level: ThinkingLevel = "off"
    previous_level: ThinkingLevel = "off"


@dataclass
class ResourcesUpdateEvent:
    """Resources update event."""

    type: str = "resources_update"
    resources: AgentHarnessResources = field(default_factory=AgentHarnessResources)
    previous_resources: AgentHarnessResources = field(default_factory=AgentHarnessResources)


# Union type for own events
AgentHarnessOwnEvent = (
    QueueUpdateEvent
    | SavePointEvent
    | AbortEvent
    | SettledEvent
    | BeforeAgentStartEvent
    | ContextEvent
    | BeforeProviderRequestEvent
    | AfterProviderResponseEvent
    | ToolCallEvent
    | ToolResultEvent
    | SessionBeforeCompactEvent
    | SessionCompactEvent
    | SessionBeforeTreeEvent
    | SessionTreeEvent
    | ModelSelectEvent
    | ThinkingLevelSelectEvent
    | ResourcesUpdateEvent
)

# Agent event is Any for now (AgentEvent from types.py)
AgentHarnessEvent = Any


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class BeforeAgentStartResult:
    """Result of before_agent_start hook."""

    messages: list[AgentMessage] | None = None
    system_prompt: str | None = None


@dataclass
class ContextResult:
    """Result of context hook."""

    messages: list[AgentMessage] = field(default_factory=list)


@dataclass
class BeforeProviderRequestResult:
    """Result of before_provider_request hook."""

    payload: Any = None


@dataclass
class ToolCallResult:
    """Result of tool_call hook."""

    block: bool = False
    reason: str | None = None


@dataclass
class ToolResultPatch:
    """Result of tool_result hook."""

    content: list[TextContent | ImageContent] | None = None
    details: Any = None
    is_error: bool | None = None
    terminate: bool | None = None


@dataclass
class SessionBeforeCompactResult:
    """Result of session_before_compact hook."""

    cancel: bool = False
    compaction: Any = None  # CompactResult


@dataclass
class SessionBeforeTreeResult:
    """Result of session_before_tree hook."""

    cancel: bool = False
    summary: dict[str, Any] | None = None
    custom_instructions: str | None = None
    replace_instructions: bool = False
    label: str | None = None


# Event result map (maps event types to their result types)
AgentHarnessEventResultMap = {
    "before_agent_start": BeforeAgentStartResult | None,
    "context": ContextResult | None,
    "before_provider_request": BeforeProviderRequestResult | None,
    "after_provider_response": None,
    "tool_call": ToolCallResult | None,
    "tool_result": ToolResultPatch | None,
    "session_before_compact": SessionBeforeCompactResult | None,
    "session_compact": None,
    "session_before_tree": SessionBeforeTreeResult | None,
    "session_tree": None,
    "model_select": None,
    "thinking_level_select": None,
    "resources_update": None,
    "queue_update": None,
    "save_point": None,
    "abort": None,
    "settled": None,
}


# ---------------------------------------------------------------------------
# Other types
# ---------------------------------------------------------------------------


@dataclass
class AgentHarnessPromptOptions:
    """Options for agent harness prompt."""

    images: list[ImageContent] | None = None


@dataclass
class AbortResult:
    """Result of abort operation."""

    cleared_steer: list[AgentMessage] = field(default_factory=list)
    cleared_follow_up: list[AgentMessage] = field(default_factory=list)


# Alias for the harness event API
CompactResult = Any  # CompactionResult


@dataclass
class NavigateTreeResult:
    """Result of navigate_tree operation."""

    cancelled: bool = False
    editor_text: str | None = None
    summary_entry: BranchSummaryEntry | None = None


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# System prompt function type
# ---------------------------------------------------------------------------

SystemPromptFn = Callable[..., Any]
"""Callable that generates a system prompt. Takes keyword arguments for
env, session, model, thinking_level, active_tools, and resources."""


# ---------------------------------------------------------------------------
# Agent harness options
# ---------------------------------------------------------------------------


@dataclass
class AgentHarnessOptions:
    """Options for constructing an AgentHarness."""

    env: ExecutionEnv = field(default_factory=lambda: ExecutionEnv(cwd=""))
    session: Any = None  # Session
    tools: list[AgentTool[Any, Any]] | None = None
    resources: AgentHarnessResources | None = None
    system_prompt: str | SystemPromptFn | None = None
    get_api_key_and_headers: Callable[..., Any] | None = None
    model: Model[Any] | None = None
    thinking_level: ThinkingLevel | None = None
    active_tool_names: list[str] | None = None
    steering_mode: str | None = None  # QueueMode
    follow_up_mode: str | None = None  # QueueMode
