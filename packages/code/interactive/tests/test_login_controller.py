"""Tests for the auth flows (step 7.11).

``TestIsApiKeyLoginProvider`` and ``TestOAuthSelectorComponent`` are the port of
``test/oauth-selector.test.ts``. The rest is the controller, driven the way
`test_selectors.py` drives components — construct, send keystrokes, read what came
back — because that is the layer the branching lives at. What the *app* does with
it (open the picker over the editor, put the editor back) is the end-to-end
corpus's job, in `auth/login-dialog` and `auth/missing-key-message`.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, cast

import pytest
from cortex.ai.types import Model
from cortex.code.config import (
    BUILT_IN_PROVIDER_DISPLAY_NAMES,
    ApiKeyCredential,
    AuthStatus,
    AuthStorage,
    ModelRegistry,
    OAuthCredential,
)
from cortex.code.interactive.components.oauth_selector import (
    AuthSelectorProvider,
    OAuthSelectorComponent,
)
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.interactive.login_controller import (
    API_KEY_LABEL,
    NO_REGISTRY_MESSAGE,
    NO_STORED_CREDENTIALS_MESSAGE,
    SUBSCRIPTION_LABEL,
    LoginController,
    is_api_key_login_provider,
)
from cortex.tui.keys import set_keybindings

DOWN = "\x1b[B"
ENTER = "\r"
ESCAPE = "\x1b"


@pytest.fixture(autouse=True)
def _app_keybindings() -> None:  # pyright: ignore[reportUnusedFunction]
    set_keybindings(KeybindingsManager())


def _lines(component: Any, width: int = 120) -> list[str]:
    return [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in component.render(width)]


def _text(component: Any, width: int = 120) -> str:
    return "\n".join(_lines(component, width))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _model(provider: str, model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="anthropic-messages",
        provider=provider,
        base_url="https://example.invalid",
        reasoning=False,
        input=["text"],
        cost={"input": 0.0, "output": 0.0},
        context_window=1000,
        max_tokens=100,
    )


class TestIsApiKeyLoginProvider:
    def test_built_in_api_key_providers_are_separate_from_oauth_only_ones(self):
        oauth_ids = {"anthropic", "github-copilot", "custom-oauth"}
        built_in_ids = {"anthropic", "github-copilot", "openai"}

        # Anthropic offers both, and the display-name table is what says so.
        assert is_api_key_login_provider("anthropic", oauth_ids, built_in_ids) is True
        assert BUILT_IN_PROVIDER_DISPLAY_NAMES["anthropic"] == "Anthropic"
        assert is_api_key_login_provider("openai", oauth_ids, built_in_ids) is True
        # A built-in model provider that is not in the table is subscription-only.
        assert is_api_key_login_provider("github-copilot", oauth_ids, built_in_ids) is False
        assert is_api_key_login_provider("custom-oauth", oauth_ids, built_in_ids) is False
        # Anything else takes a key unless it registered an OAuth flow.
        assert is_api_key_login_provider("custom-api", oauth_ids, built_in_ids) is True

    def test_the_built_in_set_defaults_to_the_live_provider_list(self):
        """Read at call time, not import time, so an extension's provider counts."""
        assert is_api_key_login_provider("openai", set()) is True
        assert is_api_key_login_provider("some-extension-provider", set()) is True

    def test_a_built_in_provider_outside_the_table_is_subscription_only(self):
        """Rule 2 on its own, with nothing else able to answer.

        Added after mutation testing: every built-in provider missing from the
        display table happens to *also* be an OAuth provider, so rule 3 returned
        the same `False` and deleting rule 2 changed no assertion.
        """
        assert (
            is_api_key_login_provider(
                "gated-provider", oauth_provider_ids=set(), built_in_provider_ids={"gated-provider"}
            )
            is False
        )
        # Same inputs minus the built-in membership: now it takes a key.
        assert (
            is_api_key_login_provider(
                "gated-provider", oauth_provider_ids=set(), built_in_provider_ids=set()
            )
            is True
        )


