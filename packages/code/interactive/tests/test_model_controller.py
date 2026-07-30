"""Tests for the model controller (step 7.9).

The controller is the layer between a keystroke and the session, so what is worth
pinning is what it *reports*: which status line a cycle produces, which overlay it
opens, and what it does when the registry has nothing to offer. It reaches its
dependencies through a protocol, so all of that runs against a context built by
hand — no TUI, no terminal, no provider.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cortex.ai.types import Model
from cortex.code.config import SettingsManager, WarningSettings
from cortex.code.config.settings_storage import InMemorySettingsStorage
from cortex.code.interactive.keybindings import KeybindingsManager
from cortex.code.interactive.model_controller import (
    ANTHROPIC_SUBSCRIPTION_AUTH_WARNING,
    ModelController,
)
from cortex.code.session import ScopedModel
from cortex.tui.keys import set_keybindings


@pytest.fixture(autouse=True)
def _app_keybindings() -> None:  # pyright: ignore[reportUnusedFunction]
    set_keybindings(KeybindingsManager())


def _model(provider: str, model_id: str, *, reasoning: bool = False) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="anthropic-messages",
        provider=provider,
        base_url="https://example.invalid",
        reasoning=reasoning,
        input=["text"],
        cost={"input": 0.0, "output": 0.0},
        context_window=1000,
        max_tokens=100,
    )


ALPHA = _model("anthropic", "alpha")
BETA = _model("openai", "beta")


class FakeUI:
    def __init__(self) -> None:
        self.renders = 0

    def request_render(self) -> None:
        self.renders += 1


class FakeRegistry:
    def __init__(self, models: list[Model], *, fails: bool = False) -> None:
        self._models = models
        self._fails = fails
        self.refreshes = 0

    def refresh(self) -> None:
        self.refreshes += 1

    async def get_available(self) -> list[Model]:
        if self._fails:
            raise RuntimeError("registry is unreachable")
        return list(self._models)


class FakeSession:
    """Just the members the controller reads."""

    def __init__(
        self,
        *,
        models: list[Model] | None = None,
        scoped: list[Any] | None = None,
        registry: Any = None,
        cycle_result: Any = None,
        cycle_error: str | None = None,
        model: Model | None = None,
    ) -> None:
        self.settings_manager = SettingsManager.from_storage(InMemorySettingsStorage())
        self.model = model if model is not None else ALPHA
        self.scoped_models = list(scoped) if scoped else []
        self.model_registry = (
            registry if registry is not None else FakeRegistry(models if models else [])
        )
        self._cycle_result = cycle_result
        self._cycle_error = cycle_error
        self.set_models: list[Model] = []
        self.set_model_error: str | None = None
        self.scoped_writes: list[list[Any]] = []

    async def cycle_model(self, direction: str) -> Any:
        if self._cycle_error is not None:
            raise RuntimeError(self._cycle_error)
        return self._cycle_result

    async def set_model(self, model: Model) -> None:
        if self.set_model_error is not None:
            raise RuntimeError(self.set_model_error)
        self.set_models.append(model)
        self.model = model

    def set_scoped_models(self, scoped_models: list[Any]) -> None:
        self.scoped_writes.append(list(scoped_models))
        self.scoped_models = list(scoped_models)


class Deps:
    """A :class:`ModelControllerDeps` built by hand."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session
        self._ui = FakeUI()
        self.statuses: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.border_updates = 0
        self.footer_invalidations = 0
        self.provider_counts: list[int] = []
        #: The overlay the controller last opened, and the callback that closes it.
        self.component: Any = None
        self.focus: Any = None
        self.done: Any = None
        self.closes = 0

    @property
    def ui(self) -> Any:
        return self._ui

    @property
    def session(self) -> Any:
        return self._session

    def show_selector(self, create: Any) -> None:
        def done() -> None:
            self.closes += 1
            self.component = None

        self.done = done
        self.component, self.focus = create(done)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)

    def show_error(self, error_message: str) -> None:
        self.errors.append(error_message)

    def show_warning(self, warning_message: str) -> None:
        self.warnings.append(warning_message)

    def update_editor_border_color(self) -> None:
        self.border_updates += 1

    def invalidate_footer(self) -> None:
        self.footer_invalidations += 1

    def set_available_provider_count(self, count: int) -> None:
        self.provider_counts.append(count)


