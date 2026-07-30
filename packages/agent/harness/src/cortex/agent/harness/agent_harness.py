# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false, reportRedeclaration=false
"""Agent harness implementation.

Mechanical port of hoocode's ``packages/agent/src/harness/agent-harness.ts``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from cortex.agent.agent import (  # pyright: ignore[reportMissingTypeStubs]
    Agent,
    AgentOptions,
    QueueMode,
)
from cortex.agent.types import (  # pyright: ignore[reportMissingTypeStubs]
    AgentContext,
    AgentMessage,
    AgentTool,
    ThinkingLevel,
)
from cortex.ai.types import ImageContent, Model, TextContent

from .prompt_templates import format_prompt_template_invocation
from .skills import format_skill_invocation
from .types import (
    AbortEvent,
    AbortResult,
    AfterProviderResponseEvent,
    AgentHarnessOptions,
    AgentHarnessOwnEvent,
    AgentHarnessPhase,
    AgentHarnessResources,
    AgentHarnessTurnState,
    BeforeAgentStartEvent,
    BeforeProviderRequestEvent,
    ContextEvent,
    ExecutionEnv,
    ModelSelectEvent,
    NavigateTreeResult,
    QueueUpdateEvent,
    ResourcesUpdateEvent,
    SavePointEvent,
    Session,
    SessionBeforeCompactEvent,
    SessionBeforeTreeEvent,
    SessionCompactEvent,
    SessionTreeEvent,
    SettledEvent,
    ThinkingLevelSelectEvent,
    ToolCallEvent,
    ToolResultEvent,
    TreePreparation,
)

__all__ = [
    "AgentHarness",
]


def _create_user_message(text: str, images: list[ImageContent] | None = None) -> Any:
    """Create a user message."""
    content: list[TextContent | ImageContent] = [TextContent(type="text", text=text)]
    if images:
        content.extend(images)
    return {"role": "user", "content": content, "timestamp": int(time.time() * 1000)}


class AgentHarness:
    """Agent harness that wraps an Agent with session management, tools, and resources."""

    def __init__(
        self,
        options: AgentHarnessOptions,
    ) -> None:
        # Create agent with initial state
        initial_state: dict[str, Any] = {}
        if options.model is not None:  # pyright: ignore[reportUnnecessaryComparison]
            initial_state["model"] = options.model
        if options.thinking_level is not None:
            initial_state["thinking_level"] = options.thinking_level
        if options.tools is not None:
            initial_state["tools"] = options.tools

        # `AgentOptions`, not the dict this passed: `Agent.__init__` reads
        # `options.initial_state`, so a harness could not be constructed at all.
        # Nothing noticed because nothing in this port builds one.
        self.agent = Agent(
            AgentOptions(
                initial_state=initial_state,
                steering_mode=cast(QueueMode, options.steering_mode or "all"),
                follow_up_mode=cast(QueueMode, options.follow_up_mode or "all"),
            )
        )
        self.env: ExecutionEnv = options.env
        self.session: Session = options.session
        self.model: Model[Any] = options.model
        self.thinking_level: ThinkingLevel = (
            options.thinking_level or self.agent.state.thinking_level
        )
        self.active_tool_names: list[str] = options.active_tool_names or [
            tool.name for tool in (options.tools or [])
        ]
        self.next_turn_queue: list[AgentMessage] = []
        self.phase: AgentHarnessPhase = "idle"
        self.steer_queue: list[Any] = []
        self.follow_up_queue: list[Any] = []
        self.pending_session_writes: list[dict[str, Any]] = []
        self.resources: AgentHarnessResources = options.resources or AgentHarnessResources()
        self.system_prompt_option: str | Callable[..., Any] | None = options.system_prompt
        self.get_api_key_and_headers: Callable[..., Any] | None = options.get_api_key_and_headers
        self.tools: dict[str, AgentTool[Any, Any]] = {}
        self._listeners: list[Callable[..., Any]] = []
        self._hooks: dict[str, set[Callable[..., Any]]] = {}

        # Register tools
        if options.tools:
            for tool in options.tools:
                self.tools[tool.name] = tool

        # Set agent state
        self.agent.state.model = self.model
        self.agent.state.thinking_level = self.thinking_level

        # Set up agent callbacks. The TS assigns these onto the agent
        # (`this.agent.getApiKey = …`), and so must this: `Agent.__init__` copies
        # its options onto itself and the loop config is built from *those*
        # attributes, so hanging the callbacks off `agent.options` after
        # construction registered nothing at all.
        self.agent.get_api_key = self._get_api_key
        self.agent.transform_context = self._transform_context
        self.agent.before_tool_call = self._before_tool_call
        self.agent.after_tool_call = self._after_tool_call
        self.agent.options.on_payload = self._on_payload
        self.agent.options.on_response = self._on_response
        self.agent.prepare_next_turn = self._prepare_next_turn

        # Subscribe to agent events
        self.agent.subscribe(self._handle_agent_event)

    async def _get_api_key(self, provider: str) -> str | None:
        """Get API key for the model provider."""
        if not self.get_api_key_and_headers or self.model.provider != provider:
            return None
        result = await self.get_api_key_and_headers(self.model)
        return result.get("apiKey") if result else None

    async def _transform_context(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """Transform context messages via hook."""
        result = await self._emit_hook(ContextEvent(messages=list(messages)))
        return result.messages if result and hasattr(result, "messages") else messages

    async def _before_tool_call(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Handle before tool call hook."""
        tool_call = context.get("tool_call", {})
        args = context.get("args", {})
        result = await self._emit_hook(
            ToolCallEvent(
                tool_call_id=tool_call.get("id", ""),
                tool_name=tool_call.get("name", ""),
                input=args if isinstance(args, dict) else {},
            )
        )
        if result and hasattr(result, "block"):
            return {"block": result.block, "reason": result.reason}
        return None

    async def _after_tool_call(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Handle after tool call hook."""
        tool_call = context.get("tool_call", {})
        args = context.get("args", {})
        result_data = context.get("result", {})
        is_error = context.get("is_error", False)
        patch = await self._emit_hook(
            ToolResultEvent(
                tool_call_id=tool_call.get("id", ""),
                tool_name=tool_call.get("name", ""),
                input=args if isinstance(args, dict) else {},
                content=result_data.get("content", []),
                details=result_data.get("details"),
                is_error=is_error,
            )
        )
        if patch and hasattr(patch, "content"):
            return {
                "content": patch.content,
                "details": patch.details,
                "is_error": patch.is_error,
                "terminate": patch.terminate,
            }
        return None

    async def _on_payload(self, payload: Any) -> Any:
        """Handle before provider request hook."""
        result = await self._emit_hook(BeforeProviderRequestEvent(payload=payload))
        return result.payload if result and hasattr(result, "payload") else payload

    async def _on_response(self, response: Any) -> None:
        """Handle after provider response."""
        headers = dict(response.get("headers", {})) if isinstance(response, dict) else {}
        await self._emit_own(
            AfterProviderResponseEvent(
                status=response.get("status", 0) if isinstance(response, dict) else 0,
                headers=headers,
            )
        )

    async def _prepare_next_turn(self, *_args: Any) -> dict[str, Any]:
        """Prepare for the next turn.

        The arguments are the loop context and the signal the agent passes; the
        TS's arrow ignores both, and so does this. What it hands back is read by
        the loop, which means an :class:`AgentContext` and snake_case keys — the
        camelCase dict this returned would have been assigned onto the loop as
        the next turn's context and died on the first attribute read.
        """
        await self._flush_pending_session_writes()
        turn_state = await self._create_turn_state()
        self._apply_turn_state(turn_state)
        return {
            "context": AgentContext(
                system_prompt=turn_state.system_prompt,
                messages=list(turn_state.messages),
                tools=list(turn_state.active_tools),
            ),
            "model": turn_state.model,
            "thinking_level": turn_state.thinking_level,
        }

    async def _handle_agent_event(self, event: Any, signal: Any = None) -> None:
        """Handle agent events."""
        await self._emit_any(event, signal)

        if isinstance(event, dict) or hasattr(event, "type"):
            event_type = (
                event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
            )

            if event_type == "message_start":
                message = (
                    event.get("message")
                    if isinstance(event, dict)
                    else getattr(event, "message", None)
                )
                if message and (isinstance(message, dict) and message.get("role") == "user"):
                    # Check steer queue
                    for i, msg in enumerate(self.steer_queue):
                        if msg is message:
                            self.steer_queue.pop(i)
                            await self._emit_queue_update()
                            return
                    # Check follow-up queue
                    for i, msg in enumerate(self.follow_up_queue):
                        if msg is message:
                            self.follow_up_queue.pop(i)
                            await self._emit_queue_update()
                            return

            elif event_type == "message_end":
                message = (
                    event.get("message")
                    if isinstance(event, dict)
                    else getattr(event, "message", None)
                )
                if message:
                    await self.session.append_message(message)

            elif event_type == "turn_end":
                had_pending_mutations = len(self.pending_session_writes) > 0
                await self._flush_pending_session_writes()
                await self._emit_own(SavePointEvent(had_pending_mutations=had_pending_mutations))

            elif event_type == "agent_end":
                await self._flush_pending_session_writes()
                self.phase = "idle"
                await self._emit_own(
                    SettledEvent(next_turn_count=len(self.next_turn_queue)), signal
                )

    async def _emit_own(self, event: AgentHarnessOwnEvent, signal: Any = None) -> None:
        """Emit an own event to all listeners."""
        for listener in self._listeners:
            try:
                await listener(event, signal)
            except Exception:
                pass

    async def _emit_any(self, event: Any, signal: Any = None) -> None:
        """Emit any event to all listeners."""
        for listener in self._listeners:
            try:
                await listener(event, signal)
            except Exception:
                pass

    async def _emit_hook(self, event: AgentHarnessOwnEvent) -> Any:
        """Emit a hook event and return the last result."""
        event_type = event.type if hasattr(event, "type") else None
        handlers = self._hooks.get(event_type, set()) if event_type else set()
        if not handlers:
            return None

        last_result = None
        for handler in handlers:
            try:
                result = await handler(event)
                if result is not None:
                    last_result = result
            except Exception:
                pass
        return last_result

    async def _emit_queue_update(self) -> None:
        """Emit a queue update event."""
        await self._emit_own(
            QueueUpdateEvent(
                steer=list(self.steer_queue),
                follow_up=list(self.follow_up_queue),
                next_turn=list(self.next_turn_queue),
            )
        )

    async def _create_turn_state(self) -> AgentHarnessTurnState:
        """Create the turn state."""
        context = await self.session.build_context()
        resources = self.get_resources()
        tools = list(self.tools.values())
        active_tools = [self.tools[name] for name in self.active_tool_names if name in self.tools]

        # Get system prompt
        system_prompt = "You are a helpful assistant."
        if isinstance(self.system_prompt_option, str):
            system_prompt = self.system_prompt_option
        elif callable(self.system_prompt_option):
            system_prompt = await self.system_prompt_option(
                env=self.env,
                session=self.session,
                model=self.model,
                thinking_level=self.thinking_level,
                active_tools=active_tools,
                resources=resources,
            )

        return AgentHarnessTurnState(
            messages=context.get("messages", []) if isinstance(context, dict) else [],
            resources=resources,
            system_prompt=system_prompt,
            model=self.model,
            thinking_level=self.thinking_level,
            tools=tools,
            active_tools=active_tools,
        )

    def _apply_turn_state(self, turn_state: AgentHarnessTurnState) -> None:
        """Apply turn state to agent."""
        self.agent.state.messages = turn_state.messages
        self.agent.state.system_prompt = turn_state.system_prompt
        self.agent.state.model = turn_state.model
        self.agent.state.thinking_level = turn_state.thinking_level
        self.agent.state.tools = turn_state.active_tools

    def _validate_tool_names(self, tool_names: list[str]) -> None:
        """Validate tool names exist."""
        missing = [name for name in tool_names if name not in self.tools]
        if missing:
            raise ValueError(f"Unknown tool(s): {', '.join(missing)}")

    async def _flush_pending_session_writes(self) -> None:
        """Flush pending session writes."""
        writes = self.pending_session_writes
        self.pending_session_writes = []

        for write in writes:
            write_type = write.get("type")
            if write_type == "message":
                await self.session.append_message(write["message"])
            elif write_type == "model_change":
                await self.session.append_model_change(write["provider"], write["modelId"])
            elif write_type == "thinking_level_change":
                await self.session.append_thinking_level_change(write["thinkingLevel"])
            elif write_type == "custom":
                await self.session.append_custom_entry(write["customType"], write.get("data"))
            elif write_type == "custom_message":
                await self.session.append_custom_message_entry(
                    write["customType"],
                    write["content"],
                    write["display"],
                    write.get("details"),
                )
            elif write_type == "label":
                await self.session.append_label(write["targetId"], write.get("label"))
            elif write_type == "session_info":
                await self.session.append_session_name(write.get("name", ""))

    async def _execute_turn(
        self,
        turn_state: AgentHarnessTurnState,
        text: str,
        images: list[ImageContent] | None = None,
    ) -> Any:
        """Execute a turn with the agent."""
        self._apply_turn_state(turn_state)
        before_length = len(self.agent.state.messages)
        messages: list[AgentMessage] = [_create_user_message(text, images)]

        if self.next_turn_queue:
            messages = [*self.next_turn_queue, messages[0]]
            self.next_turn_queue = []
            await self._emit_queue_update()

        # Emit before_agent_start hook
        before_result = await self._emit_hook(
            BeforeAgentStartEvent(
                prompt=text,
                images=images,
                system_prompt=turn_state.system_prompt,
                resources=turn_state.resources,
            )
        )
        if before_result and hasattr(before_result, "messages") and before_result.messages:
            messages = [*before_result.messages, *messages]
        if (
            before_result
            and hasattr(before_result, "system_prompt")
            and before_result.system_prompt
        ):
            self.agent.state.system_prompt = before_result.system_prompt

        try:
            await self.agent.prompt(messages)
        finally:
            await self._flush_pending_session_writes()

        # Find the assistant message
        new_messages = self.agent.state.messages[before_length:]
        response = None
        for msg in reversed(new_messages):
            if hasattr(msg, "role") and msg.role == "assistant":
                response = msg
                break

        if response is None:
            raise ValueError("AgentHarness prompt completed without an assistant message")

        return response

    async def prompt(
        self,
        text: str,
        images: list[ImageContent] | None = None,
    ) -> Any:
        """Send a prompt to the agent.

        Args:
            text: The prompt text.
            images: Optional images to include.

        Returns:
            The assistant's response.

        Raises:
            ValueError: If the harness is busy.
        """
        if self.phase != "idle":
            raise ValueError("AgentHarness is busy")
        self.phase = "turn"
        try:
            turn_state = await self._create_turn_state()
            return await self._execute_turn(turn_state, text, images)
        except Exception:
            self.phase = "idle"
            raise

    async def skill(
        self,
        name: str,
        additional_instructions: str | None = None,
    ) -> Any:
        """Invoke a skill by name.

        Args:
            name: The skill name.
            additional_instructions: Optional additional instructions.

        Returns:
            The assistant's response.

        Raises:
            ValueError: If the harness is busy or skill not found.
        """
        if self.phase != "idle":
            raise ValueError("AgentHarness is busy")
        self.phase = "turn"
        try:
            turn_state = await self._create_turn_state()
            skill = None
            for s in turn_state.resources.skills or []:
                if s.name == name:
                    skill = s
                    break
            if skill is None:
                raise ValueError(f"Unknown skill: {name}")
            return await self._execute_turn(
                turn_state, format_skill_invocation(skill, additional_instructions)
            )
        except Exception:
            self.phase = "idle"
            raise

    async def prompt_from_template(
        self,
        name: str,
        args: list[str] | None = None,
    ) -> Any:
        """Invoke a prompt template by name.

        Args:
            name: The template name.
            args: Optional positional arguments.

        Returns:
            The assistant's response.

        Raises:
            ValueError: If the harness is busy or template not found.
        """
        if self.phase != "idle":
            raise ValueError("AgentHarness is busy")
        self.phase = "turn"
        try:
            turn_state = await self._create_turn_state()
            template = None
            for t in turn_state.resources.prompt_templates or []:
                if t.name == name:
                    template = t
                    break
            if template is None:
                raise ValueError(f"Unknown prompt template: {name}")
            return await self._execute_turn(
                turn_state, format_prompt_template_invocation(template, args or [])
            )
        except Exception:
            self.phase = "idle"
            raise

    def steer(
        self,
        text: str,
        images: list[ImageContent] | None = None,
    ) -> None:
        """Steer the current turn.

        Args:
            text: The steering text.
            images: Optional images to include.

        Raises:
            ValueError: If the harness is idle.
        """
        if self.phase == "idle":
            raise ValueError("Cannot steer while idle")
        message = _create_user_message(text, images)
        self.steer_queue.append(message)
        self.agent.add_steering_message(message)
        # Fire and forget queue update
        import asyncio

        asyncio.ensure_future(self._emit_queue_update())

    def follow_up(
        self,
        text: str,
        images: list[ImageContent] | None = None,
    ) -> None:
        """Queue a follow-up message.

        Args:
            text: The follow-up text.
            images: Optional images to include.

        Raises:
            ValueError: If the harness is idle.
        """
        if self.phase == "idle":
            raise ValueError("Cannot follow up while idle")
        message = _create_user_message(text, images)
        self.follow_up_queue.append(message)
        self.agent.add_follow_up_message(message)
        # Fire and forget queue update
        import asyncio

        asyncio.ensure_future(self._emit_queue_update())

    def next_turn(
        self,
        text: str,
        images: list[ImageContent] | None = None,
    ) -> None:
        """Queue a message for the next turn.

        Args:
            text: The message text.
            images: Optional images to include.
        """
        self.next_turn_queue.append(_create_user_message(text, images))
        # Fire and forget queue update
        import asyncio

        asyncio.ensure_future(self._emit_queue_update())

    async def append_message(self, message: AgentMessage) -> None:
        """Append a message to the session.

        Args:
            message: The message to append.
        """
        if self.phase == "idle":
            await self.session.append_message(message)
        else:
            self.pending_session_writes.append({"type": "message", "message": message})

    async def compact(
        self,
        custom_instructions: str | None = None,
    ) -> dict[str, Any]:
        """Compact the conversation history.

        Args:
            custom_instructions: Optional custom instructions for compaction.

        Returns:
            Dictionary with summary and metadata.

        Raises:
            ValueError: If the harness is not idle or compaction fails.
        """
        if self.phase != "idle":
            raise ValueError("compact() requires idle harness")
        self.phase = "compaction"

        try:
            if not self.model:
                raise ValueError("No model set for compaction")

            auth = (
                await self.get_api_key_and_headers(self.model)
                if self.get_api_key_and_headers
                else None
            )
            if not auth:
                raise ValueError("No auth available for compaction")

            # Get branch entries
            branch_entries = await self.session.get_branch()

            # Prepare compaction
            from cortex.agent.compaction import DEFAULT_COMPACTION_SETTINGS, prepare_compaction

            preparation = prepare_compaction(branch_entries, DEFAULT_COMPACTION_SETTINGS)
            if not preparation:
                raise ValueError("Nothing to compact")

            # Emit hook
            hook_result = await self._emit_hook(
                SessionBeforeCompactEvent(
                    preparation=preparation,
                    branch_entries=branch_entries,
                    custom_instructions=custom_instructions,
                    signal=None,
                )
            )
            if hook_result and hasattr(hook_result, "cancel") and hook_result.cancel:
                self.phase = "idle"
                raise ValueError("Compaction cancelled")

            # Run compaction
            provided = (
                hook_result.compaction
                if hook_result and hasattr(hook_result, "compaction")
                else None
            )
            if provided:
                result = provided
            else:
                from cortex.agent.compaction import compact

                result = await compact(
                    preparation,
                    self.model,
                    auth.get("apiKey", ""),
                    auth.get("headers"),
                    custom_instructions,
                    None,
                    self.thinking_level,
                )

            # Append to session
            entry_id = await self.session.append_compaction(
                result.summary,
                result.first_kept_entry_id,
                result.tokens_before,
                result.details,
                provided is not None,
                result.tokens_after,
            )

            entry = await self.session.get_entry(entry_id)
            if entry and hasattr(entry, "type") and entry.type == "compaction":
                await self._emit_own(
                    SessionCompactEvent(compaction_entry=entry, from_hook=provided is not None)
                )

            self.phase = "idle"
            return {
                "summary": result.summary,
                "first_kept_entry_id": result.first_kept_entry_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "details": result.details,
            }
        except Exception:
            self.phase = "idle"
            raise

    async def navigate_tree(
        self,
        target_id: str,
        options: dict[str, Any] | None = None,
    ) -> NavigateTreeResult:
        """Navigate to a different point in the session tree.

        Args:
            target_id: The target entry ID.
            options: Optional options for navigation.

        Returns:
            Navigation result.
        """
        if self.phase != "idle":
            raise ValueError("navigateTree() requires idle harness")
        self.phase = "branch_summary"

        try:
            old_leaf_id = await self.session.get_leaf_id()
            if old_leaf_id == target_id:
                self.phase = "idle"
                return NavigateTreeResult(cancelled=False)

            target_entry = await self.session.get_entry(target_id)
            if not target_entry:
                raise ValueError(f"Entry {target_id} not found")

            # Collect entries for branch summary
            from cortex.agent.compaction import collect_entries_for_branch_summary

            result = await collect_entries_for_branch_summary(self.session, old_leaf_id, target_id)
            entries = result.entries
            common_ancestor_id = result.common_ancestor_id

            preparation = TreePreparation(
                target_id=target_id,
                old_leaf_id=old_leaf_id,
                common_ancestor_id=common_ancestor_id,
                entries_to_summarize=entries,
                user_wants_summary=(options or {}).get("summarize", False),
                custom_instructions=(options or {}).get("custom_instructions"),
                replace_instructions=(options or {}).get("replace_instructions"),
                label=(options or {}).get("label"),
            )

            # Emit hook
            hook_result = await self._emit_hook(
                SessionBeforeTreeEvent(preparation=preparation, signal=None)
            )
            if hook_result and hasattr(hook_result, "cancel") and hook_result.cancel:
                self.phase = "idle"
                return NavigateTreeResult(cancelled=True)

            summary_entry = None
            summary_text = (
                hook_result.summary.get("summary")
                if hook_result and hasattr(hook_result, "summary") and hook_result.summary
                else None
            )
            summary_details = (
                hook_result.summary.get("details")
                if hook_result and hasattr(hook_result, "summary") and hook_result.summary
                else None
            )

            if not summary_text and (options or {}).get("summarize") and entries:
                if not self.model:
                    raise ValueError("No model set for branch summary")
                auth = (
                    await self.get_api_key_and_headers(self.model)
                    if self.get_api_key_and_headers
                    else None
                )
                if not auth:
                    raise ValueError("No auth available for branch summary")

                from cortex.agent.compaction import generate_branch_summary

                branch_summary = await generate_branch_summary(
                    entries,
                    {
                        "model": self.model,
                        "apiKey": auth.get("apiKey", ""),
                        "headers": auth.get("headers"),
                        "signal": None,
                        "customInstructions": (
                            hook_result.custom_instructions
                            if hook_result and hasattr(hook_result, "custom_instructions")
                            else (options or {}).get("custom_instructions")
                        ),
                        "replaceInstructions": (
                            hook_result.replace_instructions
                            if hook_result and hasattr(hook_result, "replace_instructions")
                            else (options or {}).get("replace_instructions")
                        ),
                    },
                )

                if branch_summary.get("aborted"):
                    self.phase = "idle"
                    return NavigateTreeResult(cancelled=True)
                if branch_summary.get("error"):
                    raise ValueError(branch_summary["error"])

                summary_text = branch_summary.get("summary")
                summary_details = {
                    "readFiles": branch_summary.get("readFiles", []),
                    "modifiedFiles": branch_summary.get("modifiedFiles", []),
                }

            # Determine new leaf ID and editor text
            editor_text = None
            new_leaf_id = None

            if hasattr(target_entry, "type") and target_entry.type == "message":
                message = getattr(target_entry, "message", None)
                if message and hasattr(message, "role") and message.role == "user":
                    new_leaf_id = target_entry.parent_id
                    content = message.content
                    if isinstance(content, str):
                        editor_text = content
                    elif isinstance(content, list):
                        editor_text = "".join(
                            c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
                            for c in content
                            if (isinstance(c, dict) and c.get("type") == "text")
                            or (hasattr(c, "type") and c.type == "text")
                        )
            elif hasattr(target_entry, "type") and target_entry.type == "custom_message":
                new_leaf_id = target_entry.parent_id
                content = target_entry.content
                if isinstance(content, str):
                    editor_text = content
                elif isinstance(content, list):
                    editor_text = "".join(
                        c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
                        for c in content
                        if (isinstance(c, dict) and c.get("type") == "text")
                        or (hasattr(c, "type") and c.type == "text")
                    )
            else:
                new_leaf_id = target_id

            # Move to new position
            summary_id = await self.session.move_to(
                new_leaf_id,
                {
                    "summary": summary_text,
                    "details": summary_details,
                    "fromHook": hook_result
                    and hasattr(hook_result, "summary")
                    and hook_result.summary is not None,
                }
                if summary_text
                else None,
            )

            if summary_id:
                summary_entry = await self.session.get_entry(summary_id)

            await self._emit_own(
                SessionTreeEvent(
                    new_leaf_id=await self.session.get_leaf_id(),
                    old_leaf_id=old_leaf_id,
                    summary_entry=summary_entry,
                    from_hook=hook_result
                    and hasattr(hook_result, "summary")
                    and hook_result.summary is not None,
                )
            )

            self.phase = "idle"
            return NavigateTreeResult(
                cancelled=False,
                editor_text=editor_text,
                summary_entry=summary_entry,
            )
        except Exception:
            self.phase = "idle"
            raise

    async def set_model(self, model: Model[Any]) -> None:
        """Set the model.

        Args:
            model: The new model.
        """
        previous_model = self.model
        self.model = model
        if self.phase == "idle":
            self.agent.state.model = model
            await self.session.append_model_change(model.provider, model.id)
        else:
            self.pending_session_writes.append(
                {
                    "type": "model_change",
                    "provider": model.provider,
                    "modelId": model.id,
                }
            )
        await self._emit_own(
            ModelSelectEvent(model=model, previous_model=previous_model, source="set")
        )

    async def set_thinking_level(self, level: ThinkingLevel) -> None:
        """Set the thinking level.

        Args:
            level: The new thinking level.
        """
        previous_level = self.thinking_level
        self.thinking_level = level
        if self.phase == "idle":
            self.agent.state.thinking_level = level
            await self.session.append_thinking_level_change(level)
        else:
            self.pending_session_writes.append(
                {
                    "type": "thinking_level_change",
                    "thinkingLevel": level,
                }
            )
        await self._emit_own(ThinkingLevelSelectEvent(level=level, previous_level=previous_level))

    async def set_active_tools(self, tool_names: list[str]) -> None:
        """Set the active tool names.

        Args:
            tool_names: List of tool names to activate.
        """
        self._validate_tool_names(tool_names)
        self.active_tool_names = list(tool_names)
        if self.phase == "idle":
            self.agent.state.tools = [
                self.tools[name] for name in self.active_tool_names if name in self.tools
            ]

    @property
    def steering_mode(self) -> QueueMode:
        """Get the steering mode."""
        return self.agent.options.steering_mode

    @steering_mode.setter
    def steering_mode(self, mode: QueueMode) -> None:
        """Set the steering mode."""
        self.agent.options.steering_mode = mode

    @property
    def follow_up_mode(self) -> QueueMode:
        """Get the follow-up mode."""
        return self.agent.options.follow_up_mode

    @follow_up_mode.setter
    def follow_up_mode(self, mode: QueueMode) -> None:
        """Set the follow-up mode."""
        self.agent.options.follow_up_mode = mode

    def get_resources(self) -> AgentHarnessResources:
        """Get the current resources."""
        return AgentHarnessResources(
            skills=list(self.resources.skills or []),
            prompt_templates=list(self.resources.prompt_templates or []),
        )

    async def set_resources(self, resources: AgentHarnessResources) -> None:
        """Set the resources.

        Args:
            resources: The new resources.
        """
        previous_resources = self.get_resources()
        self.resources = AgentHarnessResources(
            skills=list(resources.skills or []),
            prompt_templates=list(resources.prompt_templates or []),
        )
        await self._emit_own(
            ResourcesUpdateEvent(
                resources=self.get_resources(),
                previous_resources=previous_resources,
            )
        )

    async def set_tools(
        self,
        tools: list[AgentTool[Any, Any]],
        active_tool_names: list[str] | None = None,
    ) -> None:
        """Set the tools.

        Args:
            tools: List of tools.
            active_tool_names: Optional list of active tool names.
        """
        self.tools = {tool.name: tool for tool in tools}
        if active_tool_names:
            self._validate_tool_names(active_tool_names)
            self.active_tool_names = list(active_tool_names)
        else:
            self._validate_tool_names(self.active_tool_names)

        if self.phase == "idle":
            self.agent.state.tools = [
                self.tools[name] for name in self.active_tool_names if name in self.tools
            ]

    async def abort(self) -> AbortResult:
        """Abort the current turn and clear queues.

        Returns:
            The cleared steer and follow-up messages.
        """
        cleared_steer = list(self.steer_queue)
        cleared_follow_up = list(self.follow_up_queue)
        self.steer_queue = []
        self.follow_up_queue = []
        self.agent.clear_messages()
        await self._emit_queue_update()
        self.agent.reset()
        await self._emit_own(
            AbortEvent(cleared_steer=cleared_steer, cleared_follow_up=cleared_follow_up)
        )
        return AbortResult(cleared_steer=cleared_steer, cleared_follow_up=cleared_follow_up)

    async def wait_for_idle(self) -> None:
        """Wait for the harness to become idle."""
        while self.phase != "idle":
            import asyncio

            await asyncio.sleep(0.01)

    def subscribe(self, listener: Callable[..., Any]) -> Callable[[], None]:
        """Subscribe to harness events.

        Args:
            listener: Callback function for events.

        Returns:
            Unsubscribe function.
        """
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def on(
        self,
        event_type: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        """Register a hook handler for an event type.

        Args:
            event_type: The event type to handle.
            handler: The handler function.

        Returns:
            Unregister function.
        """
        if event_type not in self._hooks:
            self._hooks[event_type] = set()
        self._hooks[event_type].add(handler)

        def unregister() -> None:
            if event_type in self._hooks:
                self._hooks[event_type].discard(handler)

        return unregister