class TestOAuthSelectorComponent:
    def test_shows_stored_oauth_auth_distinctly_in_the_api_key_selector(self):
        """You have a subscription, this row offers a key: a warning, not a tick,
        because logging in here would replace what you have."""
        auth_storage = AuthStorage.in_memory(
            {
                "anthropic": OAuthCredential(
                    refresh="refresh-token", access="access-token", expires=_now_ms() + 60_000
                )
            }
        )
        selector = OAuthSelectorComponent(
            "login",
            auth_storage,
            [AuthSelectorProvider(id="anthropic", name="Anthropic", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
        )

        output = _text(selector)
        assert "Anthropic" in output
        assert "subscription configured" in output

    def test_shows_stored_api_key_when_the_row_offers_a_subscription(self):
        auth_storage = AuthStorage.in_memory({"anthropic": ApiKeyCredential(key="sk-x")})
        selector = OAuthSelectorComponent(
            "login",
            auth_storage,
            [AuthSelectorProvider(id="anthropic", name="Anthropic", auth_type="oauth")],
            lambda _id: None,
            lambda: None,
        )
        assert "API key configured" in _text(selector)

    def test_a_matching_credential_is_a_plain_tick(self):
        auth_storage = AuthStorage.in_memory({"anthropic": ApiKeyCredential(key="sk-x")})
        selector = OAuthSelectorComponent(
            "login",
            auth_storage,
            [AuthSelectorProvider(id="anthropic", name="Anthropic", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
        )
        assert "✓ configured" in _text(selector)

    def test_shows_environment_api_key_auth_as_configured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        selector = OAuthSelectorComponent(
            "login",
            AuthStorage.in_memory(),
            [AuthSelectorProvider(id="openai", name="OpenAI", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
        )

        output = _text(selector)
        assert "OpenAI" in output
        assert "✓ env: OPENAI_API_KEY" in output
        assert "unconfigured" not in output

    def test_shows_a_custom_providers_env_key_from_the_status_resolver(self):
        selector = OAuthSelectorComponent(
            "login",
            AuthStorage.in_memory(),
            [AuthSelectorProvider(id="ollama", name="ollama", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
            lambda _provider: AuthStatus(
                configured=True, source="environment", label="OLLAMA_API_KEY"
            ),
        )

        output = _text(selector)
        assert "ollama" in output
        assert "✓ env: OLLAMA_API_KEY" in output
        assert "unconfigured" not in output

    def test_shows_models_json_api_key_auth_as_configured(self):
        selector = OAuthSelectorComponent(
            "login",
            AuthStorage.in_memory(),
            [AuthSelectorProvider(id="local-proxy", name="local-proxy", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
            lambda _provider: AuthStatus(configured=True, source="models_json_key"),
        )

        output = _text(selector)
        assert "local-proxy" in output
        assert "✓ key in models.json" in output
        assert "unconfigured" not in output

    def test_shows_models_json_command_auth_as_configured(self):
        selector = OAuthSelectorComponent(
            "login",
            AuthStorage.in_memory(),
            [AuthSelectorProvider(id="op-proxy", name="op-proxy", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
            lambda _provider: AuthStatus(configured=True, source="models_json_command"),
        )

        output = _text(selector)
        assert "op-proxy" in output
        assert "✓ command in models.json" in output
        assert "unconfigured" not in output

    def test_a_runtime_key_is_reported_as_such(self):
        storage = AuthStorage.in_memory()
        storage.set_runtime_api_key("openai", "k")
        selector = OAuthSelectorComponent(
            "login",
            storage,
            [AuthSelectorProvider(id="openai", name="OpenAI", auth_type="api_key")],
            lambda _id: None,
            lambda: None,
        )
        assert "✓ runtime API key" in _text(selector)

    def test_titles_differ_between_login_and_logout(self):
        providers = [AuthSelectorProvider(id="openai", name="OpenAI", auth_type="api_key")]
        login = OAuthSelectorComponent(
            "login", AuthStorage.in_memory(), providers, lambda _i: None, lambda: None
        )
        logout = OAuthSelectorComponent(
            "logout", AuthStorage.in_memory(), providers, lambda _i: None, lambda: None
        )
        assert "Select provider to configure:" in _text(login)
        assert "Select provider to logout:" in _text(logout)

    def test_the_empty_message_depends_on_the_mode(self):
        login = OAuthSelectorComponent(
            "login", AuthStorage.in_memory(), [], lambda _i: None, lambda: None
        )
        logout = OAuthSelectorComponent(
            "logout", AuthStorage.in_memory(), [], lambda _i: None, lambda: None
        )
        assert "No providers available" in _text(login)
        assert "No providers logged in. Use /login first." in _text(logout)

    def test_typing_filters_and_a_miss_says_so(self):
        providers = [
            AuthSelectorProvider(id="openai", name="OpenAI", auth_type="api_key"),
            AuthSelectorProvider(id="anthropic", name="Anthropic", auth_type="api_key"),
        ]
        selector = OAuthSelectorComponent(
            "login", AuthStorage.in_memory(), providers, lambda _i: None, lambda: None
        )
        assert "OpenAI" in _text(selector)

        for char in "anthro":
            selector.handle_input(char)
        output = _text(selector)
        assert "Anthropic" in output
        assert "OpenAI" not in output

        for char in "zzzz":
            selector.handle_input(char)
        assert "No matching providers" in _text(selector)

    def test_enter_selects_the_highlighted_provider(self):
        chosen: list[str] = []
        providers = [
            AuthSelectorProvider(id="openai", name="OpenAI", auth_type="api_key"),
            AuthSelectorProvider(id="anthropic", name="Anthropic", auth_type="api_key"),
        ]
        selector = OAuthSelectorComponent(
            "login", AuthStorage.in_memory(), providers, chosen.append, lambda: None
        )

        selector.handle_input(DOWN)
        selector.handle_input(ENTER)
        assert chosen == ["anthropic"]

    def test_escape_cancels(self):
        cancels: list[bool] = []
        selector = OAuthSelectorComponent(
            "login",
            AuthStorage.in_memory(),
            [AuthSelectorProvider(id="openai", name="OpenAI", auth_type="api_key")],
            lambda _i: None,
            lambda: cancels.append(True),
        )
        selector.handle_input(ESCAPE)
        assert cancels == [True]

    def test_navigation_on_an_empty_list_does_nothing(self):
        selector = OAuthSelectorComponent(
            "login", AuthStorage.in_memory(), [], lambda _i: None, lambda: None
        )
        selector.handle_input(DOWN)
        assert "No providers available" in _text(selector)


# ---------------------------------------------------------------------------
# The controller
# ---------------------------------------------------------------------------


class FakeUI:
    def __init__(self) -> None:
        self.focused: Any = None
        self.renders = 0

    def set_focus(self, component: Any) -> None:
        self.focused = component

    def request_render(self) -> None:
        self.renders += 1


class FakeContainer:
    def __init__(self) -> None:
        self.children: list[Any] = []

    def clear(self) -> None:
        self.children = []

    def add_child(self, child: Any) -> None:
        self.children.append(child)


class FakeSession:
    def __init__(self, registry: ModelRegistry, model: Any = None) -> None:
        self.model_registry = registry
        self.model = model
        self.set_models: list[Any] = []
        self.set_model_error: Exception | None = None

    async def set_model(self, model: Any) -> None:
        if self.set_model_error is not None:
            raise self.set_model_error
        self.set_models.append(model)
        self.model = model


UNKNOWN_MODEL = Model(
    id="unknown",
    name="unknown",
    api="unknown",
    provider="unknown",
    base_url="",
    reasoning=False,
    input=["text"],
    cost={"input": 0.0, "output": 0.0},
    context_window=1000,
    max_tokens=100,
)


class FakeDeps:
    """The app's slice, recorded rather than rendered."""

    def __init__(self, session: FakeSession) -> None:
        self.ui = FakeUI()
        self.session = session
        self.editor = object()
        self.editor_container = FakeContainer()
        self.statuses: list[str] = []
        self.errors: list[str] = []
        self.provider_count_updates = 0
        self.border_updates = 0
        self.footer_invalidations = 0
        self.warnings: list[Any] = []
        #: What the last `show_selector` put on screen, and how to close it.
        self.selector: Any = None
        self.done: Any = None

    def get_editor(self) -> Any:
        return self.editor

    def show_selector(self, create: Any) -> None:
        def done() -> None:
            self.editor_container.clear()
            self.editor_container.add_child(self.editor)

        component, focus = create(done)
        self.done = done
        self.selector = component
        self.editor_container.clear()
        self.editor_container.add_child(component)
        self.ui.set_focus(focus)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)

    def show_error(self, error_message: str) -> None:
        self.errors.append(error_message)

    async def update_available_provider_count(self) -> None:
        self.provider_count_updates += 1

    def update_editor_border_color(self) -> None:
        self.border_updates += 1

    def invalidate_footer(self) -> None:
        self.footer_invalidations += 1

    async def maybe_warn_about_anthropic_subscription_auth(self, model: Any = None) -> None:
        self.warnings.append(model)


def _controller(
    *, stored: dict[str, Any] | None = None, model: Any = None
) -> tuple[LoginController, FakeDeps, ModelRegistry]:
    storage = AuthStorage.in_memory(stored or {})
    registry = ModelRegistry.in_memory(storage)
    deps = FakeDeps(FakeSession(registry, model))
    # The fakes stand in for a TUI and a Container structurally; the controller
    # only ever calls the handful of methods above.
    return LoginController(cast(Any, deps)), deps, registry


class TestLoginFlow:
    async def test_login_opens_the_auth_type_picker_first(self):
        """Asking subscription-or-key first is what keeps the provider list short."""
        controller, deps, _registry = _controller()
        await controller.show_oauth_selector("login")

        output = _text(deps.selector)
        assert "Select authentication method:" in output
        assert SUBSCRIPTION_LABEL in output
        assert API_KEY_LABEL in output

    async def test_choosing_a_subscription_lists_only_oauth_providers(self):
        controller, deps, _registry = _controller()
        await controller.show_oauth_selector("login")

        deps.selector.handle_input(ENTER)  # "Use a subscription"

        output = _text(deps.selector)
        assert "Select provider to configure:" in output
        # The three built-in OAuth providers, and no API-key-only one.
        assert "Anthropic (Claude Pro/Max)" in output
        assert "OpenAI" not in output.replace("OpenAI Codex", "")

    async def test_choosing_an_api_key_lists_api_key_providers(self):
        controller, deps, _registry = _controller()
        await controller.show_oauth_selector("login")

        deps.selector.handle_input(DOWN)
        deps.selector.handle_input(ENTER)  # "Use an API key"

        output = _text(deps.selector)
        assert "Select provider to configure:" in output
        assert "Anthropic" in output

    async def test_escape_from_the_provider_list_goes_back_a_screen(self):
        """Not out — back. Otherwise the auth-type screen is unreachable after a
        mis-step."""
        controller, deps, _registry = _controller()
        await controller.show_oauth_selector("login")
        deps.selector.handle_input(ENTER)
        assert "Select provider to configure:" in _text(deps.selector)

        deps.selector.handle_input(ESCAPE)
        assert "Select authentication method:" in _text(deps.selector)

    async def test_escape_from_the_auth_type_picker_closes_and_restores_the_editor(self):
        controller, deps, _registry = _controller()
        await controller.show_oauth_selector("login")

        deps.selector.handle_input(ESCAPE)
        assert deps.editor_container.children == [deps.editor]

    async def test_every_api_key_provider_row_has_a_display_name(self):
        controller, _deps, _registry = _controller()
        options = controller._get_login_provider_options("api_key")  # pyright: ignore[reportPrivateUsage]

        assert options
        for option in options:
            assert option.name
            assert option.auth_type == "api_key"
        # Sorted by name, which is what makes the list scannable.
        assert [o.name for o in options] == sorted(o.name for o in options)

    async def test_the_provider_list_only_offers_providers_that_have_models(self):
        controller, _deps, registry = _controller()
        model_providers = {m.provider for m in registry.get_all()}
        options = controller._get_login_provider_options("api_key")  # pyright: ignore[reportPrivateUsage]

        for option in options:
            assert option.id in model_providers


class TestLogoutFlow:
    async def test_logout_with_nothing_stored_explains_what_it_does_not_touch(self):
        """ "No stored credentials" would read as "you are not logged in" to a user
        whose key comes from the environment."""
        controller, deps, _registry = _controller()
        await controller.show_oauth_selector("logout")

        assert deps.selector is None
        assert deps.statuses == [NO_STORED_CREDENTIALS_MESSAGE]

    async def test_logout_lists_only_what_is_stored(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        controller, deps, _registry = _controller(
            stored={"anthropic": ApiKeyCredential(key="sk-stored")}
        )
        await controller.show_oauth_selector("logout")

        output = _text(deps.selector)
        assert "Anthropic" in output
        # The env-key provider is not offered: `/logout` cannot remove it.
        assert "OpenAI" not in output

    async def test_logout_removes_the_credential_and_says_which(self):
        controller, deps, registry = _controller(
            stored={"anthropic": ApiKeyCredential(key="sk-stored")}
        )
        await controller.show_oauth_selector("logout")

        deps.selector.handle_input(ENTER)
        await asyncio.sleep(0)

        assert registry.auth_storage.get("anthropic") is None
        assert any("Removed stored API key" in s for s in deps.statuses)
        assert deps.provider_count_updates == 1

    async def test_logging_out_of_a_subscription_is_worded_differently(self):
        controller, deps, registry = _controller(
            stored={
                "anthropic": OAuthCredential(refresh="r", access="a", expires=_now_ms() + 60_000)
            }
        )
        await controller.show_oauth_selector("logout")

        deps.selector.handle_input(ENTER)
        await asyncio.sleep(0)

        assert any("Logged out of" in s for s in deps.statuses)
        assert registry.auth_storage.get("anthropic") is None

    async def test_a_failing_logout_is_reported(self):
        controller, deps, registry = _controller(stored={"anthropic": ApiKeyCredential(key="sk")})
        await controller.show_oauth_selector("logout")

        def boom(_provider: str) -> None:
            raise RuntimeError("disk on fire")

        registry.auth_storage.logout = boom  # type: ignore[method-assign]
        deps.selector.handle_input(ENTER)
        await asyncio.sleep(0)

        assert any("Logout failed: disk on fire" in e for e in deps.errors)


class TestApiKeyLoginDialog:
    async def test_a_typed_key_is_stored_and_the_editor_comes_back(self):
        controller, deps, registry = _controller(model=_model("anthropic", "claude-x"))

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)

        dialog = deps.editor_container.children[0]
        assert "Login to Anthropic" in _text(dialog)
        for char in "sk-typed-key":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await task

        stored = registry.auth_storage.get("anthropic")
        assert isinstance(stored, ApiKeyCredential)
        assert stored.key == "sk-typed-key"
        assert deps.editor_container.children == [deps.editor]
        assert any("Saved API key for Anthropic" in s for s in deps.statuses)

    async def test_an_empty_key_is_refused_with_a_message(self):
        controller, deps, registry = _controller(model=_model("anthropic", "claude-x"))

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)

        dialog = deps.editor_container.children[0]
        dialog.handle_input(ENTER)
        await task

        assert registry.auth_storage.get("anthropic") is None
        assert any("cannot be empty" in e for e in deps.errors)
        assert deps.editor_container.children == [deps.editor]

    async def test_escape_cancels_quietly(self):
        """A deliberate cancel is not an error, which is what the sentinel message
        is for.

        The `wait_for` is not decoration: cancelling has to *fail the pending
        future*, and a version that only aborts the controller leaves this await
        hanging rather than failing. Mutation testing found exactly that — the run
        wedged instead of reporting — so the timeout is what turns the hang into a
        result.
        """
        controller, deps, registry = _controller(model=_model("anthropic", "claude-x"))

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)

        dialog = deps.editor_container.children[0]
        dialog.handle_input(ESCAPE)
        await asyncio.wait_for(task, timeout=5)

        assert registry.auth_storage.get("anthropic") is None
        assert deps.errors == []
        assert deps.editor_container.children == [deps.editor]

    async def test_a_first_login_selects_the_providers_default_model(self):
        """A user whose first action was `/login` has no model, so leaving them on
        the sentinel means the next Enter says "no API key" right after they gave
        one."""
        controller, deps, _registry = _controller(model=UNKNOWN_MODEL)

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        dialog = deps.editor_container.children[0]
        for char in "sk-x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await task

        assert [m.id for m in deps.session.set_models] == ["claude-opus-4-7"]
        assert any("Selected claude-opus-4-7" in s for s in deps.statuses)
        assert deps.warnings == [deps.session.model]

    async def test_an_existing_model_is_left_alone(self):
        current = _model("openai", "gpt-x")
        controller, deps, _registry = _controller(model=current)

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        dialog = deps.editor_container.children[0]
        for char in "sk-x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await task

        assert deps.session.set_models == []
        assert deps.session.model is current

    async def test_a_provider_with_no_default_model_says_so_and_names_slash_model(self):
        controller, deps, registry = _controller(model=UNKNOWN_MODEL)
        # A provider that exists but has no entry in DEFAULT_MODEL_PER_PROVIDER.
        registry._models.append(_model("no-default-provider", "m1"))  # pyright: ignore[reportPrivateUsage]

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("no-default-provider", "No Default")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        dialog = deps.editor_container.children[0]
        for char in "sk-x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await task

        assert deps.errors
        assert "no default model is configured" in deps.errors[0]
        assert "/model" in deps.errors[0]

    async def test_a_failing_model_switch_is_reported_and_names_slash_model(self):
        controller, deps, _registry = _controller(model=UNKNOWN_MODEL)
        deps.session.set_model_error = RuntimeError("nope")

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        dialog = deps.editor_container.children[0]
        for char in "sk-x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await task

        assert deps.errors
        assert "selecting its default model failed" in deps.errors[0]
        assert "/model" in deps.errors[0]

    async def test_the_new_key_counts_towards_availability(self):
        controller, deps, registry = _controller(model=_model("openai", "gpt-x"))
        assert registry.get_available_sync() == []

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        dialog = deps.editor_container.children[0]
        for char in "sk-x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await task

        available = registry.get_available_sync()
        assert available
        assert {m.provider for m in available} == {"anthropic"}

    async def test_the_registry_is_reloaded_from_disk_after_a_login(self, tmp_path: Any):
        """What `refresh()` actually buys, which availability alone cannot show.

        Added after mutation testing: `has_configured_auth` reads the storage
        live, so the new key counted with or without the refresh and deleting it
        passed. Re-reading `models.json` is the part that only a refresh does.
        """
        import json

        models_json = tmp_path / "models.json"
        models_json.write_text(json.dumps({"providers": {}}), encoding="utf-8")

        storage = AuthStorage.in_memory()
        registry = ModelRegistry.create(storage, str(models_json))
        deps = FakeDeps(FakeSession(registry, _model("openai", "gpt-x")))
        controller = LoginController(cast(Any, deps))

        assert registry.find("anthropic", "claude-from-disk") is None

        # A models.json that appeared after the registry was built.
        models_json.write_text(
            json.dumps(
                {
                    "providers": {
                        "anthropic": {
                            "models": [
                                {
                                    "id": "claude-from-disk",
                                    "reasoning": False,
                                    "input": ["text"],
                                }
                            ]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        task = asyncio.ensure_future(
            controller._show_api_key_login_dialog("anthropic", "Anthropic")  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        dialog = deps.editor_container.children[0]
        for char in "sk-x":
            dialog.handle_input(char)
        dialog.handle_input(ENTER)
        await asyncio.wait_for(task, timeout=5)

        assert registry.find("anthropic", "claude-from-disk") is not None


class TestNoRegistry:
    """A session built without a registry: `/login` explains rather than crashes.

    `_create_unpersisted_session` builds one, and so does every test or scenario
    that brings its own session, so this is a reachable state and not a
    defensive branch.
    """

    async def test_login_says_so_instead_of_raising(self):
        deps = FakeDeps(FakeSession(cast(Any, None)))
        controller = LoginController(cast(Any, deps))

        await controller.show_oauth_selector("login")

        assert deps.statuses == [NO_REGISTRY_MESSAGE]
        assert deps.selector is None
        assert deps.errors == []

    async def test_logout_says_so_too(self):
        deps = FakeDeps(FakeSession(cast(Any, None)))
        controller = LoginController(cast(Any, deps))

        await controller.show_oauth_selector("logout")

        assert deps.statuses == [NO_REGISTRY_MESSAGE]
        assert deps.selector is None
