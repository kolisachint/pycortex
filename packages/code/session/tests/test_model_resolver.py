"""Tests for the model-reference half of ``model-resolver.ts``.

Two things are worth pinning here and neither is "does substring search work":

* **ambiguity is answered with nothing.** A bare id served by two providers must
  not resolve, because picking one silently is how a user ends up billed by a
  provider they never named;
* **glob scope does not cross the provider separator.** The TS matches with
  minimatch, whose ``*`` stops at ``/``, and it is the reason ``resolveModelScope``
  tries every pattern against a bare id as well. Swap in a matcher whose ``*``
  crosses and the second attempt becomes dead code — invisible unless something
  asserts on it.
"""

from __future__ import annotations

import pytest
from cortex.ai.types import Model
from cortex.code.session import (
    DEFAULT_MODEL_PER_PROVIDER,
    DEFAULT_THINKING_LEVEL,
    ScopedModel,
    find_exact_model_reference_match,
    find_initial_model,
    is_alias,
    parse_model_pattern,
    resolve_cli_model,
    resolve_model_scope,
    restore_model_from_session,
)


def _model(provider: str, model_id: str, name: str | None = None) -> Model:
    return Model(
        id=model_id,
        name=name if name is not None else model_id,
        api="anthropic-messages",
        provider=provider,
        base_url="https://example.invalid",
        reasoning=False,
        input=["text"],
        cost={"input": 0.0, "output": 0.0},
        context_window=1000,
        max_tokens=100,
    )


class Registry:
    """The one method scope resolution asks for."""

    def __init__(self, models: list[Model]) -> None:
        self._models = models

    async def get_available(self) -> list[Model]:
        return list(self._models)


ANTHROPIC_SONNET = _model("anthropic", "claude-sonnet-4-5")
ANTHROPIC_SONNET_DATED = _model("anthropic", "claude-sonnet-4-5-20250929")
COPILOT_SONNET = _model("github-copilot", "claude-sonnet-4.5")
OPENAI_MINI = _model("openai", "gpt-5-mini", "GPT-5 Mini")

ALL_MODELS = [ANTHROPIC_SONNET, ANTHROPIC_SONNET_DATED, COPILOT_SONNET, OPENAI_MINI]


class TestIsAlias:
    def test_bare_id_is_an_alias(self) -> None:
        assert is_alias("claude-sonnet-4-5") is True

    def test_latest_suffix_is_an_alias(self) -> None:
        assert is_alias("gpt-5-latest") is True

    def test_eight_digit_date_suffix_is_not(self) -> None:
        assert is_alias("claude-sonnet-4-5-20250929") is False

    def test_a_shorter_number_is_not_a_date(self) -> None:
        assert is_alias("model-2025") is True


class TestFindExactModelReferenceMatch:
    def test_canonical_reference(self) -> None:
        found = find_exact_model_reference_match("anthropic/claude-sonnet-4-5", ALL_MODELS)
        assert found is ANTHROPIC_SONNET

    def test_canonical_reference_is_case_insensitive(self) -> None:
        found = find_exact_model_reference_match("Anthropic/Claude-Sonnet-4-5", ALL_MODELS)
        assert found is ANTHROPIC_SONNET

    def test_bare_id_when_only_one_provider_serves_it(self) -> None:
        assert find_exact_model_reference_match("gpt-5-mini", ALL_MODELS) is OPENAI_MINI

    def test_bare_id_served_by_two_providers_is_ambiguous(self) -> None:
        duplicate = _model("bedrock", "gpt-5-mini")
        assert find_exact_model_reference_match("gpt-5-mini", [OPENAI_MINI, duplicate]) is None

    def test_empty_reference(self) -> None:
        assert find_exact_model_reference_match("   ", ALL_MODELS) is None

    def test_unknown_reference(self) -> None:
        assert find_exact_model_reference_match("no-such-model", ALL_MODELS) is None


