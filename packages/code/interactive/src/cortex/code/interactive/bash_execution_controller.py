"""Running a `!command` from the prompt, and putting it on screen.

Port of ``bash-execution-controller.ts``. Interactive mode owns the editor and
the containers; this owns what happens between "the user submitted ``!ls``" and
"the block says exit 0". It is behind a narrow
:class:`BashExecutionControllerDeps` for the reason the TS extracted it: it needs
five things from a 3,500-line class, and naming those five is what keeps it
testable on its own.

The one piece of policy here is *where* the block goes. A command typed while
the agent is streaming cannot be appended to the chat log — the transcript is
mid-turn and a bash row in the middle of it would sit between an assistant
message and its tool results. So it goes to the pending area instead and is
moved down by :meth:`BashExecutionController.flush_pending_bash_components` once
the turn ends.
"""

from __future__ import annotations

from typing import Any, Protocol

from cortex.code.interactive.components.bash_execution import BashExecutionComponent

__all__ = ["BashExecutionController", "BashExecutionControllerDeps"]


class BashExecutionControllerDeps(Protocol):
    """The slice of interactive mode a bash execution needs."""

    #: The active session. Read at call time — the session can be swapped.
    @property
    def session(self) -> Any: ...

    ui: Any
    #: Holds bash rows started while the agent is streaming.
    pending_messages_container: Any
    #: The chat transcript.
    chat_container: Any

    def show_error(self, error_message: str) -> None: ...


class BashExecutionController:
    """Runs `!commands` and renders them."""

    def __init__(self, deps: BashExecutionControllerDeps) -> None:
        self.deps = deps
        self.bash_component: BashExecutionComponent | None = None
        #: Shown in the pending area, moved to chat on the next submit.
        self.pending_bash_components: list[BashExecutionComponent] = []

    @property
    def session(self) -> Any:
        return self.deps.session

    def flush_pending_bash_components(self) -> None:
        """Move deferred bash blocks out of the pending area and into the chat."""
        for component in self.pending_bash_components:
            self.deps.pending_messages_container.remove_child(component)
            self.deps.chat_container.add_child(component)
        self.pending_bash_components = []

    def _place(self, component: BashExecutionComponent) -> None:
        """Put a new block where the session's state says it belongs."""
        if self.session.is_streaming:
            self.deps.pending_messages_container.add_child(component)
            self.pending_bash_components.append(component)
        else:
            self.deps.chat_container.add_child(component)

    async def handle_bash_command(self, command: str, exclude_from_context: bool = False) -> None:
        """Run *command*, streaming its output into a block in the log.

        The TS opens with ``extensionRunner.emitUserBash``, which lets an
        extension answer the command itself (a remote shell, a sandbox) and hand
        back a finished result. There is no extension runner on this session yet
        — 5.5 ported the extension types, not the session's runner — so what is
        left is the normal path: create the block, stream into it, record the
        result.
        """
        component = BashExecutionComponent(command, self.deps.ui, exclude_from_context)
        self.bash_component = component
        self._place(component)
        self.deps.ui.request_render()

        # The TS re-reads `this.bashComponent` at every point below, because a
        # second command started while this one is in flight replaces it and the
        # chunks must stop reaching the block that is no longer current. The
        # identity check is that guard, made explicit.
        def is_current() -> bool:
            return self.bash_component is component

        try:

            def on_chunk(chunk: str) -> None:
                if is_current():
                    component.append_output(chunk)
                    self.deps.ui.request_render()

            result = await self.session.execute_bash(
                command, on_chunk, exclude_from_context=exclude_from_context
            )

            if is_current():
                component.set_complete(
                    getattr(result, "exit_code", None),
                    bool(getattr(result, "cancelled", False)),
                    result if getattr(result, "truncated", False) else None,
                    getattr(result, "full_output_path", None),
                )
        except Exception as error:  # noqa: BLE001 - the TS catch, one for one
            if is_current():
                component.set_complete(None, False)
            self.deps.show_error(f"Bash command failed: {error or 'Unknown error'}")

        self.bash_component = None
        self.deps.ui.request_render()
