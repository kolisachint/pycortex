"""Tests for `cortex.ai.util.overflow` (port of overflow.ts)."""

from __future__ import annotations

from cortex.ai.types import AssistantMessage, Usage
from cortex.ai.util.overflow import isContextOverflow


def _create_error_message(error_message: str) -> AssistantMessage:
    """Create an AssistantMessage with stop_reason='error' for testing."""
    return AssistantMessage(
        role="assistant",
        content=[],
        api="openai-completions",
        provider="ollama",
        model="qwen3.5:35b",
        usage=Usage(
            input=0,
            output=0,
            cache_read=0,
            cache_write=0,
            total_tokens=0,
            cost={
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 0,
            },
        ),
        stop_reason="error",
        error_message=error_message,
        timestamp=1,
    )


def _create_length_stop_message(
    input_tokens: int, cache_read: int, output: int
) -> AssistantMessage:
    """Create an AssistantMessage with stop_reason='length' for testing."""
    return AssistantMessage(
        role="assistant",
        content=[],
        api="openai-completions",
        provider="xiaomi",
        model="mimo-v2.5-pro",
        usage=Usage(
            input=input_tokens,
            output=output,
            cache_read=cache_read,
            cache_write=0,
            total_tokens=input_tokens + cache_read + output,
            cost={
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_write": 0,
                "total": 0,
            },
        ),
        stop_reason="length",
        timestamp=1,
    )


def test_detects_explicit_ollama_prompt_too_long_errors() -> None:
    message = _create_error_message(
        "400 `prompt too long; exceeded max context length by 100918 tokens`"
    )
    assert isContextOverflow(message, 32768) is True


def test_detects_together_ai_context_length_errors() -> None:
    message = _create_error_message(
        "400 The input (516368 tokens) is longer than the model's context length (262144 tokens)."
    )
    assert isContextOverflow(message, 262144) is True


def test_does_not_treat_generic_non_overflow_ollama_errors_as_overflow() -> None:
    message = _create_error_message("500 `model runner crashed unexpectedly`")
    assert isContextOverflow(message, 32768) is False


def test_does_not_treat_bedrock_throttling_too_many_tokens_as_overflow() -> None:
    message = _create_error_message(
        "Throttling error: Too many tokens, please wait before trying again."
    )
    assert isContextOverflow(message, 200000) is False


def test_does_not_treat_bedrock_service_unavailable_as_overflow() -> None:
    message = _create_error_message("Service unavailable: The service is temporarily unavailable.")
    assert isContextOverflow(message, 200000) is False


def test_does_not_treat_generic_rate_limit_errors_as_overflow() -> None:
    message = _create_error_message("Rate limit exceeded, please retry after 30 seconds.")
    assert isContextOverflow(message, 200000) is False


def test_does_not_treat_http_429_style_errors_as_overflow() -> None:
    message = _create_error_message("Too many requests. Please slow down.")
    assert isContextOverflow(message, 200000) is False


def test_detects_xiaomi_style_overflow_length_stop_with_zero_output_and_filled_context() -> None:
    message = _create_length_stop_message(58, 1048512, 0)
    assert isContextOverflow(message, 1048576) is True


def test_does_not_treat_normal_length_stops_with_output_as_overflow() -> None:
    message = _create_length_stop_message(1000, 0, 4096)
    assert isContextOverflow(message, 200000) is False


def test_does_not_treat_length_stops_far_below_context_as_overflow() -> None:
    message = _create_length_stop_message(100, 0, 0)
    assert isContextOverflow(message, 200000) is False
