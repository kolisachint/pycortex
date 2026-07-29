"""The bridge between a run mode and the agent.

Port of ``core/agent-session.ts`` (2,479 lines) — the part of it that a mode
needs to hold a conversation: subscribe to the agent, turn a line of text into a
turn, keep the steering and follow-up queues the UI displays, persist what the
agent produced, and abort what is in flight. ``AgentSession`` is shared by all
three modes in the TS (interactive, print, rpc); step 7.4 wires the first of
them.

**What is here, and what the TS also has.** The class in the TS is the session
*and* the extension host, the tool registry, the system-prompt builder, the
compaction/retry/tree controllers and the HTML/JSONL exporters. Each of those
hangs off a subsystem this port has not reached, and inventing a stand-in for one
is how a step ends up "done" over something nobody can run. So they are absent
rather than faked, and each is named where it would have been:

* the **extension runner** (``_emitExtensionEvent``, ``bindExtensions``,
  ``activatePlugin``, extension commands in :meth:`AgentSession.prompt`) —
  ``core/extensions/**``, which `code/extensions` has not ported;
* the **tool registry** and ``_rebuildSystemPrompt`` — steps 7.5/7.6, which are
  what first need a tool to run;
* **compaction, auto-retry and tree navigation** — the controllers exist next
  door (:mod:`cortex.code.session.compaction`, ``.retry``, ``.tree_navigation``)
  but read messages as dicts with camelCase keys, so wiring them to the pydantic
  messages the agent emits is its own step, not a side effect of this one.
  :attr:`AgentSession.retry_attempt` is 0 until then, and the interactive mode
  reads it exactly where the TS does;
* **model management** (``setModel``, ``cycleModel``, thinking levels) — step
  7.9/7.11, with the model registry and the selector overlays;
* **skill and prompt-template expansion** in :meth:`AgentSession.prompt` — step
  7.8, which is where ``/``-commands start meaning anything. Until then the
  submitted line reaches the agent as typed, so a ``/`` prefix is prompt text.

The auth preflight is the one place a missing subsystem changes behaviour rather
than removing it: with no ``model_registry`` configured there is nothing that can
answer "is there a key for this model", so the check falls back to the only
answer available — the ``DEFAULT_MODEL`` sentinel the agent starts on, whose
provider is ``"unknown"``, which is the case ``formatNoApiKeyFoundMessage``
already words ("No API key found for the selected model"). That is what a fresh
``pycortex`` install shows when you press Enter, and it is what the TS shows for
the same reason: its registry answers ``false`` for that same sentinel.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from cortex.code.config import (
    format_no_api_key_found_message,
    format_no_model_selected_message,
)
from cortex.code.config.auth_guidance import UNKNOWN_PROVIDER
from cortex.code.session.bash_executor import BashResult, execute_bash_with_operations
from cortex.code.session.stats import ContextUsage, compute_context_usage

__all__ = [
    "AgentSession",
    "AgentSessionConfig",
    "AgentSessionEvent",
    "AgentSessionEventListener",
    "ModelRegistryLike",
    "PromptOptions",
]

#: Session events are the agent's events plus the few the session adds
#: (``queue_update``, ``session_info_changed``, the compaction/retry pairs). Like
#: the agent's, they are dicts tagged with ``type`` in this port.
AgentSessionEvent = dict[str, Any]

AgentSessionEventListener = Callable[[AgentSessionEvent], Any]

StreamingBehavior = Literal["steer", "followUp"]


class ModelRegistryLike(Protocol):
    """The slice of ``ModelRegistry`` the prompt preflight uses (step 7.11)."""

    def has_configured_auth(self, model: Any) -> bool: ...

    def is_using_oauth(self, model: Any) -> bool: ...


@dataclass
class AgentSessionConfig:
    """Everything the session needs to exist. Port of ``AgentSessionConfig``."""

    agent: Any
    session_manager: Any
    settings_manager: Any
    cwd: str
    #: Model registry for API key resolution. Step 7.11 supplies the real one;
    #: without it the key preflight cannot run and is skipped.
    model_registry: ModelRegistryLike | None = None
    #: Models to cycle through with Ctrl+P (from ``--models``).
    scoped_models: list[Any] = field(default_factory=list)


@dataclass
class PromptOptions:
    """Options for :meth:`AgentSession.prompt`. Port of ``PromptOptions``."""

    #: Whether to expand file-based prompt templates (default: true).
    expand_prompt_templates: bool = True
    #: Image attachments.
    images: list[Any] | None = None
    #: When streaming, how to queue the message: "steer" (interrupt) or
    #: "followUp" (wait). Required if streaming.
    streaming_behavior: StreamingBehavior | None = None
    #: Source of input for extension input handlers. Defaults to "interactive".
    source: str = "interactive"
    #: Hook used by RPC mode to observe preflight acceptance or rejection.
    preflight_result: Callable[[bool], None] | None = None


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def _field(value: Any, name: str) -> Any:
    """Read a field off a message, however the producer shaped it."""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _event_type(event: Any) -> str:
    if isinstance(event, dict):
        return str(event.get("type", ""))
    return str(getattr(event, "type", ""))


def _event_message(event: Any) -> Any:
    if isinstance(event, dict):
        return event.get("message")
    return getattr(event, "message", None)


class AgentSession:
    """Agent lifecycle and session management, shared by every run mode."""

    def __init__(self, config: AgentSessionConfig) -> None:
        self.agent = config.agent
        self.session_manager = config.session_manager
        self.settings_manager = config.settings_manager
        self._cwd = config.cwd
        self._model_registry = config.model_registry
        self._scoped_models = list(config.scoped_models)

        self._event_listeners: list[AgentSessionEventListener] = []
        #: Pending steering messages, for UI display. Removed when delivered.
        self._steering_messages: list[str] = []
        #: Pending follow-up messages, for UI display. Removed when delivered.
        self._follow_up_messages: list[str] = []
        self._last_assistant_message: Any = None
        #: Bash rows that finished mid-turn, waiting for the turn to end.
        self._pending_bash_messages: list[dict[str, Any]] = []
        self._bash_abort_controller: Any = None

        # Always subscribe to agent events for internal handling (session
        # persistence, and the queue bookkeeping the UI reads).
        self._unsubscribe_agent: Callable[[], None] | None = self.agent.subscribe(
            self._handle_agent_event
        )

    # =====================================================================
    # Event subscription
    # =====================================================================

    def subscribe(self, listener: AgentSessionEventListener) -> Callable[[], None]:
        """Subscribe to session events. Returns this listener's unsubscribe."""
        self._event_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return unsubscribe

    def _emit(self, event: AgentSessionEvent) -> None:
        """Emit an event to all listeners."""
        for listener in list(self._event_listeners):
            listener(event)

    def _emit_queue_update(self) -> None:
        self._emit(
            {
                "type": "queue_update",
                "steering": list(self._steering_messages),
                "follow_up": list(self._follow_up_messages),
            }
        )

    async def _handle_agent_event(self, event: Any, signal: Any = None) -> None:
        """Agent-event entry point.

        The TS chains every event onto ``_agentEventQueue`` so a slow handler
        cannot let the next event overtake it. Here the agent *awaits* its
        listeners in order, so the ordering the queue exists to guarantee is the
        await itself.
        """
        await self._process_agent_event(event)

    async def _process_agent_event(self, event: Any) -> None:
        event_type = _event_type(event)
        message = _event_message(event)

        # A queued message that is now being delivered leaves the queue *before*
        # the event goes out, so the UI never draws it as both pending and sent.
        if event_type == "message_start" and _message_role(message) == "user":
            message_text = self._get_user_message_text(message)
            if message_text:
                if message_text in self._steering_messages:
                    self._steering_messages.remove(message_text)
                    self._emit_queue_update()
                elif message_text in self._follow_up_messages:
                    self._follow_up_messages.remove(message_text)
                    self._emit_queue_update()

        # Notify all listeners.
        self._emit(event)

        # Handle session persistence.
        if event_type == "message_end":
            role = _message_role(message)
            if role == "custom":
                self.session_manager.append_custom_message_entry(
                    _field(message, "custom_type"),
                    _field(message, "content"),
                    _field(message, "display"),
                    _field(message, "details"),
                )
            elif role in ("user", "assistant", "toolResult"):
                self.session_manager.append_message(_to_entry(message))

            if role == "assistant":
                self._last_assistant_message = message

        if event_type == "agent_end" and self._last_assistant_message is not None:
            # Auto-retry and auto-compaction hang off this branch in the TS; both
            # controllers are unwired (see the module docstring), so all that is
            # left is dropping the reference.
            self._last_assistant_message = None

    def _get_user_message_text(self, message: Any) -> str:
        """Extract text content from a user message."""
        if _message_role(message) != "user":
            return ""
        content = _field(message, "content")
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        parts: list[str] = []
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
            )
            if block_type == "text":
                text = block.get("text", "") if isinstance(block, dict) else block.text
                parts.append(str(text))
        return "".join(parts)

    def _find_last_assistant_message(self) -> Any:
        """The last assistant message in agent state, including aborted ones."""
        for message in reversed(self.agent.state.messages):
            if _message_role(message) == "assistant":
                return message
        return None

    def dispose(self) -> None:
        """Remove all listeners and disconnect from the agent."""
        self._disconnect_from_agent()
        self._event_listeners = []

    def _disconnect_from_agent(self) -> None:
        if self._unsubscribe_agent is not None:
            self._unsubscribe_agent()
            self._unsubscribe_agent = None

    def _reconnect_to_agent(self) -> None:
        if self._unsubscribe_agent is not None:
            return
        self._unsubscribe_agent = self.agent.subscribe(self._handle_agent_event)

    # =====================================================================
    # Read-only state access
    # =====================================================================

    @property
    def state(self) -> Any:
        """Full agent state."""
        return self.agent.state

    @property
    def model(self) -> Any:
        """Current model (``None`` if none is selected)."""
        return self.agent.state.model

    @property
    def thinking_level(self) -> str:
        """Current thinking level."""
        return self.agent.state.thinking_level

    @property
    def is_streaming(self) -> bool:
        """Whether a turn is in flight."""
        return bool(self.agent.state.is_streaming)

    @property
    def system_prompt(self) -> str:
        return self.agent.state.system_prompt

    @property
    def messages(self) -> list[Any]:
        return self.agent.state.messages

    @property
    def retry_attempt(self) -> int:
        """Which auto-retry attempt is in flight; 0 when none is.

        Always 0 until the retry controller is wired (module docstring). The
        interactive mode reads it where the TS does, so an aborted turn says
        "Operation aborted" rather than "Aborted after 2 retry attempts".
        """
        return 0

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def session_file(self) -> str | None:
        return self.session_manager.get_session_file()

    @property
    def session_id(self) -> str:
        return self.session_manager.get_session_id()

    @property
    def session_name(self) -> str | None:
        return self.session_manager.get_session_name()

    @property
    def scoped_models(self) -> list[Any]:
        return list(self._scoped_models)

    def set_scoped_models(self, scoped_models: list[Any]) -> None:
        self._scoped_models = list(scoped_models)

    @property
    def model_registry(self) -> ModelRegistryLike | None:
        """The registry the session resolves auth against; ``None`` when unconfigured."""
        return self._model_registry

    def get_context_usage(self) -> ContextUsage | None:
        """How much of the model's context window this session is holding.

        ``None`` when there is no model or it declares no window — the footer
        draws a zeroed gauge for that, the same as the TS.
        """
        return compute_context_usage(
            model=self.model,
            session_manager=self.session_manager,
            messages=self.messages,
        )

    # =====================================================================
    # Prompting
    # =====================================================================

    async def prompt(self, text: str, options: PromptOptions | None = None) -> None:
        """Send ``text`` as a new turn, or queue it if one is already running."""
        options = options if options is not None else PromptOptions()
        preflight_result = options.preflight_result
        messages: list[Any] | None = None

        try:
            expanded_text = text
            images = options.images

            # If streaming, queue via steer() or follow_up() based on the option.
            if self.is_streaming:
                if options.streaming_behavior is None:
                    raise RuntimeError(
                        "Agent is already processing. Specify streaming_behavior "
                        "('steer' or 'followUp') to queue the message."
                    )
                if options.streaming_behavior == "followUp":
                    await self._queue_follow_up(expanded_text, images)
                else:
                    await self._queue_steer(expanded_text, images)
                if preflight_result is not None:
                    preflight_result(True)
                return

            # Any bash rows deferred during the last turn belong in the
            # transcript before this one starts.
            self._flush_pending_bash_messages()

            # Validate model.
            if self.model is None:
                raise RuntimeError(format_no_model_selected_message())

            registry = self._model_registry
            if registry is not None:
                if not registry.has_configured_auth(self.model):
                    if registry.is_using_oauth(self.model):
                        raise RuntimeError(
                            f'Authentication failed for "{self.model.provider}". '
                            "Credentials may have expired or network is unavailable. "
                            f"Run '/login {self.model.provider}' to re-authenticate."
                        )
                    raise RuntimeError(format_no_api_key_found_message(self.model.provider))
            elif getattr(self.model, "provider", "") == UNKNOWN_PROVIDER:
                # The agent starts on a sentinel model whose provider is
                # "unknown" (``DEFAULT_MODEL``), and with no registry to ask,
                # that sentinel is the only auth answer available: nothing has
                # been selected. It is also the case ``formatNoApiKeyFound``
                # already words for exactly this provider name.
                raise RuntimeError(format_no_api_key_found_message(UNKNOWN_PROVIDER))

            messages = [_user_message(expanded_text, images)]
        except Exception:
            if preflight_result is not None:
                preflight_result(False)
            raise

        if preflight_result is not None:
            preflight_result(True)
        await self.agent.prompt(messages)

    async def steer(self, text: str, images: list[Any] | None = None) -> None:
        """Queue a message to interrupt the agent after the current turn."""
        await self._queue_steer(text, images)

    async def follow_up(self, text: str, images: list[Any] | None = None) -> None:
        """Queue a message to be processed after the agent finishes."""
        await self._queue_follow_up(text, images)

    async def _queue_steer(self, text: str, images: list[Any] | None = None) -> None:
        self._steering_messages.append(text)
        self._emit_queue_update()
        self.agent.steer(_user_message(text, images))

    async def _queue_follow_up(self, text: str, images: list[Any] | None = None) -> None:
        self._follow_up_messages.append(text)
        self._emit_queue_update()
        self.agent.follow_up(_user_message(text, images))

    def clear_queue(self) -> dict[str, list[str]]:
        """Drop every queued message and tell the UI. Port of ``clearQueue``."""
        cleared = {
            "steering": list(self._steering_messages),
            "follow_up": list(self._follow_up_messages),
        }
        self._steering_messages = []
        self._follow_up_messages = []
        self.agent.clear_all_queues()
        self._emit_queue_update()
        return cleared

    @property
    def pending_message_count(self) -> int:
        return len(self._steering_messages) + len(self._follow_up_messages)

    def get_steering_messages(self) -> list[str]:
        return list(self._steering_messages)

    def get_follow_up_messages(self) -> list[str]:
        return list(self._follow_up_messages)

    # =====================================================================
    # Bash execution
    # =====================================================================

    async def execute_bash(
        self,
        command: str,
        on_chunk: Callable[[str], None] | None = None,
        *,
        exclude_from_context: bool = False,
        operations: Any = None,
    ) -> BashResult:
        """Run *command* and record what it printed in the transcript.

        This is the `!command` prompt mode's path, not the bash tool's. The
        shell prefix and shell path from settings apply — ``shopt -s
        expand_aliases`` and friends, so a user's aliases work here the way they
        do in their own terminal.
        """
        # Imported here for the reason `create_agent_session` imports the agent
        # here: this leaf needs no agent runtime, only the object it is handed.
        from cortex.agent.agent import AbortController
        from cortex.code.tools import create_local_bash_operations

        self._bash_abort_controller = AbortController()

        prefix = self.settings_manager.get_shell_command_prefix()
        shell_path = self.settings_manager.get_shell_path()
        resolved_command = f"{prefix}\n{command}" if prefix else command

        try:
            result = await execute_bash_with_operations(
                resolved_command,
                self.session_manager.get_cwd(),
                operations if operations is not None else create_local_bash_operations(shell_path),
                on_chunk=on_chunk,
                signal=self._bash_abort_controller.signal,
            )
            self.record_bash_result(command, result, exclude_from_context=exclude_from_context)
            return result
        finally:
            self._bash_abort_controller = None

    def record_bash_result(
        self,
        command: str,
        result: BashResult,
        *,
        exclude_from_context: bool = False,
    ) -> None:
        """Put a finished bash command into session history.

        Deferred while the agent is streaming: the transcript is mid-turn, and
        slipping a ``bashExecution`` row between a tool call and its result is
        how the next request to the provider becomes malformed.
        """
        bash_message: dict[str, Any] = {
            "role": "bashExecution",
            "command": command,
            "output": result.output,
            "exit_code": result.exit_code,
            "cancelled": result.cancelled,
            "truncated": result.truncated,
            "full_output_path": result.full_output_path,
            "timestamp": int(time.time() * 1000),
            "exclude_from_context": exclude_from_context,
        }

        if self.is_streaming:
            self._pending_bash_messages.append(bash_message)
            return

        self.agent.state.messages.append(bash_message)
        self.session_manager.append_message(bash_message)

    def _flush_pending_bash_messages(self) -> None:
        """Move deferred bash rows into the transcript, in order."""
        if not self._pending_bash_messages:
            return
        for bash_message in self._pending_bash_messages:
            self.agent.state.messages.append(bash_message)
            self.session_manager.append_message(bash_message)
        self._pending_bash_messages = []

    def abort_bash(self) -> None:
        """Cancel the running bash command, if there is one."""
        if self._bash_abort_controller is not None:
            self._bash_abort_controller.abort()

    @property
    def is_bash_running(self) -> bool:
        return self._bash_abort_controller is not None

    @property
    def has_pending_bash_messages(self) -> bool:
        return len(self._pending_bash_messages) > 0

    # =====================================================================
    # Aborting
    # =====================================================================

    async def abort(self) -> None:
        """Abort the current operation and wait for the agent to go idle."""
        self.agent.abort()
        result = self.agent.wait_for_idle()
        if inspect.isawaitable(result):
            await result


def _user_message(text: str, images: list[Any] | None = None) -> Any:
    """Build the user message a prompt or a queued message becomes."""
    from cortex.ai.types import TextContent, UserMessage

    content: list[Any] = [TextContent(text=text)]
    if images:
        content.extend(images)
    return UserMessage(content=content, timestamp=int(time.time() * 1000))


def _to_entry(message: Any) -> dict[str, Any]:
    """A message as the session file stores it.

    ``SessionManager`` is a JSONL store: it holds plain dicts, so a pydantic
    message has to be dumped on the way in.
    """
    if isinstance(message, dict):
        return message
    dump = getattr(message, "model_dump", None)
    if dump is not None:
        return dict(dump())
    return {"role": _message_role(message)}