def _controller(session: FakeSession) -> tuple[ModelController, Deps]:
    deps = Deps(session)
    return ModelController(deps), deps


class _CycleResult:
    def __init__(self, model: Model, thinking_level: str = "off", is_scoped: bool = False) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self.is_scoped = is_scoped


class TestCycleModel:
    async def test_it_names_the_model_it_switched_to(self) -> None:
        controller, deps = _controller(FakeSession(cycle_result=_CycleResult(BETA)))
        await controller.cycle_model("forward")
        assert deps.statuses == ["Switched to beta"]
        assert (deps.footer_invalidations, deps.border_updates) == (1, 1)

    async def test_a_thinking_level_is_named_alongside_the_model(self) -> None:
        thinker = _model("anthropic", "thinker", reasoning=True)
        controller, deps = _controller(
            FakeSession(cycle_result=_CycleResult(thinker, "high", True))
        )
        await controller.cycle_model("forward")
        assert deps.statuses == ["Switched to thinker (thinking: high)"]

    async def test_thinking_off_is_not_mentioned(self) -> None:
        thinker = _model("anthropic", "thinker", reasoning=True)
        controller, deps = _controller(FakeSession(cycle_result=_CycleResult(thinker, "off")))
        await controller.cycle_model("forward")
        assert deps.statuses == ["Switched to thinker"]

    async def test_nothing_to_cycle_says_so(self) -> None:
        controller, deps = _controller(FakeSession(cycle_result=None))
        await controller.cycle_model("forward")
        assert deps.statuses == ["Only one model available"]

    async def test_nothing_to_cycle_within_a_scope_says_so_differently(self) -> None:
        controller, deps = _controller(FakeSession(cycle_result=None, scoped=[ScopedModel(ALPHA)]))
        await controller.cycle_model("forward")
        assert deps.statuses == ["Only one model in scope"]

    async def test_a_failed_switch_is_reported_rather_than_raised(self) -> None:
        controller, deps = _controller(FakeSession(cycle_error="no key for openai"))
        await controller.cycle_model("forward")
        assert deps.errors == ["no key for openai"]
        assert deps.statuses == []


