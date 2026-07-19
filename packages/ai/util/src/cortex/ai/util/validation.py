"""Tool call argument validation — port of validation.ts.

Validates tool call arguments against JSON schemas, with type coercion
for primitive types (string→number, boolean↔number, etc.).
"""

from __future__ import annotations

import copy
from typing import Any

from cortex.ai.types import Tool, ToolCall

# Cache for compiled validators (schema id -> validator)
_validator_cache: dict[int, dict[str, Any]] = {}

# JSON Schema type names we handle for coercion
_PRIMITIVE_COERCIONS: dict[str, set[type]] = {
    "number": {str, bool, type(None)},
    "integer": {str, bool, type(None)},
    "boolean": {str, int, float},
    "string": {int, float, bool, type(None)},
    "null": {str, int, float, bool},
}


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _get_schema_types(schema: dict[str, Any]) -> list[str]:
    """Extract the 'type' field from a JSON schema, handling string and array forms."""
    t = schema.get("type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    return []


def _matches_json_type(value: Any, type_name: str) -> bool:
    """Check if *value* matches the given JSON schema type."""
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool) and value == int(value)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "null":
        return value is None
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    return False


def _coerce_primitive_by_type(value: Any, type_name: str) -> Any:
    """Attempt to coerce *value* to *type_name* using AJV-compatible rules."""
    if type_name == "number":
        if value is None:
            return 0
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
                if parsed != float("inf") and parsed != float("-inf") and parsed == parsed:
                    return parsed
            except ValueError:
                pass
        if isinstance(value, bool):
            return 1 if value else 0
        return value

    if type_name == "integer":
        if value is None:
            return 0
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
                if parsed == int(parsed):
                    return int(parsed)
            except ValueError:
                pass
        if isinstance(value, bool):
            return 1 if value else 0
        return value

    if type_name == "boolean":
        if value is None:
            return False
        if isinstance(value, str):
            if value == "true":
                return True
            if value == "false":
                return False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return True
            if value == 0:
                return False
        return value

    if type_name == "string":
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return value

    if type_name == "null":
        if value == "" or value == 0 or value is False:
            return None
        return value

    return value


def _coerce_with_json_schema(value: Any, schema: dict[str, Any]) -> Any:
    """Recursively coerce *value* according to a JSON schema."""
    next_value = value

    # Handle allOf
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for nested in all_of:
            next_value = _coerce_with_json_schema(next_value, nested)

    # Handle anyOf / oneOf
    for key in ("anyOf", "oneOf"):
        union_schemas = schema.get(key)
        if isinstance(union_schemas, list):
            next_value = _coerce_with_union_schema(next_value, union_schemas)

    # Apply primitive coercion for union types
    schema_types = _get_schema_types(schema)
    matches_union_member = len(schema_types) > 1 and any(
        _matches_json_type(next_value, t) for t in schema_types
    )
    if schema_types and not matches_union_member:
        for type_name in schema_types:
            candidate = _coerce_primitive_by_type(next_value, type_name)
            if candidate is not next_value:
                next_value = candidate
                break

    # Apply object coercion
    if "object" in schema_types and _is_record(next_value):
        _apply_schema_object_coercion(next_value, schema)

    # Apply array coercion
    if "array" in schema_types and isinstance(next_value, list):
        _apply_schema_array_coercion(next_value, schema)

    return next_value


def _apply_schema_object_coercion(value: dict[str, Any], schema: dict[str, Any]) -> None:
    """Coerce nested object properties according to schema."""
    properties = schema.get("properties", {})
    defined_keys = set(properties.keys())

    for key, prop_schema in properties.items():
        if key in value:
            value[key] = _coerce_with_json_schema(value[key], prop_schema)

    # Handle additionalProperties
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        for key, prop_value in list(value.items()):
            if key not in defined_keys:
                value[key] = _coerce_with_json_schema(prop_value, additional)


