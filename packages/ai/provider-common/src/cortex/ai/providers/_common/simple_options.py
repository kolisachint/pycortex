from __future__ import annotations

from typing import Literal

from cortex.ai.types import (
    Model,
    SimpleStreamOptions,
    StreamOptions,
    ThinkingBudgets,
    ThinkingLevel,
)


def build_base_options(
    model: Model,
    options: SimpleStreamOptions | None = None,
    api_key: str | None = None,
) -> StreamOptions:
    return StreamOptions(
        temperature=options.temperature if options else None,
        max_tokens=(
            options.max_tokens
            if options and options.max_tokens is not None
            else (min(model.max_tokens, 32000) if model.max_tokens > 0 else None)
        ),
        signal=options.signal if options else None,
        api_key=api_key or (options.api_key if options else None),
        transport=options.transport if options else None,
        cache_retention=options.cache_retention if options else None,
        session_id=options.session_id if options else None,
        headers=options.headers if options else None,
        on_payload=options.on_payload if options else None,
        on_response=options.on_response if options else None,
        timeout_ms=options.timeout_ms if options else None,
        max_retries=options.max_retries if options else None,
        max_retry_delay_ms=options.max_retry_delay_ms if options else None,
        metadata=options.metadata if options else None,
        constrain_tool_calls=options.constrain_tool_calls if options else None,
    )


def clamp_reasoning(
    effort: ThinkingLevel | None,
) -> Literal["minimal", "low", "medium", "high"] | None:
    return "high" if effort == "xhigh" else effort


def adjust_max_tokens_for_thinking(
    base_max_tokens: int,
    model_max_tokens: int,
    reasoning_level: ThinkingLevel,
    custom_budgets: ThinkingBudgets | None = None,
) -> dict[str, int]:
    default_budgets: dict[str, int] = {
        "minimal": 1024,
        "low": 2048,
        "medium": 8192,
        "high": 16384,
    }
    budgets = dict(default_budgets)
    if custom_budgets is not None:
        for key in ("minimal", "low", "medium", "high"):
            value = getattr(custom_budgets, key)
            if value is not None:
                budgets[key] = value

    min_output_tokens = 1024
    level = clamp_reasoning(reasoning_level)
    assert level is not None
    thinking_budget = budgets[level]
    max_tokens = min(base_max_tokens + thinking_budget, model_max_tokens)

    if max_tokens <= thinking_budget:
        thinking_budget = max(0, max_tokens - min_output_tokens)

    return {"max_tokens": max_tokens, "thinking_budget": thinking_budget}
