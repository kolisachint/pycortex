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

``AgentSessionRuntime``'s session-*replacement* half (:meth:`new_session`,
:meth:`fork`, :meth:`switch_session`, :meth:`import_from_jsonl`) landed with step
7.10: it is the machinery behind ``--continue``, ``/new``, ``/resume``, ``/fork``,
``/clone`` and ``/import``. Every one of them does the same three things —
tear the current session down, build a replacement over a different
:class:`SessionManager`, then hand the new session back to whatever was drawing
the old one — and the ordering matters: the *old* session is disposed before the
new one exists, so nothing can deliver an event into a screen that is about to be
rebuilt.

**The extension hooks around that sequence are not ported.** The TS asks
``session_before_switch`` and ``session_before_fork`` whether to proceed and
emits ``session_shutdown`` on the way out; nothing in this port has an extension
runner on a session yet. The ``cancelled`` flag every method returns is
therefore always ``False`` today — but it is in the signature, and every caller
checks it, because the day the runner is wired the answer stops being constant
and no call site should have to change.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from cortex.ai.types import SimpleStreamOptions
from cortex.code.config import SettingsManager, get_agent_dir, is_install_telemetry_enabled
from cortex.code.session.agent_session import AgentSession, AgentSessionConfig, ModelRegistryLike
from cortex.code.session.cwd import assert_session_cwd_exists
from cortex.code.session.manager import SessionManager, get_default_session_dir

