"""Model selection, off the app class. Port of ``model-controller.ts``.

The TS pulled these five flows out of ``InteractiveMode`` behind a narrow
:class:`ModelControllerDeps`: the single-model picker, the cycling-scope picker,
the cycle keys, exact-match lookup for ``/model <name>``, and the footer's
provider count. This port keeps that shape and, as with
:class:`~cortex.code.interactive.command_executor.CommandExecutor`, the app
itself satisfies the protocol.

**The registry is the seam that is still open.** Every flow here asks the session
for models, and the session asks a
:class:`~cortex.code.session.ModelRegistryLike` — which step 7.11 supplies and
this port does not yet have. With none configured there is nothing to list, so
``/model`` opens on an empty list saying so and the cycle keys report only one
model. That is the same screen hoocode shows a user with no providers
configured, reached by a different route, and it is exactly the state
``/login`` (7.11) exists to change.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from cortex.code.interactive.components.model_selector import ModelSelectorComponent
from cortex.code.interactive.components.scoped_models_selector import (
    ModelsCallbacks,
    ModelsConfig,
    ScopedModelsSelectorComponent,
)
from cortex.code.session import find_exact_model_reference_match, resolve_model_scope
from cortex.tui.render import TUI

__all__ = [
    "ANTHROPIC_SUBSCRIPTION_AUTH_WARNING",
    "ModelController",
    "ModelControllerDeps",
    "SelectorFactory",
]

ANTHROPIC_SUBSCRIPTION_AUTH_WARNING = (
    "Anthropic subscription auth: billed per token as extra usage, not plan limits."
)

#: What :meth:`ModelControllerDeps.show_selector` is handed: a factory that takes
#: the "close me" callback and returns the overlay plus what to focus in it.
SelectorFactory = Callable[[Callable[[], None]], tuple[Any, Any]]


def _is_anthropic_subscription_auth_key(api_key: str | None) -> bool:
    return isinstance(api_key, str) and api_key.startswith("sk-ant-oat")


class ModelControllerDeps(Protocol):
    """The slice of the interactive mode the model flows need."""

    @property
    def ui(self) -> TUI: ...

    #: Read at call time — the session can be replaced under the controller.
    @property
    def session(self) -> Any: ...

    def show_selector(self, create: SelectorFactory, /) -> None: ...

    def show_status(self, message: str, /) -> None: ...

    def show_error(self, error_message: str, /) -> None: ...

    def show_warning(self, warning_message: str, /) -> None: ...

    def update_editor_border_color(self) -> None: ...

    def invalidate_footer(self) -> None: ...

    def set_available_provider_count(self, count: int, /) -> None: ...


class ModelController:
    """``/model``, ``/scoped-models`` and the cycle keys."""

    def __init__(self, deps: ModelControllerDeps) -> None:
        self._deps = deps
        self._anthropic_subscription_warning_shown = False

    @property
    def _session(self) -> Any:
        return self._deps.session

    async def find_exact_model_match(self, search_term: str) -> Any | None:
        models = await self._get_model_candidates()
        return find_exact_model_reference_match(search_term, models)

    async def _get_model_candidates(self) -> list[Any]:
        """The models in play: the scoped set if there is one, else all available."""
        if self._session.scoped_models:
            return [scoped.model for scoped in self._session.scoped_models]

        registry = self._session.model_registry
        if registry is None:
            return []
        registry.refresh()
        try:
            return list(await registry.get_available())
        except Exception:  # noqa: BLE001 - the TS's bare catch, one for one
            return []

    async def update_available_provider_count(self) -> None:
        """Recount the distinct providers behind the footer's badge."""
        models = await self._get_model_candidates()
        self._deps.set_available_provider_count(len({model.provider for model in models}))

    async def maybe_warn_about_anthropic_subscription_auth(self, model: Any = None) -> None:
        """Say once, per run, that subscription auth bills as extra usage.

        The warning is about money, so the three ways out of it are all checked
        before it is shown: the user turned it off, it has already been shown, or
        the model is not Anthropic's.
        """
        target_model = model if model is not None else self._session.model
        if self._session.settings_manager.get_warnings().anthropic_extra_usage is False:
            return
        if self._anthropic_subscription_warning_shown:
            return
        if not target_model or target_model.provider != "anthropic":
            return

        registry = self._session.model_registry
        if registry is None:
            return

        auth_storage = getattr(registry, "auth_storage", None)
        stored_credential = auth_storage.get("anthropic") if auth_storage is not None else None
        if stored_credential is not None and getattr(stored_credential, "type", None) == "oauth":
            self._anthropic_subscription_warning_shown = True
            self._deps.show_warning(ANTHROPIC_SUBSCRIPTION_AUTH_WARNING)
            return

        get_api_key = getattr(registry, "get_api_key_for_provider", None)
        if get_api_key is None:
            return
        try:
            api_key = await get_api_key(target_model.provider)
        except Exception:  # noqa: BLE001 - a warning-only check never fails loudly
            return
        if not _is_anthropic_subscription_auth_key(api_key):
            return
        self._anthropic_subscription_warning_shown = True
        self._deps.show_warning(ANTHROPIC_SUBSCRIPTION_AUTH_WARNING)

    async def cycle_model(self, direction: str) -> None:
        """Ctrl+P / Ctrl+Shift+P: step to the next model and say which."""
        try:
            result = await self._session.cycle_model(direction)
        except Exception as error:  # noqa: BLE001 - the TS catch, one for one
            self._deps.show_error(str(error))
            return

        if result is None:
            self._deps.show_status(
                "Only one model in scope"
                if self._session.scoped_models
                else "Only one model available"
            )
            return

        self._deps.invalidate_footer()
        self._deps.update_editor_border_color()
        thinking_str = (
            f" (thinking: {result.thinking_level})"
            if result.model.reasoning and result.thinking_level != "off"
            else ""
        )
        self._deps.show_status(f"Switched to {result.model.name or result.model.id}{thinking_str}")
        await self.maybe_warn_about_anthropic_subscription_auth(result.model)

    async def show_model_selector(self, initial_search_input: str | None = None) -> None:
        """``/model`` and Ctrl+L: the picker, over the models there are.

        The models are fetched here rather than inside the component (see that
        module's docstring), which is also what makes this method async where
        the TS's is not.
        """
        session = self._session
        available_models = await self._get_available_models()

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            def cancel() -> None:
                done()
                self._deps.ui.request_render()

            selector = ModelSelectorComponent(
                session.model,
                session.settings_manager,
                available_models,
                list(session.scoped_models),
                lambda model: self._select_model(model, done),
                cancel,
                initial_search_input,
            )
            return selector, selector

        self._deps.show_selector(create)

    async def _get_available_models(self) -> list[Any]:
        registry = self._session.model_registry
        if registry is None:
            return []
        registry.refresh()
        try:
            return list(await registry.get_available())
        except Exception:  # noqa: BLE001 - an unreachable registry is an empty list
            return []

    def _select_model(self, model: Any, done: Callable[[], None]) -> None:
        """Apply the picked model. Scheduled, because the switch is async.

        ``setModel`` is awaited in the TS inside the selector's callback; here
        the callback is synchronous (it is called from a keystroke), so the
        await goes onto the loop and the overlay closes when it lands — which
        is the TS's order too: ``done()`` runs after the switch, so a rejected
        model leaves the selector open on the error.
        """

        async def apply() -> None:
            try:
                await self._session.set_model(model)
                self._deps.invalidate_footer()
                self._deps.update_editor_border_color()
                done()
                self._deps.show_status(f"Model: {model.id}")
                await self.maybe_warn_about_anthropic_subscription_auth(model)
            except Exception as error:  # noqa: BLE001 - the TS catch, one for one
                done()
                self._deps.show_error(str(error))
            self._deps.ui.request_render()

        _schedule(apply())

    async def show_models_selector(self) -> None:
        """``/scoped-models``: which models Ctrl+P cycles, and in what order."""
        session = self._session
        all_models = await self._get_available_models()

        if not all_models:
            self._deps.show_status("No models available")
            return

        # Session scope first (set by `--models` or an earlier visit here), then
        # the persisted patterns; `None` means no filter at all.
        session_scoped_models = list(session.scoped_models)
        current_enabled_ids: list[str] | None = None

        if session_scoped_models:
            current_enabled_ids = [
                f"{scoped.model.provider}/{scoped.model.id}" for scoped in session_scoped_models
            ]
        else:
            patterns = session.settings_manager.get_enabled_models()
            if patterns:
                scoped_models = await resolve_model_scope(list(patterns), session.model_registry)
                current_enabled_ids = [
                    f"{scoped.model.provider}/{scoped.model.id}" for scoped in scoped_models
                ]

        async def update_session_models(enabled_ids: list[str] | None) -> None:
            # All enabled or none enabled is no filter at all — the empty scope
            # would otherwise leave Ctrl+P with nothing to cycle to.
            if enabled_ids and 0 < len(enabled_ids) < len(all_models):
                new_scoped_models = await resolve_model_scope(enabled_ids, session.model_registry)
                session.set_scoped_models(list(new_scoped_models))
            else:
                session.set_scoped_models([])
            await self.update_available_provider_count()
            self._deps.ui.request_render()

        def persist(enabled_ids: list[str] | None) -> None:
            new_patterns = (
                None
                if enabled_ids is None or len(enabled_ids) == len(all_models)
                else list(enabled_ids)
            )
            session.settings_manager.set_enabled_models(new_patterns)
            self._deps.show_status("Model selection saved to settings")

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            def cancel() -> None:
                done()
                self._deps.ui.request_render()

            selector = ScopedModelsSelectorComponent(
                ModelsConfig(all_models=all_models, enabled_model_ids=current_enabled_ids),
                ModelsCallbacks(
                    on_change=lambda enabled_ids: _schedule(update_session_models(enabled_ids)),
                    on_persist=persist,
                    on_cancel=cancel,
                ),
            )
            return selector, selector

        self._deps.show_selector(create)


def _schedule(coro: Any) -> None:
    """Run a coroutine from a keystroke handler, loop or no loop.

    A selector's callbacks are reached from a keystroke, which is synchronous
    everywhere in this port; the work behind them is not. With a loop running
    the coroutine is scheduled on it, and without one — a test driving the
    component directly — it is run to completion rather than dropped.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    _ = asyncio.ensure_future(coro)