class TestParseModelPattern:
    def test_thinking_suffix_is_split_off(self) -> None:
        result = parse_model_pattern("claude-sonnet-4-5:high", ALL_MODELS)
        assert result.model is ANTHROPIC_SONNET
        assert result.thinking_level == "high"
        assert result.warning is None

    def test_an_invalid_suffix_warns_and_falls_back(self) -> None:
        result = parse_model_pattern("claude-sonnet-4-5:enormous", ALL_MODELS)
        assert result.model is ANTHROPIC_SONNET
        assert result.thinking_level is None
        assert result.warning is not None
        assert "enormous" in result.warning

    def test_strict_mode_refuses_an_invalid_suffix(self) -> None:
        result = parse_model_pattern(
            "claude-sonnet-4-5:enormous", ALL_MODELS, allow_invalid_thinking_level_fallback=False
        )
        assert result.model is None

    def test_a_colon_in_the_id_itself_is_matched_whole(self) -> None:
        exacto = _model("openrouter", "some-model:exacto")
        result = parse_model_pattern("some-model:exacto", [exacto])
        assert result.model is exacto
        assert result.thinking_level is None

    def test_partial_match_prefers_the_alias_over_the_dated_release(self) -> None:
        result = parse_model_pattern("sonnet-4-5", [ANTHROPIC_SONNET_DATED, ANTHROPIC_SONNET])
        assert result.model is ANTHROPIC_SONNET

    def test_partial_match_falls_back_to_the_newest_dated_release(self) -> None:
        older = _model("anthropic", "claude-sonnet-4-5-20250101")
        result = parse_model_pattern("sonnet-4-5", [older, ANTHROPIC_SONNET_DATED])
        assert result.model is ANTHROPIC_SONNET_DATED

    def test_separators_are_normalised_when_nothing_matched(self) -> None:
        # "claude-sonnet-4-5" is not a substring of copilot's "claude-sonnet-4.5",
        # and providers really do disagree like this.
        result = parse_model_pattern("claude-sonnet-4-5", [COPILOT_SONNET])
        assert result.model is COPILOT_SONNET

    def test_matches_on_the_display_name_too(self) -> None:
        result = parse_model_pattern("GPT-5 Mini", [OPENAI_MINI])
        assert result.model is OPENAI_MINI


class TestResolveModelScope:
    @pytest.mark.asyncio
    async def test_plain_patterns_resolve_in_order(self) -> None:
        scoped = await resolve_model_scope(
            ["gpt-5-mini", "anthropic/claude-sonnet-4-5"], Registry(ALL_MODELS)
        )
        assert [item.model.id for item in scoped] == ["gpt-5-mini", "claude-sonnet-4-5"]

    @pytest.mark.asyncio
    async def test_a_pattern_that_matches_nothing_is_skipped(self) -> None:
        scoped = await resolve_model_scope(["no-such-model"], Registry(ALL_MODELS))
        assert scoped == []

    @pytest.mark.asyncio
    async def test_duplicates_are_dropped(self) -> None:
        scoped = await resolve_model_scope(
            ["gpt-5-mini", "openai/gpt-5-mini"], Registry(ALL_MODELS)
        )
        assert len(scoped) == 1

    @pytest.mark.asyncio
    async def test_a_thinking_suffix_is_carried_onto_the_scoped_model(self) -> None:
        scoped = await resolve_model_scope(["gpt-5-mini:high"], Registry(ALL_MODELS))
        assert [(item.model.id, item.thinking_level) for item in scoped] == [("gpt-5-mini", "high")]

    @pytest.mark.asyncio
    async def test_a_provider_glob_selects_that_provider(self) -> None:
        scoped = await resolve_model_scope(["anthropic/*"], Registry(ALL_MODELS))
        assert {item.model.provider for item in scoped} == {"anthropic"}
        assert len(scoped) == 2

    @pytest.mark.asyncio
    async def test_a_bare_glob_matches_ids_across_providers(self) -> None:
        # This is the minimatch behaviour the caller depends on: `*sonnet*`
        # cannot cross the `/`, so it is the bare-id attempt that matches — and
        # it reaches every provider serving a sonnet.
        scoped = await resolve_model_scope(["*sonnet*"], Registry(ALL_MODELS))
        assert {item.model.provider for item in scoped} == {"anthropic", "github-copilot"}

    @pytest.mark.asyncio
    async def test_a_glob_carries_its_thinking_suffix(self) -> None:
        scoped = await resolve_model_scope(["anthropic/*:medium"], Registry(ALL_MODELS))
        assert scoped
        assert all(item.thinking_level == "medium" for item in scoped)

    @pytest.mark.asyncio
    async def test_a_glob_matching_nothing_is_skipped(self) -> None:
        assert await resolve_model_scope(["nowhere/*"], Registry(ALL_MODELS)) == []


