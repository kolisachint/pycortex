"""Tests for tool argument validation.

Mechanical port of hoocode's `packages/ai/test/validation.test.ts`.
"""

from __future__ import annotations

import pytest
from cortex.ai.types import Tool, ToolCall
from cortex.ai.util.validation import validate_tool_arguments


def _create_tool_call_with_plain_schema(schema: dict[str, object], value: object) -> tuple[Tool, ToolCall]:
    """Helper to create a Tool and ToolCall with a plain JSON schema."""
    tool = Tool(
        name="echo",
        description="Echo tool",
        parameters={
            "type": "object",
            "properties": {
                "value": schema,
            },
            "required": ["value"],
        },
    )

    tool_call = ToolCall(
        type="toolCall",
        id="tool-1",
        name="echo",
        arguments={"value": value},
    )

    return tool, tool_call


class TestValidateToolArguments:
    def test_coerces_plain_json_schemas_with_primitive_rules(self):
        """Coerces serialized plain JSON schemas with AJV-compatible primitive rules."""
        passing_cases: list[tuple[dict[str, object], object, object]] = [
            ({"type": "number"}, "42", 42),
            ({"type": "number"}, True, 1),
            ({"type": "number"}, None, 0),
            ({"type": "integer"}, "42", 42),
            ({"type": "boolean"}, "true", True),
            ({"type": "boolean"}, "false", False),
            ({"type": "boolean"}, 1, True),
            ({"type": "boolean"}, 0, False),
            ({"type": "string"}, None, ""),
            ({"type": "string"}, True, "true"),
            ({"type": "null"}, "", None),
            ({"type": "null"}, 0, None),
            ({"type": "null"}, False, None),
            ({"type": ["number", "string"]}, "1", "1"),
            ({"type": ["boolean", "number"]}, "1", 1),
        ]

        for schema, input_val, expected in passing_cases:
            tool, tool_call = _create_tool_call_with_plain_schema(schema, input_val)
            result = validate_tool_arguments(tool, tool_call)
            assert result == {"value": expected}, f"Failed for schema={schema}, input={input_val}"

    def test_rejects_invalid_coercions(self):
        """Rejects invalid coercions for serialized plain JSON schemas."""
        failing_cases: list[tuple[dict[str, object], object]] = [
            ({"type": "boolean"}, "1"),
            ({"type": "boolean"}, "0"),
            ({"type": "null"}, "null"),
            ({"type": "integer"}, "42.1"),
        ]

        for schema, input_val in failing_cases:
            tool, tool_call = _create_tool_call_with_plain_schema(schema, input_val)
            with pytest.raises(Exception, match="Validation failed"):
                validate_tool_arguments(tool, tool_call)