__all__ = [
    "AgentSessionRuntime",
    "AgentSessionRuntimeDiagnostic",
    "AgentSessionServices",
    "CreateAgentSessionResult",
    "CreateAgentSessionRuntimeFactory",
    "SessionImportFileNotFoundError",
    "SessionReplacementResult",
    "create_agent_session",
    "create_agent_session_runtime",
    "get_attribution_headers",
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


@dataclass(frozen=True)
class SessionReplacementResult:
    """The answer every session-replacement method gives.

    ``cancelled`` is the ``session_before_switch``/``session_before_fork`` veto,
    which no extension can cast in this port yet (see the module docstring).
    ``selected_text`` is :meth:`AgentSessionRuntime.fork`'s alone: forking
    *before* a user message takes that message back out of the transcript, and
    the caller puts its text into the editor so it can be sent again.
    """

    cancelled: bool = False
    selected_text: str | None = None


#: Builds a whole runtime — session plus cwd-bound services — for one cwd and
#: session file. The runtime keeps the one it was created with and calls it again
#: for every replacement, which is what makes ``/new`` and ``/resume`` produce a
#: session assembled exactly like the one the process started on.
CreateAgentSessionRuntimeFactory = Callable[..., CreateAgentSessionResult]


class SessionImportFileNotFoundError(Exception):
    """``/import`` was given a path that is not there."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        super().__init__(f"File not found: {file_path}")


#: The headers an install that opted into telemetry adds to an OpenRouter
#: request, so the traffic is attributable to this tool on their dashboard.
_OPENROUTER_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/kolisachint/hoocode",
    "X-OpenRouter-Title": "hoocode",
    "X-OpenRouter-Categories": "cli-agent",
}


def get_attribution_headers(model: Any, settings_manager: SettingsManager) -> dict[str, str] | None:
    """Who is asking, when the provider is one that shows it. Port of ``getAttributionHeaders``.

    Only OpenRouter has anywhere to put this, and it is reached two ways — the
    built-in provider, and any custom provider a user has pointed at
    ``openrouter.ai`` — so both are matched.
    """
    if not is_install_telemetry_enabled(settings_manager):
        return None

    base_url = getattr(model, "base_url", "") or ""
    if getattr(model, "provider", None) == "openrouter" or "openrouter.ai" in base_url:
        return dict(_OPENROUTER_ATTRIBUTION_HEADERS)

    return None


def _registry_stream_fn(
    model_registry: ModelRegistryLike, settings_manager: SettingsManager
) -> Any:
    """The agent's stream function, with the user's credentials on it.

    Port of the ``streamFn`` ``createAgentSession`` wraps the Agent in. Without
    it every request leaves here with ``api_key=None`` and each provider falls
    back to :func:`~cortex.ai.env.get_env_api_key` — which is fine for a machine
    with ``ANTHROPIC_API_KEY`` set and fatal for one whose only credential is in
    ``auth.json``, because a subscription provider like ``github-copilot`` has no
    environment variable to fall back *to*. That is the "No API key for provider:
    github-copilot" a logged-in user was getting.

    It has to be a coroutine — the credentials are resolved with an ``await`` —
    and it returns the provider's stream rather than yielding through it, because
    the loop wants the :class:`~cortex.ai.stream.EventStream` itself: it iterates
    the events *and* awaits ``result()`` for the assembled message. That is the
    TS's ``async (…) => streamSimple(…)``, and its ``await streamFunction(…)``.

    Header precedence is the TS's spread order — attribution first, then what the
    registry resolved for the provider, then whatever the caller passed — so the
    most specific source wins. The retry budget is the mirror image: the caller's
    value wins and the settings only fill in what it left unset.
    """
    from cortex.ai.stream import stream_simple

    async def registry_stream_fn(model: Any, context: Any, options: Any = None) -> Any:
        auth = await model_registry.get_api_key_and_headers(model)
        if not auth.ok:
            raise RuntimeError(auth.error)

        retry = settings_manager.get_provider_retry_settings()
        attribution = get_attribution_headers(model, settings_manager)
        option_headers = getattr(options, "headers", None)

        headers: dict[str, str] | None = None
        if attribution or auth.headers or option_headers:
            headers = {**(attribution or {}), **(auth.headers or {}), **(option_headers or {})}

        base = options if isinstance(options, SimpleStreamOptions) else SimpleStreamOptions()
        resolved = base.model_copy(
            update={
                "api_key": auth.api_key,
                "timeout_ms": _first_set(base.timeout_ms, retry.timeout_ms),
                "max_retries": _first_set(base.max_retries, retry.max_retries),
                "max_retry_delay_ms": _first_set(base.max_retry_delay_ms, retry.max_retry_delay_ms),
                "headers": headers,
            }
        )

        return stream_simple(model, context, resolved)

    return registry_stream_fn


def _first_set(value: int | None, fallback: int | None) -> int | None:
    """The TS's ``??``: ``0`` is a value the caller meant, ``None`` is not."""
    return fallback if value is None else value


def _extract_user_message_text(content: Any) -> str:
    """The text of a user message, for the editor to be refilled with."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", "")
        if part_type != "text":
            continue
        text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


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

    # An explicit `stream_fn` still wins: the faux provider and the e2e sessions
    # substitute one precisely so no credential is resolved and no socket opens.
    effective_stream_fn = stream_fn
    if effective_stream_fn is None and model_registry is not None:
        effective_stream_fn = _registry_stream_fn(model_registry, settings)

    agent = Agent(
        AgentOptions(
            initial_state={
                "system_prompt": system_prompt,
                "model": model,
                "thinking_level": thinking_level,
                "tools": list(tools) if tools else [],
            },
            stream_fn=effective_stream_fn,
            session_id=sessions.get_session_id(),
        )
    )

    # A session file that already holds a conversation *is* the agent's context:
    # `sdk.ts` assigns the restored messages onto the agent after building it,
    # and without that a resumed session would look right on screen and arrive
    # at the provider with no history at all.
    existing_messages = sessions.build_session_context().messages
    if existing_messages:
        agent.state.messages = list(existing_messages)

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
        create_runtime: CreateAgentSessionRuntimeFactory | None = None,
        diagnostics: list[AgentSessionRuntimeDiagnostic] | None = None,
        model_fallback_message: str | None = None,
    ) -> None:
        self._session = session
        self._services = services
        self._create_runtime = (
            create_runtime if create_runtime is not None else _default_create_runtime
        )
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

    # ------------------------------------------------------------------
    # Session replacement
    # ------------------------------------------------------------------

    def _teardown_current(self) -> None:
        """Retire the session that is about to be replaced.

        The TS emits ``session_shutdown`` first and awaits its handlers; with no
        runner on a session here, what is left is the two lines that follow it —
        host teardown, then dispose — and their order, which is the part that
        matters: the host detaches while its context is still valid.
        """
        if self._before_session_invalidate is not None:
            self._before_session_invalidate()
        self._session.dispose()

    def _apply(self, result: CreateAgentSessionResult) -> None:
        """Adopt a freshly built runtime as the current one."""
        self._session = result.session
        self._services = result.services
        self._diagnostics = list(result.diagnostics)
        self._model_fallback_message = result.model_fallback_message

    async def _finish_session_replacement(self) -> None:
        """Hand the replacement session to whatever was drawing the old one."""
        if self._rebind_session is not None:
            outcome = self._rebind_session(self._session)
            if hasattr(outcome, "__await__"):
                await outcome

    async def switch_session(
        self, session_path: str, cwd_override: str | None = None
    ) -> SessionReplacementResult:
        """Replace the current session with the one stored at ``session_path``.

        The cwd check happens **before** the teardown, so a session file naming a
        directory that no longer exists leaves the current session running and
        the caller free to ask the user where to open it instead.
        """
        session_manager = SessionManager.open(session_path, None, cwd_override)
        assert_session_cwd_exists(
            session_manager.get_cwd(), session_manager.get_session_file(), self.cwd
        )
        self._teardown_current()
        self._apply(
            self._create_runtime(
                cwd=session_manager.get_cwd(),
                agent_dir=self.services.agent_dir,
                session_manager=session_manager,
            )
        )
        await self._finish_session_replacement()
        return SessionReplacementResult()

    async def new_session(
        self,
        parent_session: str | None = None,
        setup: Callable[[SessionManager], Any] | None = None,
    ) -> SessionReplacementResult:
        """Start an empty session in the same directory. What ``/new`` does.

        The replacement is persisted only if the session it replaces was. The TS
        calls ``SessionManager.create`` unconditionally, which under
        ``--no-session`` would start writing files the user asked not to have;
        :meth:`fork` already branches on the same question, so this is the shape
        the rest of the class is in rather than a new rule.
        """
        if not self._session.session_manager.is_persisted():
            session_manager = SessionManager.in_memory(self.cwd)
        else:
            session_dir = self._session.session_manager.get_session_dir()
            session_manager = SessionManager.create(self.cwd, session_dir)
        if parent_session:
            session_manager.new_session(parent_session=parent_session)

        self._teardown_current()
        self._apply(
            self._create_runtime(
                cwd=self.cwd,
                agent_dir=self.services.agent_dir,
                session_manager=session_manager,
            )
        )
        if setup is not None:
            outcome = setup(self._session.session_manager)
            if hasattr(outcome, "__await__"):
                await outcome
            # Whatever `setup` wrote into the file is context now, so the agent
            # is given the session's messages rather than the empty list it was
            # built with.
            self._session.agent.state.messages = (
                self._session.session_manager.build_session_context().messages
            )
        await self._finish_session_replacement()
        return SessionReplacementResult()

    async def fork(self, entry_id: str, position: str = "before") -> SessionReplacementResult:
        """Copy the branch up to ``entry_id`` into a new session and switch to it.

        Two positions, and the difference is one entry: ``at`` keeps the selected
        entry (``/clone``, forking at the leaf), ``before`` stops short of it and
        hands its text back so the editor can be refilled with the message you
        are about to ask differently (``/fork``).
        """
        selected_entry = self._session.session_manager.get_entry(entry_id)
        if not selected_entry:
            raise ValueError("Invalid entry ID for forking")

        selected_text: str | None = None
        if position == "at":
            target_leaf_id: str | None = selected_entry.get("id")
        else:
            message = selected_entry.get("message") or {}
            if selected_entry.get("type") != "message" or message.get("role") != "user":
                raise ValueError("Invalid entry ID for forking")
            target_leaf_id = selected_entry.get("parentId")
            selected_text = _extract_user_message_text(message.get("content", ""))

        if self._session.session_manager.is_persisted():
            current_session_file = self._session.session_file
            if not current_session_file:
                raise ValueError("Persisted session is missing a session file")
            session_dir = self._session.session_manager.get_session_dir()

            if not target_leaf_id:
                # Forking before the *first* user message: there is no branch to
                # copy, so what it means is an empty session that remembers where
                # it came from.
                session_manager = SessionManager.create(self.cwd, session_dir)
                session_manager.new_session(parent_session=current_session_file)
                cwd = self.cwd
            else:
                source_manager = SessionManager.open(current_session_file, session_dir)
                forked_session_path = source_manager.create_branched_session(target_leaf_id)
                if not forked_session_path:
                    raise ValueError("Failed to create forked session")
                session_manager = SessionManager.open(forked_session_path, session_dir)
                cwd = session_manager.get_cwd()
        else:
            # An unpersisted session has nowhere to copy *to*, so the fork happens
            # in place: the same manager, its leaf moved back.
            session_manager = self._session.session_manager
            if not target_leaf_id:
                session_manager.new_session(parent_session=self._session.session_file)
            else:
                session_manager.create_branched_session(target_leaf_id)
            cwd = self.cwd

        self._teardown_current()
        self._apply(
            self._create_runtime(
                cwd=cwd,
                agent_dir=self.services.agent_dir,
                session_manager=session_manager,
            )
        )
        await self._finish_session_replacement()
        return SessionReplacementResult(selected_text=selected_text)

    async def import_from_jsonl(
        self, input_path: str, cwd_override: str | None = None
    ) -> SessionReplacementResult:
        """Copy a session file into this project's session directory and open it.

        The copy is the point: an imported session becomes one of *this*
        project's sessions, so it shows up in ``/resume`` afterwards and further
        turns are appended where the rest live rather than to wherever the file
        came from.
        """
        resolved_path = os.path.abspath(input_path)
        if not os.path.exists(resolved_path):
            raise SessionImportFileNotFoundError(resolved_path)

        session_dir = self._session.session_manager.get_session_dir()
        os.makedirs(session_dir, exist_ok=True)

        destination_path = os.path.join(session_dir, os.path.basename(resolved_path))
        if os.path.abspath(destination_path) != resolved_path:
            shutil.copyfile(resolved_path, destination_path)

        session_manager = SessionManager.open(destination_path, session_dir, cwd_override)
        assert_session_cwd_exists(
            session_manager.get_cwd(), session_manager.get_session_file(), self.cwd
        )
        self._teardown_current()
        self._apply(
            self._create_runtime(
                cwd=session_manager.get_cwd(),
                agent_dir=self.services.agent_dir,
                session_manager=session_manager,
            )
        )
        await self._finish_session_replacement()
        return SessionReplacementResult()

    def dispose(self) -> None:
        """Tear the current session down."""
        if self._before_session_invalidate is not None:
            self._before_session_invalidate()
        self._session.dispose()


def _default_create_runtime(
    *,
    cwd: str,
    agent_dir: str,
    session_manager: SessionManager,
) -> CreateAgentSessionResult:
    """Build a session from nothing but a cwd and a file.

    The fallback for a runtime constructed without a factory. It has no model,
    no tools and no system prompt — which is the honest state until 7.11 resolves
    one — so a host that wants its replacements to look like its first session
    passes a factory that carries those over.
    """
    return create_agent_session(cwd=cwd, agent_dir=agent_dir, session_manager=session_manager)


def create_agent_session_runtime(
    create_runtime: CreateAgentSessionRuntimeFactory,
    *,
    cwd: str,
    agent_dir: str,
    session_manager: SessionManager,
) -> AgentSessionRuntime:
    """Build the first runtime, and keep the factory for every later replacement."""
    assert_session_cwd_exists(session_manager.get_cwd(), session_manager.get_session_file(), cwd)
    result = create_runtime(cwd=cwd, agent_dir=agent_dir, session_manager=session_manager)
    return AgentSessionRuntime(
        result.session,
        result.services,
        create_runtime,
        result.diagnostics,
        result.model_fallback_message,
    )