# ===========================================================================
# The startup half — `resolve_cli_model`, `find_initial_model`,
# `restore_model_from_session` (ported with 7.11)
# ===========================================================================

GPT_4O = _model("openai", "gpt-4o")
OPENROUTER_GPT_4O_EXTENDED = _model("openrouter", "openai/gpt-4o:extended")
OPENROUTER_QWEN = _model("openrouter", "qwen/qwen3-coder:exacto")
ZAI_GLM = _model("zai", "glm-5")
GATEWAY_GLM = _model("vercel-ai-gateway", "zai/glm-5")

#: The TS's `allModels`: one anthropic model, one openai model, and two
#: openrouter models whose ids contain both a slash and a colon. There is
#: deliberately no `openai/gpt-4o:extended` — the tests below turn on the fact
#: that the only model with that id belongs to *openrouter*.
CLI_MODELS = [
    ANTHROPIC_SONNET,
    GPT_4O,
    OPENROUTER_QWEN,
    OPENROUTER_GPT_4O_EXTENDED,
]


class StartupRegistry:
    """The four methods startup resolution asks for.

    ``auth_providers`` is which providers have credentials; every model is
    findable, and only authed ones are reported available. That split is the
    point of most of these tests.
    """

    def __init__(self, models: list[Model], auth_providers: list[str] | None = None) -> None:
        self._models = list(models)
        self._auth_providers = list(auth_providers or [])

    def get_all(self) -> list[Model]:
        return list(self._models)

    def find(self, provider: str, model_id: str) -> Model | None:
        return next((m for m in self._models if m.provider == provider and m.id == model_id), None)

    def has_configured_auth(self, model: Model) -> bool:
        return model.provider in self._auth_providers

    async def get_available(self) -> list[Model]:
        return [m for m in self._models if m.provider in self._auth_providers]


