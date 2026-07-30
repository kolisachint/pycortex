"""The provider picker behind ``/login`` and ``/logout``.

Port of ``components/oauth-selector.ts``. A searchable list of providers, each row
annotated with what auth it already has — which is the component's real job, and
the reason it takes an
:class:`~cortex.code.config.AuthStorage` rather than pre-rendered strings.

**The status column distinguishes five ways of having a key, and the distinction
is what makes the screen useful.** A stored credential of the *asked-for* kind is
"✓ configured"; a stored credential of the other kind ("you have an API key, this
row offers a subscription") is a warning rather than a tick, because logging in
here replaces it; and a key that comes from the environment, the ``--api-key``
flag or ``models.json`` is a tick with its source named, because ``/login`` cannot
change it and ``/logout`` cannot remove it.

``get_auth_status`` is injected rather than read off the storage directly so the
registry's wider answer can be used — the storage alone cannot see keys that
exist only in ``models.json``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from cortex.code.config import AuthStatus, AuthStorage, OAuthCredential
from cortex.code.interactive.components.dynamic_border import DynamicBorder
from cortex.code.interactive.theme import get_theme
from cortex.tui.components import Input, Spacer, TruncatedText
from cortex.tui.fuzzy import fuzzy_filter
from cortex.tui.keys import get_keybindings
from cortex.tui.render import Container

__all__ = ["AuthSelectorProvider", "OAuthSelectorComponent"]

#: How many rows the list shows at once, the TS's ``maxVisible``.
_MAX_VISIBLE = 8


@dataclass(frozen=True)
class AuthSelectorProvider:
    """One row: a provider, and which kind of auth this row offers.

    A provider can appear twice in a login list — once for its subscription and
    once for an API key — so ``auth_type`` is part of the row rather than a
    property of the provider.
    """

    id: str
    name: str
    auth_type: Literal["oauth", "api_key"]


class OAuthSelectorComponent(Container):
    """Renders an auth provider selector."""

    def __init__(
        self,
        mode: Literal["login", "logout"],
        auth_storage: AuthStorage,
        providers: list[AuthSelectorProvider],
        on_select: Callable[[str], None],
        on_cancel: Callable[[], None],
        get_auth_status: Callable[[str], AuthStatus] | None = None,
    ) -> None:
        super().__init__()

        self._mode = mode
        self._auth_storage = auth_storage
        self._get_auth_status = (
            get_auth_status if get_auth_status is not None else auth_storage.get_auth_status
        )
        self._all_providers = list(providers)
        self._filtered_providers = list(providers)
        self._selected_index = 0
        self._on_select = on_select
        self._on_cancel = on_cancel
        self._focused = False

        theme = get_theme()

        self.add_child(DynamicBorder())
        self.add_child(Spacer(1))

        title = "Select provider to configure:" if mode == "login" else "Select provider to logout:"
        self.add_child(TruncatedText(theme.fg("accent", theme.bold(title)), 1, 0))
        self.add_child(Spacer(1))

        self._search_input = Input()
        self._search_input.on_submit = self._submit_selected
        self.add_child(self._search_input)
        self.add_child(Spacer(1))

        self._list_container = Container()
        self.add_child(self._list_container)

        self.add_child(Spacer(1))
        self.add_child(DynamicBorder())

        self._filter_providers("")

    # -- focus -------------------------------------------------------------

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        """Propagate focus to the search box so the caret lands in it."""
        self._focused = value
        self._search_input.focused = value

    # -- list --------------------------------------------------------------

    def _submit_selected(self, _value: str | None = None) -> None:
        """Confirm the highlighted row. Reached from Enter and from
        ``Input.on_submit``, which passes the search text — ignored, because the
        selection is what is being confirmed, not what was typed."""
        if 0 <= self._selected_index < len(self._filtered_providers):
            self._on_select(self._filtered_providers[self._selected_index].id)

    def _filter_providers(self, query: str) -> None:
        self._filtered_providers = (
            fuzzy_filter(
                self._all_providers,
                query,
                lambda provider: f"{provider.name} {provider.id} {provider.auth_type}",
            )
            if query
            else list(self._all_providers)
        )
        self._selected_index = max(
            0, min(self._selected_index, max(0, len(self._filtered_providers) - 1))
        )
        self._update_list()

    def _update_list(self) -> None:
        theme = get_theme()
        self._list_container.clear()

        start_index = max(
            0,
            min(
                self._selected_index - _MAX_VISIBLE // 2,
                len(self._filtered_providers) - _MAX_VISIBLE,
            ),
        )
        end_index = min(start_index + _MAX_VISIBLE, len(self._filtered_providers))

        for index in range(start_index, end_index):
            provider = self._filtered_providers[index]
            status_indicator = self._format_status_indicator(provider)
            if index == self._selected_index:
                line = (
                    theme.fg("accent", "→ ") + theme.fg("accent", provider.name) + status_indicator
                )
            else:
                line = f"  {theme.fg('text', provider.name)}" + status_indicator
            self._list_container.add_child(TruncatedText(line, 1, 0))

        if start_index > 0 or end_index < len(self._filtered_providers):
            scroll_info = theme.fg(
                "muted", f"  ({self._selected_index + 1}/{len(self._filtered_providers)})"
            )
            self._list_container.add_child(TruncatedText(scroll_info, 1, 0))

        if not self._filtered_providers:
            if self._all_providers:
                message = "No matching providers"
            elif self._mode == "login":
                message = "No providers available"
            else:
                message = "No providers logged in. Use /login first."
            self._list_container.add_child(TruncatedText(theme.fg("muted", f"  {message}"), 1, 0))

    def _format_status_indicator(self, provider: AuthSelectorProvider) -> str:
        """The right-hand annotation on a provider's row."""
        theme = get_theme()
        credential = self._auth_storage.get(provider.id)
        if credential is None:
            credential_type = None
        elif isinstance(credential, OAuthCredential):
            credential_type = "oauth"
        else:
            credential_type = "api_key"

        if credential_type == provider.auth_type:
            return theme.fg("success", " ✓ configured")
        if credential is not None:
            # A credential of the *other* kind: logging in here replaces it, so
            # this is a warning rather than a tick.
            label = (
                "subscription configured" if credential_type == "oauth" else "API key configured"
            )
            return theme.fg("muted", " • ") + theme.fg("warning", label)
        if provider.auth_type != "api_key":
            return theme.fg("muted", " • unconfigured")

        status = self._get_auth_status(provider.id)
        if status.source == "environment":
            return theme.fg("success", f" ✓ env: {status.label or 'API key'}")
        if status.source == "runtime":
            return theme.fg("success", " ✓ runtime API key")
        if status.source == "fallback":
            return theme.fg("success", " ✓ custom API key")
        if status.source == "models_json_key":
            return theme.fg("success", " ✓ key in models.json")
        if status.source == "models_json_command":
            return theme.fg("success", " ✓ command in models.json")
        return theme.fg("muted", " • unconfigured")

    # -- input -------------------------------------------------------------

    def handle_input(self, key_data: str) -> None:
        kb = get_keybindings()
        if kb.matches(key_data, "tui.select.up"):
            if not self._filtered_providers:
                return
            self._selected_index = max(0, self._selected_index - 1)
            self._update_list()
        elif kb.matches(key_data, "tui.select.down"):
            if not self._filtered_providers:
                return
            self._selected_index = min(len(self._filtered_providers) - 1, self._selected_index + 1)
            self._update_list()
        elif kb.matches(key_data, "tui.select.confirm"):
            self._submit_selected()
        elif kb.matches(key_data, "tui.select.cancel"):
            self._on_cancel()
        else:
            # Anything else is search text, which re-filters the list.
            self._search_input.handle_input(key_data)
            self._filter_providers(self._search_input.get_value())
