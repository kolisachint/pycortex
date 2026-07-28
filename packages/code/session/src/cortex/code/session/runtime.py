"""Assembling a session, and owning the one that is current.

Port of ``core/agent-session-runtime.ts`` plus the slice of
``agent-session-services.ts`` and ``sdk.ts::createAgentSession`` that has
something to assemble. The TS factory builds seven cwd-bound services —
``AuthStorage``, ``ModelRegistry``, ``DefaultResourceLoader``, the extension
runner, the tool registry, subagent pools, the embedding-search service — and
four of them belong to steps this port has not reached. What is left is still the
thing every mode starts from: settings, a session file, an :class:`Agent` over a
model, and the :class:`~cortex.code.session.agent_session.AgentSession` that
brings the three together.

**The model is the honest gap.** ``createAgentSession`` resolves one from the
session file, then the settings default, then the provider defaults, each check
going through the model registry (step 7.11). Without it, a session gets exactly
the model it was handed — which for a fresh ``pycortex`` is none, and
:meth:`AgentSession.prompt` then says so with the ``/login`` guidance instead of
pretending to send a request.

``AgentSessionRuntime``'s session-*replacement* half (``newSession``, ``fork``,
``switchSession``, ``/import``) is step 7.10's: it is the machinery behind
``--continue`` and the session selector, and there is nothing to switch between
until sessions are restored on screen.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from cortex.code.config import SettingsManager, get_agent_dir
from cortex.code.session.agent_session import AgentSession, AgentSessionConfig, ModelRegistryLike
from cortex.code.session.manager import SessionManager, get_default_session_dir

__all__ = [
    "AgentSessionRuntime",
    "AgentSessionRuntimeDiagnostic",
    "AgentSessionServices",
    "CreateAgentSessionResult",
    "create_agent_session",
]

DiagnosticType = Literal["info", "warning", "error"]


@dataclass
class AgentSessionRuntimeDiagnostic:
    """A non-fatal issue collected while creating services or a session.

    Creation returns these instead of printing or exiting: the app layer decides
    whether a warning reaches the screen and whether an error aborts startup.
    """

    type: DiagnosticType
    message: str


@dataclass
class AgentSessionServices:
    """Coherent cwd-bound services for one effective session cwd."""

    cwd: str
    agent_dir: str
    settings_manager: SettingsManager
    diagnostics: list[AgentSessionRuntimeDiagnostic] = field(default_factory=list)
    #: Step 7.11 fills this in; until then the key preflight cannot run.
    model_registry: ModelRegistryLike | None = None


@dataclass
class CreateAgentSessionResult:
    """What :func:`create_agent_session` hands back. Port of ``CreateAgentSessionResult``."""

    session: AgentSession
    services: AgentSessionServices
    diagnostics: list[AgentSessionRuntimeDiagnostic] = field(default_factory=list)
    #: Set when a session was restored with a different model than it was saved with.
    model_fallback_message: str | None = None


def create_agent_session(
    *,
    cwd: str | None = None,
    agent_dir: str | None = None,
    settings_manager: SettingsManager | None = None,
    session_manager: SessionManager | None = None,
    model: Any = None,
    model_registry: ModelRegistryLike | None = None,
    thinking_level: str = "off",
    system_prompt: str = "",
    tools: list[Any] | None = None,
    stream_fn: Any = None,
    scoped_models: list[Any] | None = None,
) -> CreateAgentSessionResult:
    """Build an :class:`AgentSession` and the services it sits on."""
    # Imported here so the leaf's import graph matches the TS's: the session
    # module itself needs no agent runtime, only the object it is handed.
    from cortex.agent.agent import Agent, AgentOptions

    effective_cwd = cwd if cwd is not None else os.getcwd()
    effective_agent_dir = agent_dir if agent_dir is not None else get_agent_dir()
    settings = (
        settings_manager
        if settings_manager is not None
        else SettingsManager.create(effective_cwd, effective_agent_dir)
    )
    sessions = (
        session_manager
        if session_manager is not None
        else SessionManager(
            effective_cwd, get_default_session_dir(effective_cwd, effective_agent_dir)
        )
    )

    services = AgentSessionServices(
        cwd=effective_cwd,
        agent_dir=effective_agent_dir,
        settings_manager=settings,
        model_registry=model_registry,
    )

    agent = Agent(
        AgentOptions(
            initial_state={
                "system_prompt": system_prompt,
                "model": model,
                "thinking_level": thinking_level,
                "tools": list(tools) if tools else [],
            },
            stream_fn=stream_fn,
            session_id=sessions.get_session_id(),
        )
    )

    session = AgentSession(
        AgentSessionConfig(
            agent=agent,
            session_manager=sessions,
            settings_manager=settings,
            cwd=effective_cwd,
            model_registry=model_registry,
            scoped_models=list(scoped_models) if scoped_models else [],
        )
    )

    return CreateAgentSessionResult(
        session=session,
        services=services,
        diagnostics=list(services.diagnostics),
    )


class AgentSessionRuntime:
    """Owns the current :class:`AgentSession` plus its cwd-bound services."""

    def __init__(
        self,
        session: AgentSession,
        services: AgentSessionServices,
        diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
        model_fallback_message: str | None = None,
    ) -> None:
        self._session = session
        self._services = services
        self._diagnostics = list(diagnostics) if diagnostics else []
        self._model_fallback_message = model_fallback_message
        self._rebind_session: Callable[[AgentSession], Any] | None = None
        self._before_session_invalidate: Callable[[], None] | None = None

    @property
    def session(self) -> AgentSession:
        return self._session

    @property
    def services(self) -> AgentSessionServices:
        return self._services

    @property
    def cwd(self) -> str:
        return self._services.cwd

    @property
    def diagnostics(self) -> list[AgentSessionRuntimeDiagnostic]:
        return list(self._diagnostics)

    @property
    def model_fallback_message(self) -> str | None:
        return self._model_fallback_message

    def set_rebind_session(self, rebind_session: Callable[[AgentSession], Any] | None) -> None:
        """Register what re-attaches a UI to a replacement session (step 7.10)."""
        self._rebind_session = rebind_session

    def set_before_session_invalidate(self, callback: Callable[[], None] | None) -> None:
        """Register host-owned teardown that must run before a session goes stale."""
        self._before_session_invalidate = callback

    def dispose(self) -> None:
        """Tear the current session down."""
        self._session.dispose()