class TestResolveCliModel:
    def test_resolves_provider_slash_id_without_explicit_provider(self) -> None:
        result = resolve_cli_model(
            cli_model="openai/gpt-4o", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openai", "gpt-4o")

    def test_resolves_fuzzy_patterns_within_an_explicit_provider(self) -> None:
        result = resolve_cli_model(
            cli_provider="openai", cli_model="4o", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openai", "gpt-4o")

    def test_supports_pattern_colon_thinking(self) -> None:
        result = resolve_cli_model(
            cli_model="sonnet:high", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.error is None
        assert result.model is not None
        assert result.model.id == "claude-sonnet-4-5"
        assert result.thinking_level == "high"

    def test_prefers_exact_id_match_over_provider_inference(self) -> None:
        """``openai/gpt-4o:extended`` looks like a provider prefix and is an
        OpenRouter model id. The literal match has to win, or the user gets a
        different provider's model than the one they named."""
        result = resolve_cli_model(
            cli_model="openai/gpt-4o:extended", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == (
            "openrouter",
            "openai/gpt-4o:extended",
        )

    def test_does_not_strip_an_invalid_colon_suffix_as_a_thinking_level(self) -> None:
        """``:extended`` is not a thinking level, and must not be dropped to make
        ``gpt-4o`` match. With no such model on openai the id is kept and becomes
        a custom model on that provider — which is the TS's outcome too, and the
        reason ``resolve_cli_model`` passes
        ``allow_invalid_thinking_level_fallback=False``."""
        result = resolve_cli_model(
            cli_provider="openai",
            cli_model="gpt-4o:extended",
            model_registry=StartupRegistry(CLI_MODELS),
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openai", "gpt-4o:extended")

    def test_allows_custom_ids_for_explicit_providers_without_double_prefixing(self) -> None:
        result = resolve_cli_model(
            cli_provider="openrouter",
            cli_model="openrouter/openai/ghost-model",
            model_registry=StartupRegistry(CLI_MODELS),
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openrouter", "openai/ghost-model")

    def test_returns_a_clear_error_when_there_are_no_models(self) -> None:
        result = resolve_cli_model(
            cli_provider="openai", cli_model="gpt-4o", model_registry=StartupRegistry([])
        )
        assert result.model is None
        assert result.error is not None
        assert "No models available" in result.error

    def test_prefers_provider_split_over_a_gateway_model_with_a_matching_id(self) -> None:
        """With both a ``zai`` model ``glm-5`` and a gateway model ``zai/glm-5``,
        ``--model zai/glm-5`` means the zai one."""
        result = resolve_cli_model(
            cli_model="zai/glm-5",
            model_registry=StartupRegistry([*CLI_MODELS, ZAI_GLM, GATEWAY_GLM]),
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("zai", "glm-5")

    def test_resolves_provider_prefixed_fuzzy_patterns(self) -> None:
        result = resolve_cli_model(
            cli_model="openrouter/qwen", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.error is None
        assert result.model is not None
        assert (result.model.provider, result.model.id) == (
            "openrouter",
            "qwen/qwen3-coder:exacto",
        )

    def test_unknown_provider_is_an_error_naming_list_models(self) -> None:
        result = resolve_cli_model(
            cli_provider="nope", cli_model="anything", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.model is None
        assert result.error is not None
        assert "Unknown provider" in result.error

    def test_no_cli_model_resolves_to_nothing_without_an_error(self) -> None:
        result = resolve_cli_model(model_registry=StartupRegistry(CLI_MODELS))
        assert result.model is None
        assert result.error is None

    def test_an_unmatched_id_on_a_known_provider_becomes_a_custom_model(self) -> None:
        """``--model my-local-llama --provider openai`` is how a user points at an
        OpenAI-compatible endpoint the registry has never heard of: the id is
        theirs, every other field is borrowed from the provider's default."""
        result = resolve_cli_model(
            cli_provider="openai",
            cli_model="my-local-llama",
            model_registry=StartupRegistry(CLI_MODELS),
        )
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openai", "my-local-llama")
        assert result.model.name == "my-local-llama"
        assert result.warning is not None
        assert "Using custom model id" in result.warning

    def test_an_unmatched_id_with_no_provider_is_an_error(self) -> None:
        result = resolve_cli_model(
            cli_model="nothing-like-this", model_registry=StartupRegistry(CLI_MODELS)
        )
        assert result.model is None
        assert result.error is not None
        assert "not found" in result.error


class TestDefaultModelPerProvider:
    """The defaults track the model data, so a rename here is caught."""

    def test_openai_defaults(self) -> None:
        assert DEFAULT_MODEL_PER_PROVIDER["openai"] == "gpt-5.4"
        assert DEFAULT_MODEL_PER_PROVIDER["openai-codex"] == "gpt-5.5"

    def test_zai_minimax_and_cerebras_defaults(self) -> None:
        assert DEFAULT_MODEL_PER_PROVIDER["zai"] == "glm-5.1"
        assert DEFAULT_MODEL_PER_PROVIDER["minimax"] == "MiniMax-M2.7"
        assert DEFAULT_MODEL_PER_PROVIDER["minimax-cn"] == "MiniMax-M2.7"
        assert DEFAULT_MODEL_PER_PROVIDER["cerebras"] == "zai-glm-4.7"

    def test_ai_gateway_default(self) -> None:
        assert DEFAULT_MODEL_PER_PROVIDER["vercel-ai-gateway"] == "zai/glm-5.1"

    def test_every_default_names_a_real_built_in_model(self) -> None:
        """A default that names a model the provider does not have would make
        ``find_initial_model`` skip that provider silently."""
        from cortex.ai.models import get_models, get_providers

        known = set(get_providers())
        missing: list[str] = []
        for provider, model_id in DEFAULT_MODEL_PER_PROVIDER.items():
            if provider not in known:
                missing.append(f"{provider} (unknown provider)")
                continue
            if not any(m.id == model_id for m in get_models(provider)):
                missing.append(f"{provider}/{model_id}")
        assert missing == [], f"defaults naming no built-in model: {missing}"


class TestFindInitialModel:
    async def test_accepts_explicit_provider_custom_model_ids(self) -> None:
        result = await find_initial_model(
            cli_provider="openrouter",
            cli_model="openrouter/openai/ghost-model",
            scoped_models=[],
            is_continuing=False,
            model_registry=StartupRegistry(CLI_MODELS),
        )
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openrouter", "openai/ghost-model")

    async def test_selects_the_provider_default_when_available(self) -> None:
        gateway_default = _model("vercel-ai-gateway", "zai/glm-5.1")
        other = _model("openai", "some-other-model")
        result = await find_initial_model(
            scoped_models=[],
            is_continuing=False,
            model_registry=StartupRegistry(
                [other, gateway_default], ["openai", "vercel-ai-gateway"]
            ),
        )
        assert result.model is not None
        assert result.model.id == "zai/glm-5.1"

    async def test_honours_a_saved_default_when_that_provider_has_auth(self) -> None:
        result = await find_initial_model(
            scoped_models=[],
            is_continuing=False,
            default_provider="openai",
            default_model_id="gpt-4o",
            model_registry=StartupRegistry(CLI_MODELS, ["anthropic", "openai"]),
        )
        assert result.model is not None
        assert (result.model.provider, result.model.id) == ("openai", "gpt-4o")

    async def test_skips_a_saved_default_without_auth_and_auto_detects(self) -> None:
        """A stale default naming a provider with no key must not wedge selection
        — otherwise every turn says "no API key" while a working provider sits
        unused."""
        result = await find_initial_model(
            scoped_models=[],
            is_continuing=False,
            default_provider="anthropic",
            default_model_id="claude-sonnet-4-5",
            model_registry=StartupRegistry(CLI_MODELS, ["openai"]),
        )
        assert result.model is not None
        assert result.model.provider == "openai"

    async def test_falls_through_to_no_model_when_nothing_is_authed(self) -> None:
        result = await find_initial_model(
            scoped_models=[],
            is_continuing=False,
            default_provider="anthropic",
            default_model_id="claude-sonnet-4-5",
            model_registry=StartupRegistry(CLI_MODELS, []),
        )
        assert result.model is None
        assert result.thinking_level == DEFAULT_THINKING_LEVEL

    async def test_the_scope_wins_over_the_saved_default(self) -> None:
        result = await find_initial_model(
            scoped_models=[ScopedModel(model=GPT_4O, thinking_level="medium")],
            is_continuing=False,
            default_provider="anthropic",
            default_model_id="claude-sonnet-4-5",
            model_registry=StartupRegistry(CLI_MODELS, ["anthropic"]),
        )
        assert result.model is GPT_4O
        assert result.thinking_level == "medium"

    async def test_resuming_ignores_the_scope(self) -> None:
        """When continuing, the model the session left off on wins — so the scope
        is skipped and the saved default is consulted instead."""
        result = await find_initial_model(
            scoped_models=[ScopedModel(model=GPT_4O)],
            is_continuing=True,
            default_provider="anthropic",
            default_model_id="claude-sonnet-4-5",
            model_registry=StartupRegistry(CLI_MODELS, ["anthropic"]),
        )
        assert result.model is not None
        assert result.model.provider == "anthropic"

    async def test_a_cli_error_is_returned_rather_than_exiting(self) -> None:
        """The TS prints and calls ``process.exit(1)``; a leaf other modes import
        must not, so the error comes back for ``code/main`` to act on."""
        result = await find_initial_model(
            cli_provider="openai",
            cli_model="nothing-like-this-either",
            scoped_models=[],
            is_continuing=False,
            model_registry=StartupRegistry([ANTHROPIC_SONNET]),
        )
        assert result.model is None
        assert result.error is not None

    async def test_the_saved_thinking_level_is_applied_with_the_saved_default(self) -> None:
        result = await find_initial_model(
            scoped_models=[],
            is_continuing=False,
            default_provider="openai",
            default_model_id="gpt-4o",
            default_thinking_level="high",
            model_registry=StartupRegistry(CLI_MODELS, ["openai"]),
        )
        assert result.thinking_level == "high"


class TestRestoreModelFromSession:
    async def test_restores_the_saved_model_when_it_still_has_auth(self) -> None:
        result = await restore_model_from_session(
            "openai", "gpt-4o", None, False, StartupRegistry(CLI_MODELS, ["openai"])
        )
        assert result.model is GPT_4O
        assert result.fallback_message is None

    async def test_a_model_that_no_longer_exists_falls_back_and_says_why(self) -> None:
        result = await restore_model_from_session(
            "openai", "gone-model", None, False, StartupRegistry(CLI_MODELS, ["openai"])
        )
        assert result.model is not None
        assert result.fallback_message is not None
        assert "model no longer exists" in result.fallback_message

    async def test_a_model_whose_provider_lost_its_key_says_so_differently(self) -> None:
        """The two failures need different fixes — ``/model`` versus ``/login`` —
        so the message names which one happened."""
        result = await restore_model_from_session(
            "anthropic",
            "claude-sonnet-4-5",
            None,
            False,
            StartupRegistry(CLI_MODELS, ["openai"]),
        )
        assert result.fallback_message is not None
        assert "no auth configured" in result.fallback_message

    async def test_the_current_model_is_preferred_as_the_fallback(self) -> None:
        result = await restore_model_from_session(
            "openai", "gone-model", ANTHROPIC_SONNET, False, StartupRegistry(CLI_MODELS, ["openai"])
        )
        assert result.model is ANTHROPIC_SONNET
        assert result.fallback_message is not None
        assert "anthropic/claude-sonnet-4-5" in result.fallback_message

    async def test_no_models_available_yields_no_model_and_no_message(self) -> None:
        result = await restore_model_from_session(
            "openai", "gpt-4o", None, False, StartupRegistry(CLI_MODELS, [])
        )
        assert result.model is None
        assert result.fallback_message is None

    async def test_the_provider_default_is_preferred_when_falling_back(self) -> None:
        gateway_default = _model("vercel-ai-gateway", "zai/glm-5.1")
        other = _model("vercel-ai-gateway", "some-other")
        result = await restore_model_from_session(
            "openai",
            "gone-model",
            None,
            False,
            StartupRegistry([other, gateway_default], ["vercel-ai-gateway"]),
        )
        assert result.model is not None
        assert result.model.id == "zai/glm-5.1"

    async def test_messages_are_printed_only_when_asked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        await restore_model_from_session(
            "openai", "gpt-4o", None, False, StartupRegistry(CLI_MODELS, ["openai"])
        )
        assert capsys.readouterr().out == ""

        await restore_model_from_session(
            "openai", "gpt-4o", None, True, StartupRegistry(CLI_MODELS, ["openai"])
        )
        assert "Restored model: openai/gpt-4o" in capsys.readouterr().out
