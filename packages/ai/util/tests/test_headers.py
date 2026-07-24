"""Tests for header utilities.

Mechanical port of hoocode's headers.ts tests (if any).
"""

from cortex.ai.util.headers import headers_to_record


def test_headers_to_record_converts_mapping() -> None:
    """headers_to_record should convert a mapping to a dict."""
    headers = {"Content-Type": "application/json", "Authorization": "Bearer token"}
    result = headers_to_record(headers)
    assert result == {"Content-Type": "application/json", "Authorization": "Bearer token"}


def test_headers_to_record_handles_empty_mapping() -> None:
    """headers_to_record should handle empty mappings."""
    result = headers_to_record({})
    assert result == {}


def test_headers_to_record_returns_dict() -> None:
    """headers_to_record should return a dict."""
    result = headers_to_record({"key": "value"})
    assert isinstance(result, dict)