class TestShowModelSelector:
    async def test_it_opens_an_overlay_over_the_available_models(self) -> None:
        controller, deps = _controller(FakeSession(models=[ALPHA, BETA]))
        await controller.show_model_selector()
        assert deps.component is not None
        rendered = "\n".join(deps.component.render(80))
        assert "alpha" in rendered
        assert "beta" in rendered

    async def test_the_search_box_is_what_gets_the_focus(self) -> None:
        controller, deps = _controller(FakeSession(models=[ALPHA, BETA]))
        await controller.show_model_selector()
        assert deps.focus is deps.component, "the overlay must handle its own input"

    async def test_with_no_registry_the_overlay_opens_empty(self) -> None:
        """The registry is 7.11's. Until then there is nothing to list, and the
        overlay says so rather than the app refusing to open it."""
        session = FakeSession()
        session.model_registry = None
        controller, deps = _controller(session)
        await controller.show_model_selector()
        assert "No matching models" in "\n".join(deps.component.render(80))

    async def test_an_unreachable_registry_is_an_empty_list(self) -> None:
        controller, deps = _controller(FakeSession(registry=FakeRegistry([], fails=True)))
        await controller.show_model_selector()
        assert deps.component is not None

    async def test_choosing_a_model_applies_it_and_closes_the_overlay(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        controller, deps = _controller(session)
        await controller.show_model_selector()

        deps.component.handle_input("\x1b[B")  # down, onto beta
        deps.component.handle_input("\r")
        await asyncio.sleep(0)

        assert session.set_models == [BETA]
        assert deps.closes == 1
        assert deps.statuses == ["Model: beta"]

    async def test_a_refused_model_reports_the_error_and_still_closes(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        session.set_model_error = "No API key for openai/beta"
        controller, deps = _controller(session)
        await controller.show_model_selector()

        deps.component.handle_input("\x1b[B")
        deps.component.handle_input("\r")
        await asyncio.sleep(0)

        assert deps.errors == ["No API key for openai/beta"]
        assert deps.closes == 1

    async def test_escape_closes_without_switching(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        controller, deps = _controller(session)
        await controller.show_model_selector()
        deps.component.handle_input("\x1b")
        assert (deps.closes, session.set_models) == (1, [])

    async def test_a_search_term_opens_the_overlay_filtered(self) -> None:
        controller, deps = _controller(FakeSession(models=[ALPHA, BETA]))
        await controller.show_model_selector("beta")
        rendered = "\n".join(deps.component.render(80))
        assert "beta" in rendered
        assert "alpha" not in rendered


class TestFindExactModelMatch:
    async def test_it_resolves_a_canonical_reference(self) -> None:
        controller, _ = _controller(FakeSession(models=[ALPHA, BETA]))
        assert await controller.find_exact_model_match("openai/beta") is BETA

    async def test_a_scope_narrows_what_can_be_matched(self) -> None:
        controller, _ = _controller(FakeSession(models=[ALPHA, BETA], scoped=[ScopedModel(ALPHA)]))
        assert await controller.find_exact_model_match("openai/beta") is None

    async def test_an_unknown_reference_resolves_to_nothing(self) -> None:
        controller, _ = _controller(FakeSession(models=[ALPHA, BETA]))
        assert await controller.find_exact_model_match("gpt-9") is None


class TestProviderCount:
    async def test_it_counts_distinct_providers(self) -> None:
        controller, deps = _controller(FakeSession(models=[ALPHA, BETA, _model("openai", "gamma")]))
        await controller.update_available_provider_count()
        assert deps.provider_counts == [2]

    async def test_with_nothing_available_it_is_zero(self) -> None:
        controller, deps = _controller(FakeSession(models=[]))
        await controller.update_available_provider_count()
        assert deps.provider_counts == [0]


class TestScopedModelsSelector:
    async def test_nothing_available_means_nothing_to_configure(self) -> None:
        controller, deps = _controller(FakeSession(models=[]))
        await controller.show_models_selector()
        assert deps.statuses == ["No models available"]
        assert deps.component is None

    async def test_it_opens_over_every_available_model(self) -> None:
        controller, deps = _controller(FakeSession(models=[ALPHA, BETA]))
        await controller.show_models_selector()
        rendered = "\n".join(deps.component.render(80))
        assert "alpha" in rendered
        assert "beta" in rendered

    async def test_a_toggle_narrows_the_session_scope(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        controller, deps = _controller(session)
        await controller.show_models_selector()

        deps.component.handle_input("\r")  # toggle the first model
        await asyncio.sleep(0)

        assert session.scoped_writes, "the toggle never reached the session"
        assert [scoped.model.id for scoped in session.scoped_writes[-1]] == ["alpha"]

    async def test_saving_writes_the_patterns_to_settings(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        controller, deps = _controller(session)
        await controller.show_models_selector()

        deps.component.handle_input("\r")  # narrow to alpha
        deps.component.handle_input("\x13")  # Ctrl+S
        await asyncio.sleep(0)

        assert session.settings_manager.get_enabled_models() == ["anthropic/alpha"]
        assert "saved to settings" in deps.statuses[-1]

    async def test_saving_everything_clears_the_filter_rather_than_listing_it(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        session.settings_manager.set_enabled_models(["anthropic/alpha"])
        controller, deps = _controller(session)
        await controller.show_models_selector()

        deps.component.handle_input("\x01")  # Ctrl+A — enable all
        deps.component.handle_input("\x13")  # Ctrl+S
        await asyncio.sleep(0)

        assert session.settings_manager.get_enabled_models() is None

    async def test_selecting_every_model_by_hand_still_clears_the_filter(self) -> None:
        """Reached by toggling rather than by Ctrl+A, so the enabled set is an
        explicit list of everything rather than `None` — and a list of everything
        is not a filter, in the session or in `settings.json`."""
        session = FakeSession(models=[ALPHA, BETA])
        controller, deps = _controller(session)
        await controller.show_models_selector()

        deps.component.handle_input("\r")  # toggle alpha on
        deps.component.handle_input("\x1b[B")
        deps.component.handle_input("\r")  # toggle beta on — now both
        await asyncio.sleep(0)

        assert session.scoped_writes[-1] == [], "an all-inclusive scope was applied as a filter"

        deps.component.handle_input("\x13")  # Ctrl+S
        await asyncio.sleep(0)
        assert session.settings_manager.get_enabled_models() is None

    async def test_it_opens_on_the_persisted_patterns(self) -> None:
        session = FakeSession(models=[ALPHA, BETA])
        session.settings_manager.set_enabled_models(["anthropic/alpha"])
        controller, deps = _controller(session)
        await controller.show_models_selector()
        rendered = "\n".join(deps.component.render(80))
        assert "all enabled" not in rendered, "a persisted scope opened as though unfiltered"

    async def test_escape_closes_it(self) -> None:
        controller, deps = _controller(FakeSession(models=[ALPHA, BETA]))
        await controller.show_models_selector()
        deps.component.handle_input("\x1b")
        assert deps.closes == 1


class TestAnthropicSubscriptionWarning:
    class _OauthStorage:
        def get(self, provider: str) -> Any:
            class Credential:
                type = "oauth"

            return Credential()

    def _registry_with_oauth(self) -> Any:
        registry = FakeRegistry([ALPHA])
        registry.auth_storage = self._OauthStorage()  # type: ignore[attr-defined]
        return registry

    async def test_oauth_credentials_produce_the_warning(self) -> None:
        controller, deps = _controller(FakeSession(registry=self._registry_with_oauth()))
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        assert deps.warnings == [ANTHROPIC_SUBSCRIPTION_AUTH_WARNING]

    async def test_it_is_said_once_per_run(self) -> None:
        controller, deps = _controller(FakeSession(registry=self._registry_with_oauth()))
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        assert len(deps.warnings) == 1

    async def test_a_non_anthropic_model_is_none_of_its_business(self) -> None:
        controller, deps = _controller(FakeSession(registry=self._registry_with_oauth()))
        await controller.maybe_warn_about_anthropic_subscription_auth(BETA)
        assert deps.warnings == []

    async def test_the_user_can_switch_it_off(self) -> None:
        session = FakeSession(registry=self._registry_with_oauth())
        session.settings_manager.set_warnings(WarningSettings(anthropic_extra_usage=False))
        controller, deps = _controller(session)
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        assert deps.warnings == []

    async def test_a_subscription_api_key_produces_the_warning_too(self) -> None:
        registry = FakeRegistry([ALPHA])

        async def get_api_key_for_provider(provider: str) -> str:
            return "sk-ant-oat01-secret"

        registry.get_api_key_for_provider = get_api_key_for_provider  # type: ignore[attr-defined]
        controller, deps = _controller(FakeSession(registry=registry))
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        assert deps.warnings == [ANTHROPIC_SUBSCRIPTION_AUTH_WARNING]

    async def test_an_ordinary_api_key_does_not(self) -> None:
        registry = FakeRegistry([ALPHA])

        async def get_api_key_for_provider(provider: str) -> str:
            return "sk-ant-api03-secret"

        registry.get_api_key_for_provider = get_api_key_for_provider  # type: ignore[attr-defined]
        controller, deps = _controller(FakeSession(registry=registry))
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        assert deps.warnings == []

    async def test_a_failed_auth_lookup_is_swallowed(self) -> None:
        registry = FakeRegistry([ALPHA])

        async def get_api_key_for_provider(provider: str) -> str:
            raise RuntimeError("keychain locked")

        registry.get_api_key_for_provider = get_api_key_for_provider  # type: ignore[attr-defined]
        controller, deps = _controller(FakeSession(registry=registry))
        await controller.maybe_warn_about_anthropic_subscription_auth(ALPHA)
        assert (deps.warnings, deps.errors) == ([], [])
