"""Unit tests for simple-options helpers (build_base_options, thinking budgets)."""

from __future__ import annotations

from cortex.ai.providers._common import (
    adjust_max_tokens_for_thinking,
    build_base_options,
    clamp_reasoning,
)
from cortex.ai.types import Model, SimpleStreamOptions, ThinkingBudgets


def make_model(max_tokens: int) -> Model:
    return Model(
        id="test-model",
        name="Test Model",
        api="openai-completions",
        provider="test-openai-completions",
        base_url="https://example.com/v1",
        reasoning=False,
        input=["text"],
        cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        context_window=128000,
        max_tokens=max_tokens,
    )


def test_build_base_options_defaults_max_tokens_to_min_of_model_and_cap() -> None:
    result = build_base_options(make_model(64000))
    assert result.max_tokens == 32000


def test_build_base_options_uses_model_max_when_below_cap() -> None:
    result = build_base_options(make_model(4096))
    assert result.max_tokens == 4096


def test_build_base_options_none_when_model_max_is_zero() -> None:
    result = build_base_options(make_model(0))
    assert result.max_tokens is None


def test_build_base_options_prefers_option_max_tokens() -> None:
    options = SimpleStreamOptions(max_tokens=100)
    result = build_base_options(make_model(64000), options)
    assert result.max_tokens == 100


def test_build_base_options_copies_scalar_options() -> None:
    options = SimpleStreamOptions(
        temperature=0.5,
        cache_retention="long",
        session_id="s1",
        headers={"X-Test": "1"},
        timeout_ms=1000,
        max_retries=3,
        max_retry_delay_ms=60000,
        metadata={"user_id": "u"},
        constrain_tool_calls=True,
    )
    result = build_base_options(make_model(4096), options)
    assert result.temperature == 0.5
    assert result.cache_retention == "long"
    assert result.session_id == "s1"
    assert result.headers == {"X-Test": "1"}
    assert result.timeout_ms == 1000
    assert result.max_retries == 3
    assert result.max_retry_delay_ms == 60000
    assert result.metadata == {"user_id": "u"}
    assert result.constrain_tool_calls is True


def test_build_base_options_api_key_argument_wins() -> None:
    options = SimpleStreamOptions(api_key="from-options")
    result = build_base_options(make_model(4096), options, "from-arg")
    assert result.api_key == "from-arg"


def test_build_base_options_falls_back_to_options_api_key() -> None:
    options = SimpleStreamOptions(api_key="from-options")
    result = build_base_options(make_model(4096), options)
    assert result.api_key == "from-options"


def test_clamp_reasoning_downgrades_xhigh_to_high() -> None:
    assert clamp_reasoning("xhigh") == "high"


def test_clamp_reasoning_passes_through() -> None:
    assert clamp_reasoning("medium") == "medium"
    assert clamp_reasoning(None) is None


def test_adjust_max_tokens_for_thinking_default_budget() -> None:
    result = adjust_max_tokens_for_thinking(4096, 200000, "medium")
    assert result == {"max_tokens": 4096 + 8192, "thinking_budget": 8192}


def test_adjust_max_tokens_for_thinking_custom_budget() -> None:
    result = adjust_max_tokens_for_thinking(4096, 200000, "low", ThinkingBudgets(low=5000))
    assert result == {"max_tokens": 4096 + 5000, "thinking_budget": 5000}


def test_adjust_max_tokens_for_thinking_clamps_to_model_max() -> None:
    result = adjust_max_tokens_for_thinking(4096, 5000, "high")
    # max_tokens = min(4096 + 16384, 5000) = 5000, and 5000 <= 16384 so budget
    # shrinks to max(0, 5000 - 1024) = 3976
    assert result == {"max_tokens": 5000, "thinking_budget": 3976}


def test_adjust_max_tokens_for_thinking_xhigh_uses_high_budget() -> None:
    result = adjust_max_tokens_for_thinking(4096, 200000, "xhigh")
    assert result == {"max_tokens": 4096 + 16384, "thinking_budget": 16384}
