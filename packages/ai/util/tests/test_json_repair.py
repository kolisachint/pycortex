"""Tests for JSON repair utilities.

Mechanical port of hoocode's json-parse.ts tests.
"""

import pytest
from cortex.ai.util.json_repair import (
    parse_json_with_repair,
    parse_streaming_json,
    repair_json,
)


class TestRepairJson:
    def test_repairs_raw_control_characters(self) -> None:
        """repair_json should escape raw control characters inside strings."""
        # Tab character inside string
        json_str = '{"key": "value\twith\ttabs"}'
        repaired = repair_json(json_str)
        assert "\\t" in repaired
        assert "\t" not in repaired.split('"')[1]

    def test_repairs_newlines_in_strings(self) -> None:
        """repair_json should escape newlines inside strings."""
        json_str = '{"key": "line1\nline2"}'
        repaired = repair_json(json_str)
        assert "\\n" in repaired
        assert "\n" not in repaired.split('"')[1]

    def test_preserves_valid_json(self) -> None:
        """repair_json should not modify valid JSON."""
        valid_json = '{"key": "value", "number": 42}'
        repaired = repair_json(valid_json)
        assert repaired == valid_json

    def test_doubles_backslashes_before_invalid_escapes(self) -> None:
        """repair_json should double backslashes before invalid escape characters."""
        json_str = '{"key": "value\\xinvalid"}'
        repaired = repair_json(json_str)
        assert "\\\\" in repaired


class TestParseJsonWithRepair:
    def test_parses_valid_json(self) -> None:
        """parse_json_with_repair should parse valid JSON."""
        valid_json = '{"key": "value"}'
        result = parse_json_with_repair(valid_json)
        assert result == {"key": "value"}

    def test_repairs_and_parses_invalid_json(self) -> None:
        """parse_json_with_repair should repair and parse invalid JSON."""
        # JSON with raw tab
        invalid_json = '{"key": "value\twith\ttabs"}'
        result = parse_json_with_repair(invalid_json)
        assert result == {"key": "value\twith\ttabs"}

    def test_raises_on_unrepairable_json(self) -> None:
        """parse_json_with_repair should raise ValueError for unrepairable JSON."""
        with pytest.raises(ValueError):
            parse_json_with_repair("not json at all")


class TestParseStreamingJson:
    def test_returns_empty_dict_for_none(self) -> None:
        """parse_streaming_json should return empty dict for None input."""
        result = parse_streaming_json(None)
        assert result == {}

    def test_returns_empty_dict_for_empty_string(self) -> None:
        """parse_streaming_json should return empty dict for empty string."""
        result = parse_streaming_json("")
        assert result == {}

    def test_returns_empty_dict_for_whitespace(self) -> None:
        """parse_streaming_json should return empty dict for whitespace-only string."""
        result = parse_streaming_json("   ")
        assert result == {}

    def test_parses_complete_json(self) -> None:
        """parse_streaming_json should parse complete JSON."""
        json_str = '{"key": "value"}'
        result = parse_streaming_json(json_str)
        assert result == {"key": "value"}

    def test_returns_empty_dict_for_invalid_json(self) -> None:
        """parse_streaming_json should return empty dict for invalid JSON."""
        result = parse_streaming_json("not json")
        assert result == {}

    def test_returns_dict_when_result_is_dict(self) -> None:
        """parse_streaming_json should return dict when parsed result is a dict."""
        json_str = '{"nested": {"key": "value"}}'
        result = parse_streaming_json(json_str)
        assert isinstance(result, dict)
        assert result == {"nested": {"key": "value"}}
