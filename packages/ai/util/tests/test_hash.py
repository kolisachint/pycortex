"""Tests for hash utilities.

Mechanical port of hoocode's hash.ts tests (if any).
"""

from cortex.ai.util.hash import short_hash


def test_short_hash_returns_string() -> None:
    """short_hash should return a string."""
    result = short_hash("test")
    assert isinstance(result, str)


def test_short_hash_is_deterministic() -> None:
    """short_hash should return the same value for the same input."""
    assert short_hash("hello") == short_hash("hello")


def test_short_hash_differs_for_different_inputs() -> None:
    """short_hash should return different values for different inputs."""
    assert short_hash("hello") != short_hash("world")


def test_short_hash_handles_empty_string() -> None:
    """short_hash should handle empty strings."""
    result = short_hash("")
    assert isinstance(result, str)
    assert len(result) > 0


def test_short_hash_handles_unicode() -> None:
    """short_hash should handle unicode characters."""
    result = short_hash("hello 🌍")
    assert isinstance(result, str)


def test_short_hash_returns_base36() -> None:
    """short_hash should return a base-36 encoded string."""
    result = short_hash("test")
    assert all(c in "0123456789abcdefghijklmnopqrstuvwxyz" for c in result)