def _apply_schema_array_coercion(value: list[Any], schema: dict[str, Any]) -> None:
    """Coerce array items according to schema."""
    items = schema.get("items")
    if isinstance(items, list):
        for i, item_value in enumerate(value):
            if i < len(items):
                value[i] = _coerce_with_json_schema(item_value, items[i])
    elif isinstance(items, dict):
        for i, item_value in enumerate(value):
            value[i] = _coerce_with_json_schema(item_value, items)


def _coerce_with_union_schema(value: Any, schemas: list[dict[str, Any]]) -> Any:
    """Try each union schema and return the first that validates."""
    for schema in schemas:
        candidate = copy.deepcopy(value)
        coerced = _coerce_with_json_schema(candidate, schema)
        if _validate_against_schema(coerced, schema):
            return coerced
    return value


def _validate_against_schema(value: Any, schema: dict[str, Any]) -> bool:
    """Simple schema validation for union type checking."""
    schema_types = _get_schema_types(schema)

    if schema_types:
        matches = any(_matches_json_type(value, t) for t in schema_types)
        if not matches:
            return False

    # Check required properties for objects
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not all(key in value for key in required):
            return False

        # Check properties recursively
        properties = schema.get("properties", {})
        for key, prop_value in value.items():
            if key in properties:
                if not _validate_against_schema(prop_value, properties[key]):
                    return False

    return True


def validate_tool_call(tools: list[Tool], tool_call: ToolCall) -> dict[str, Any]:
    """Find a tool by name and validate the tool call arguments against its schema.

    Args:
        tools: Array of tool definitions.
        tool_call: The tool call from the LLM.

    Returns:
        The validated (and potentially coerced) arguments.

    Raises:
        Error if tool is not found or validation fails.
    """
    tool = next((t for t in tools if t.name == tool_call.name), None)
    if not tool:
        raise Error(f'Tool "{tool_call.name}" not found')
    return validate_tool_arguments(tool, tool_call)


def validate_tool_arguments(tool: Tool, tool_call: ToolCall) -> dict[str, Any]:
    """Validate tool call arguments against the tool's schema.

    Args:
        tool: The tool definition with schema.
        tool_call: The tool call from the LLM.

    Returns:
        The validated (and potentially coerced) arguments.

    Raises:
        Error with formatted message if validation fails.
    """
    args = copy.deepcopy(tool_call.arguments)

    # Apply coercion for non-TypeBox (plain JSON) schemas
    schema = tool.parameters
    if _is_record(schema):
        coerced = _coerce_with_json_schema(args, schema)
        if coerced is not args and _is_record(coerced):
            args.clear()
            args.update(coerced)
        elif coerced is not args:
            return coerced

    # Validate the result
    if _validate_against_schema(args, schema):
        return args

    # Build error message
    errors = _collect_validation_errors(args, schema)
    error_text = (
        "\n".join(f"  - {path}: {msg}" for path, msg in errors) or "Unknown validation error"
    )

    error_message = (
        f'Validation failed for tool "{tool_call.name}":\n'
        f"{error_text}\n\n"
        f"Received arguments:\n"
        f"{__import__('json').dumps(tool_call.arguments, indent=2)}"
    )
    raise Exception(error_message)


def _collect_validation_errors(value: Any, schema: dict[str, Any]) -> list[tuple[str, str]]:
    """Collect validation errors as (path, message) pairs."""
    errors: list[tuple[str, str]] = []

    schema_types = _get_schema_types(schema)
    if schema_types and not any(_matches_json_type(value, t) for t in schema_types):
        expected = " or ".join(schema_types)
        errors.append(("root", f"expected type {expected}"))
        return errors

    if isinstance(value, dict):
        # Check required properties
        required = schema.get("required", [])
        for req_key in required:
            if req_key not in value:
                errors.append(("root", f"missing required property '{req_key}'"))

        # Check property types
        properties = schema.get("properties", {})
        for key, prop_value in value.items():
            if key in properties:
                prop_errors = _collect_validation_errors(prop_value, properties[key])
                for path, msg in prop_errors:
                    full_path = f"{key}.{path}" if path != "root" else key
                    errors.append((full_path, msg))

    return errors


class Error(Exception):
    """Custom error class for validation errors."""

    pass
