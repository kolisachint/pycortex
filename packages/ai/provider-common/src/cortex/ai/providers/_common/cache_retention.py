from __future__ import annotations

import os

from cortex.ai.types import CacheRetention


def resolve_cache_retention(cache_retention: CacheRetention | None = None) -> CacheRetention:
    """Resolve cache retention preference.

    Default is "long" — Anthropic 1h / OpenAI 24h prompt cache. Re-using cached
    prompts past the 5-minute short-TTL window is roughly 10x cheaper than the
    un-cached read. Models that don't advertise ``supportsLongCacheRetention``
    silently fall back to the provider's default ephemeral cache, so defaulting
    to "long" is safe.

    Opt out with HOOCODE_CACHE_RETENTION=short (5-min ephemeral) or =none
    (disable caching).
    """
    if cache_retention:
        return cache_retention
    env = os.environ.get("HOOCODE_CACHE_RETENTION")
    if env in ("short", "long", "none"):
        return env
    return "long"
