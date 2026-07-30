"""``/login`` and ``/logout``, off the app class. Port of ``login-controller.ts``.

The flow ``/login`` opens is three screens deep, and each level exists for a
reason: *how* do you want to authenticate (subscription or API key), *which*
provider, and then the provider's own dialog. Asking the auth type first is what
keeps the provider list short — nearly every provider takes an API key, only
three offer a subscription.

**What happens after a successful login is the half that is easy to miss.**
:meth:`LoginController._complete_provider_authentication` refreshes the registry,
and *if the session is still on the ``unknown`` sentinel model* it also selects
the new provider's default — because a user whose first action was ``/login`` has
no model yet, and leaving them on the sentinel means the next Enter says "no API
key found" immediately after they supplied one. Every way that selection can fail
gets its own message naming ``/model``, rather than one generic apology.

Like :class:`~cortex.code.interactive.model_controller.ModelController`, this
takes a narrow deps protocol that the app itself satisfies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal, Protocol

from cortex.ai.models import get_providers
from cortex.code.config import (
    BUILT_IN_PROVIDER_DISPLAY_NAMES,
    ApiKeyCredential,
    get_auth_path,
)
from cortex.code.interactive.components.extension_selector import ExtensionSelectorComponent
from cortex.code.interactive.components.login_dialog import (
    LOGIN_CANCELLED,
    LoginDialogComponent,
)
from cortex.code.interactive.components.oauth_selector import (
    AuthSelectorProvider,
    OAuthSelectorComponent,
)
from cortex.code.session import DEFAULT_MODEL_PER_PROVIDER
from cortex.tui.render import TUI, Container

__all__ = [
    "LoginController",
    "LoginControllerDeps",
    "is_api_key_login_provider",
]

AuthType = Literal["oauth", "api_key"]

SUBSCRIPTION_LABEL = "Use a subscription"
API_KEY_LABEL = "Use an API key"

#: `/logout` only removes what `/login` wrote, and says so — otherwise a user
#: whose key comes from the environment would read "no stored credentials" as
#: "you are not logged in".
NO_STORED_CREDENTIALS_MESSAGE = (
    "No stored credentials to remove. /logout only removes credentials saved by "
    "/login; environment variables and models.json config are unchanged."
)

#: A session can be built without a registry — ``_create_unpersisted_session``
#: does, and so does every test that brings its own session. There is nothing for
#: the auth flows to read or write in that state, so they say so rather than
#: raising on ``None``.
NO_REGISTRY_MESSAGE = "Provider configuration is unavailable in this session."


def is_api_key_login_provider(
    provider_id: str,
    oauth_provider_ids: frozenset[str] | set[str],
    built_in_provider_ids: frozenset[str] | set[str] | None = None,
) -> bool:
    """Whether this provider should be offered an API-key login.

    Three rules, in the TS's order, and the order is what makes it work:

    1. a provider in :data:`BUILT_IN_PROVIDER_DISPLAY_NAMES` takes a key — even
       one that *also* offers OAuth, because Anthropic accepts both;
    2. any other built-in *model* provider does not — these are the ones reached
       only by subscription (``openai-codex``, ``github-copilot``);
    3. anything else — a provider from ``models.json`` or an extension — takes a
       key unless it registered an OAuth flow.
    """
    if provider_id in BUILT_IN_PROVIDER_DISPLAY_NAMES:
        return True
    built_in = (
        built_in_provider_ids if built_in_provider_ids is not None else _built_in_model_providers()
    )
    if provider_id in built_in:
        return False
    return provider_id not in oauth_provider_ids


def _built_in_model_providers() -> set[str]:
    """Read at call time, not at import: an extension can add a provider."""
    return set(get_providers())


def _is_unknown_model(model: Any) -> bool:
    """Whether the session is still on the agent's ``DEFAULT_MODEL`` sentinel.

    That sentinel is what a session with no resolvable model runs on, so it is
    also the signal that this login is the user's first — and the only case where
    logging in should pick a model for them.
    """
    return bool(
        model is not None
        and getattr(model, "provider", None) == "unknown"
        and getattr(model, "id", None) == "unknown"
        and getattr(model, "api", None) == "unknown"
    )


class LoginControllerDeps(Protocol):
    """The slice of the interactive mode the login flows need."""

    @property
    def ui(self) -> TUI: ...

    #: Read at call time — the session can be replaced under the controller.
    @property
    def session(self) -> Any: ...

    #: Read at call time — the editor can be replaced under the controller.
    def get_editor(self) -> Any: ...

    @property
    def editor_container(self) -> Container: ...

    def show_selector(self, create: Callable[[Callable[[], None]], tuple[Any, Any]], /) -> None: ...

    def show_status(self, message: str, /) -> None: ...

    def show_error(self, error_message: str, /) -> None: ...

    async def update_available_provider_count(self) -> None: ...

    def update_editor_border_color(self) -> None: ...

    def invalidate_footer(self) -> None: ...

    async def maybe_warn_about_anthropic_subscription_auth(self, model: Any = None) -> None: ...


class LoginController:
    """``/login`` and ``/logout``."""

    def __init__(self, deps: LoginControllerDeps) -> None:
        self._deps = deps

    @property
    def _session(self) -> Any:
        return self._deps.session

    @property
    def _registry(self) -> Any:
        return self._session.model_registry

    def _restore_editor(self) -> None:
        """Put the editor back where a dialog was, and give it the keyboard."""
        deps = self._deps
        deps.editor_container.clear()
        deps.editor_container.add_child(deps.get_editor())
        deps.ui.set_focus(deps.get_editor())
        deps.ui.request_render()

    # -- building the lists ------------------------------------------------

    def _get_login_provider_options(
        self, auth_type: AuthType | None = None
    ) -> list[AuthSelectorProvider]:
        """Every provider that can be logged into, name-sorted.

        OAuth providers come from the registry's storage; API-key providers are
        derived from the *models* there are, so a provider only appears if there
        is something to point a key at.
        """
        auth_storage = self._registry.auth_storage
        oauth_providers = auth_storage.get_oauth_providers()
        oauth_provider_ids = {provider.id for provider in oauth_providers}
        options: list[AuthSelectorProvider] = [
            AuthSelectorProvider(id=provider.id, name=provider.name, auth_type="oauth")
            for provider in oauth_providers
        ]

        model_providers = {model.provider for model in self._registry.get_all()}
        for provider_id in model_providers:
            if not is_api_key_login_provider(provider_id, oauth_provider_ids):
                continue
            options.append(
                AuthSelectorProvider(
                    id=provider_id,
                    name=self._registry.get_provider_display_name(provider_id),
                    auth_type="api_key",
                )
            )

        filtered = (
            [option for option in options if option.auth_type == auth_type]
            if auth_type
            else options
        )
        return sorted(filtered, key=lambda option: option.name)

    def _get_logout_provider_options(self) -> list[AuthSelectorProvider]:
        """Only what is actually stored — the only thing ``/logout`` can remove."""
        auth_storage = self._registry.auth_storage
        options: list[AuthSelectorProvider] = []

        for provider_id in auth_storage.list():
            credential = auth_storage.get(provider_id)
            if credential is None:
                continue
            options.append(
                AuthSelectorProvider(
                    id=provider_id,
                    name=self._registry.get_provider_display_name(provider_id),
                    auth_type="oauth" if credential.type == "oauth" else "api_key",
                )
            )

        return sorted(options, key=lambda option: option.name)

    # -- the three screens -------------------------------------------------

    def _show_login_auth_type_selector(self) -> None:
        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            def select(option: str) -> None:
                done()
                auth_type: AuthType = "oauth" if option == SUBSCRIPTION_LABEL else "api_key"
                self._show_login_provider_selector(auth_type)

            def cancel() -> None:
                done()
                self._deps.ui.request_render()

            selector = ExtensionSelectorComponent(
                "Select authentication method:",
                [SUBSCRIPTION_LABEL, API_KEY_LABEL],
                select,
                cancel,
            )
            return selector, selector

        self._deps.show_selector(create)

    def _show_login_provider_selector(self, auth_type: AuthType) -> None:
        provider_options = self._get_login_provider_options(auth_type)
        if not provider_options:
            self._deps.show_status(
                "No subscription providers available."
                if auth_type == "oauth"
                else "No API key providers available."
            )
            return

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            def select(provider_id: str) -> None:
                done()

                provider_option = next((p for p in provider_options if p.id == provider_id), None)
                if provider_option is None:
                    return

                if provider_option.auth_type == "oauth":
                    _schedule(self._show_login_dialog(provider_option.id, provider_option.name))
                else:
                    _schedule(
                        self._show_api_key_login_dialog(provider_option.id, provider_option.name)
                    )

            def cancel() -> None:
                # Escape goes *back* a screen rather than closing, which is the
                # TS's behaviour and the reason this list is reachable at all.
                done()
                self._show_login_auth_type_selector()

            selector = OAuthSelectorComponent(
                "login",
                self._registry.auth_storage,
                provider_options,
                select,
                cancel,
                self._registry.get_provider_auth_status,
            )
            return selector, selector

        self._deps.show_selector(create)

    async def show_oauth_selector(self, mode: Literal["login", "logout"]) -> None:
        """``/login`` and ``/logout``: the entry point for both."""
        if self._registry is None:
            self._deps.show_status(NO_REGISTRY_MESSAGE)
            return

        if mode == "login":
            self._show_login_auth_type_selector()
            return

        provider_options = self._get_logout_provider_options()
        if not provider_options:
            self._deps.show_status(NO_STORED_CREDENTIALS_MESSAGE)
            return

        def create(done: Callable[[], None]) -> tuple[Any, Any]:
            def select(provider_id: str) -> None:
                done()

                provider_option = next((p for p in provider_options if p.id == provider_id), None)
                if provider_option is None:
                    return

                _schedule(self._logout(provider_option))

            def cancel() -> None:
                done()
                self._deps.ui.request_render()

            selector = OAuthSelectorComponent(
                mode,
                self._registry.auth_storage,
                provider_options,
                select,
                cancel,
            )
            return selector, selector

        self._deps.show_selector(create)

    async def _logout(self, provider_option: AuthSelectorProvider) -> None:
        try:
            self._registry.auth_storage.logout(provider_option.id)
            self._registry.refresh()
            await self._deps.update_available_provider_count()
            message = (
                f"Logged out of {provider_option.name}"
                if provider_option.auth_type == "oauth"
                else (
                    f"Removed stored API key for {provider_option.name}. "
                    "Environment variables and models.json config are unchanged."
                )
            )
            self._deps.show_status(message)
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            self._deps.show_error(f"Logout failed: {error}")
        self._deps.ui.request_render()

    # -- after a successful login -----------------------------------------

    async def _complete_provider_authentication(
        self,
        provider_id: str,
        provider_name: str,
        auth_type: AuthType,
        previous_model: Any,
    ) -> None:
        """Refresh, maybe pick a model, and say what happened."""
        self._registry.refresh()

        action_label = (
            f"Logged in to {provider_name}"
            if auth_type == "oauth"
            else f"Saved API key for {provider_name}"
        )

        selected_model: Any = None
        selection_error: str | None = None
        if _is_unknown_model(previous_model):
            available_models = self._registry.get_available_sync()
            provider_models = [m for m in available_models if m.provider == provider_id]
            default_model_id = DEFAULT_MODEL_PER_PROVIDER.get(provider_id)
            if default_model_id is None:
                selection_error = (
                    f"{action_label}, but no default model is configured for provider "
                    f'"{provider_id}". Use /model to select a model.'
                )
            elif not provider_models:
                selection_error = (
                    f"{action_label}, but no models are available for that provider. "
                    "Use /model to select a model."
                )
            else:
                selected_model = next(
                    (m for m in provider_models if m.id == default_model_id), None
                )
                if selected_model is None:
                    selection_error = (
                        f'{action_label}, but its default model "{default_model_id}" is '
                        "not available. Use /model to select a model."
                    )
                else:
                    try:
                        await self._session.set_model(selected_model)
                    except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
                        selected_model = None
                        selection_error = (
                            f"{action_label}, but selecting its default model failed: "
                            f"{error}. Use /model to select a model."
                        )

        await self._deps.update_available_provider_count()
        self._deps.invalidate_footer()
        self._deps.update_editor_border_color()
        if selected_model is not None:
            self._deps.show_status(
                f"{action_label}. Selected {selected_model.id}. "
                f"Credentials saved to {get_auth_path()}"
            )
            await self._deps.maybe_warn_about_anthropic_subscription_auth(selected_model)
        else:
            self._deps.show_status(f"{action_label}. Credentials saved to {get_auth_path()}")
            if selection_error:
                self._deps.show_error(selection_error)
            else:
                await self._deps.maybe_warn_about_anthropic_subscription_auth()
        self._deps.ui.request_render()

    # -- the two dialogs ---------------------------------------------------

    async def _show_api_key_login_dialog(self, provider_id: str, provider_name: str) -> None:
        previous_model = self._session.model

        dialog = LoginDialogComponent(
            self._deps.ui,
            provider_id,
            lambda _success, _message: None,
            provider_name,
        )

        self._deps.editor_container.clear()
        self._deps.editor_container.add_child(dialog)
        self._deps.ui.set_focus(dialog)
        self._deps.ui.request_render()

        try:
            api_key = (await dialog.show_prompt("Enter API key:")).strip()
            if not api_key:
                raise ValueError("API key cannot be empty.")

            self._registry.auth_storage.set(provider_id, ApiKeyCredential(key=api_key))

            self._restore_editor()
            await self._complete_provider_authentication(
                provider_id, provider_name, "api_key", previous_model
            )
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            self._restore_editor()
            if str(error) != LOGIN_CANCELLED:
                self._deps.show_error(f"Failed to save API key for {provider_name}: {error}")
            self._deps.ui.request_render()

    async def _show_oauth_login_select(
        self, dialog: LoginDialogComponent, prompt: Any
    ) -> str | None:
        """The provider asked a multiple-choice question mid-flow.

        The selector takes the dialog's place and the dialog is put back either
        way, because the flow it belongs to is still running underneath.
        """
        future: asyncio.Future[str | None] = asyncio.get_event_loop().create_future()

        def restore_dialog() -> None:
            self._deps.editor_container.clear()
            self._deps.editor_container.add_child(dialog)
            self._deps.ui.set_focus(dialog)
            self._deps.ui.request_render()

        options = list(prompt.options)
        labels = [option.label for option in options]

        def select(option_label: str) -> None:
            restore_dialog()
            if not future.done():
                chosen = next((o for o in options if o.label == option_label), None)
                future.set_result(chosen.id if chosen is not None else None)

        def cancel() -> None:
            restore_dialog()
            if not future.done():
                future.set_result(None)

        selector = ExtensionSelectorComponent(prompt.message, labels, select, cancel)
        self._deps.editor_container.clear()
        self._deps.editor_container.add_child(selector)
        self._deps.ui.set_focus(selector)
        self._deps.ui.request_render()

        return await future

    async def _show_login_dialog(self, provider_id: str, provider_name: str) -> None:
        auth_storage = self._registry.auth_storage
        provider_info = next(
            (p for p in auth_storage.get_oauth_providers() if p.id == provider_id), None
        )
        previous_model = self._session.model

        # Providers with a callback server let the user paste the redirect URL
        # instead of waiting for the browser to come back.
        uses_callback_server = bool(
            getattr(provider_info, "uses_callback_server", False)
            if provider_info is not None
            else False
        )

        dialog = LoginDialogComponent(
            self._deps.ui,
            provider_id,
            lambda _success, _message: None,
            provider_name,
        )

        self._deps.editor_container.clear()
        self._deps.editor_container.add_child(dialog)
        self._deps.ui.set_focus(dialog)
        self._deps.ui.request_render()

        # The manual-code path races the callback server: whichever answers
        # first is the code, and cancelling fails both.
        manual_code_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        def on_auth(info: Any) -> None:
            dialog.show_auth(info.url, getattr(info, "instructions", None))

            if uses_callback_server:
                manual = dialog.show_manual_input(
                    "Paste redirect URL below, or complete login in browser:"
                )

                def forward(task: asyncio.Future[str]) -> None:
                    if manual_code_future.done():
                        return
                    if task.cancelled():
                        return
                    error = task.exception()
                    if error is not None:
                        manual_code_future.set_exception(RuntimeError(LOGIN_CANCELLED))
                        return
                    value = task.result()
                    if value:
                        manual_code_future.set_result(value)

                manual.add_done_callback(forward)
            elif provider_id == "github-copilot":
                # GitHub Copilot polls after `on_auth` rather than prompting.
                dialog.show_waiting("Waiting for browser authentication...")
            # Anthropic calls `on_prompt` immediately after this.

        callbacks = _OAuthLoginCallbacks(
            on_auth=on_auth,
            on_prompt=lambda prompt: dialog.show_prompt(
                prompt.message, getattr(prompt, "placeholder", None)
            ),
            on_progress=dialog.show_progress,
            on_select=lambda prompt: self._show_oauth_login_select(dialog, prompt),
            on_manual_code_input=lambda: manual_code_future,
            signal=dialog.signal,
        )

        try:
            await auth_storage.login(provider_id, callbacks)

            self._restore_editor()
            await self._complete_provider_authentication(
                provider_id, provider_name, "oauth", previous_model
            )
        except Exception as error:  # noqa: BLE001 - the TS's catch, one for one
            self._restore_editor()
            if str(error) != LOGIN_CANCELLED:
                self._deps.show_error(f"Failed to login to {provider_name}: {error}")
            self._deps.ui.request_render()


class _OAuthLoginCallbacks:
    """The callbacks a provider's ``login`` flow calls, as one object.

    A class rather than a dict because
    :class:`~cortex.ai.oauth.OAuthLoginCallbacks` is a Protocol of methods —
    a provider calls ``callbacks.on_auth(...)``, not ``callbacks["onAuth"]``.
    """

    def __init__(
        self,
        *,
        on_auth: Callable[[Any], None],
        on_prompt: Callable[[Any], Any],
        on_progress: Callable[[str], None],
        on_select: Callable[[Any], Any],
        on_manual_code_input: Callable[[], Any],
        signal: Any,
    ) -> None:
        self._on_auth = on_auth
        self._on_prompt = on_prompt
        self._on_progress = on_progress
        self._on_select = on_select
        self._on_manual_code_input = on_manual_code_input
        self.signal = signal

    def on_auth(self, info: Any) -> None:
        self._on_auth(info)

    async def on_prompt(self, prompt: Any) -> str:
        return await self._on_prompt(prompt)

    def on_progress(self, message: str) -> None:
        self._on_progress(message)

    async def on_select(self, prompt: Any) -> str | None:
        return await self._on_select(prompt)

    async def on_manual_code_input(self) -> str:
        return await self._on_manual_code_input()


def _schedule(coro: Any) -> None:
    """Run a coroutine from a keystroke handler, loop or no loop.

    The same helper as
    :func:`cortex.code.interactive.model_controller._schedule`, and for the same
    reason: a selector's callbacks are reached synchronously from a keystroke and
    the work behind them is not.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    _ = asyncio.ensure_future(coro)
