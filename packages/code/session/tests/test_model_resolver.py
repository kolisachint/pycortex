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
    find_exact_model_reference_match,
    is_alias,
    parse_model_pattern,
    resolve_model_scope,
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
