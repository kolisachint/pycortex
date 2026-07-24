"""Unit tests for resolve_cache_retention (HOOCODE_CACHE_RETENTION)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from cortex.ai.providers._common import resolve_cache_retention


@pytest.fixture(autouse=True)
def _clear_env() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    original = os.environ.get("HOOCODE_CACHE_RETENTION")
    os.environ.pop("HOOCODE_CACHE_RETENTION", None)
    try:
        yield
    finally:
        if original is not None:
            os.environ["HOOCODE_CACHE_RETENTION"] = original
        else:
            os.environ.pop("HOOCODE_CACHE_RETENTION", None)


def test_defaults_to_long() -> None:
    assert resolve_cache_retention() == "long"


def test_explicit_argument_wins_over_env() -> None:
    os.environ["HOOCODE_CACHE_RETENTION"] = "short"
    assert resolve_cache_retention("none") == "none"


def test_reads_short_from_env() -> None:
    os.environ["HOOCODE_CACHE_RETENTION"] = "short"
    assert resolve_cache_retention() == "short"


def test_reads_long_from_env() -> None:
    os.environ["HOOCODE_CACHE_RETENTION"] = "long"
    assert resolve_cache_retention() == "long"


def test_reads_none_from_env() -> None:
    os.environ["HOOCODE_CACHE_RETENTION"] = "none"
    assert resolve_cache_retention() == "none"


def test_ignores_unknown_env_value() -> None:
    os.environ["HOOCODE_CACHE_RETENTION"] = "forever"
    assert resolve_cache_retention() == "long"
