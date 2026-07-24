"""Helpers for constraining tool-call argument decoding.

OpenAI-style strict function calling ("structured outputs") guarantees the
model can only emit arguments matching the tool's JSON schema, but it
requires a "closed" schema: every property listed in ``required``,
``additionalProperties: false`` on every object, and only a subset of JSON
Schema keywords. TypeBox-generated tool schemas are open (optionals,
validation keywords), so they must be transformed before ``strict: true`` can
be sent — otherwise the API rejects the request.
"""

from __future__ import annotations

from typing import Any

STRICT_KEYWORD_ALLOWLIST: frozenset[str] = frozenset(
    [
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "definitions",
        "description",
        "enum",
        "items",
        "properties",
        "required",
        "title",
        "type",
    ]
)


def _is_record(value: Any) -> bool:
    """Check if value is a dict-like object."""
    return isinstance(value, dict)


def _make_nullable(schema: Any) -> Any:
    """Make a schema nullable by adding null to the type union.

    Optional properties cannot exist in strict mode (everything is required),
    so a formerly-optional property is expressed as "this type or null".
    """
    if not _is_record(schema):
        return schema

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        if schema_type == "null":
            return schema
        return {**schema, "type": [schema_type, "null"]}

    if isinstance(schema_type, list):
        if "null" in schema_type:
            return schema
        return {**schema, "type": [*schema_type, "null"]}

    enum_values = schema.get("enum")
    if isinstance(enum_values, list):
        if None in enum_values:
            return schema
        return {**schema, "enum": [*enum_values, None]}

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        has_null = any(_is_record(variant) and variant.get("type") == "null" for variant in any_of)
        if has_null:
            return schema
        return {**schema, "anyOf": [*any_of, {"type": "null"}]}

    return {"anyOf": [schema, {"type": "null"}]}


def _transform_node(node: Any) -> Any:
    """Transform a JSON schema node for strict mode."""
    if not _is_record(node):
        return node

    # Keep only keywords strict mode accepts; validation-only keywords
    # (minLength, pattern, format, default, ...) get the request rejected.
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in STRICT_KEYWORD_ALLOWLIST:
            out[key] = value

    properties = out.get("properties")
    if _is_record(properties):
        originally_required = set(
            out.get("required", []) if isinstance(out.get("required"), list) else []
        )
        transformed_properties: dict[str, Any] = {}
        for name, property_schema in properties.items():
            transformed = _transform_node(property_schema)
            transformed_properties[name] = (
                transformed if name in originally_required else _make_nullable(transformed)
            )
        out["properties"] = transformed_properties
        out["required"] = list(transformed_properties.keys())
        out["additionalProperties"] = False
    elif out.get("type") == "object":
        out["properties"] = {}
        out["required"] = []
        out["additionalProperties"] = False

    items = out.get("items")
    if items is not None:
        if isinstance(items, list):
            out["items"] = [_transform_node(item) for item in items]
        else:
            out["items"] = _transform_node(items)

    any_of = out.get("anyOf")
    if isinstance(any_of, list):
        out["anyOf"] = [_transform_node(item) for item in any_of]

    for defs_key in ("$defs", "definitions"):
        defs = out.get(defs_key)
        if _is_record(defs):
            out[defs_key] = {name: _transform_node(def_val) for name, def_val in defs.items()}

    return out


def to_strict_json_schema(schema: Any) -> dict[str, Any]:
    """Transform a JSON schema into the closed form OpenAI strict function calling accepts.

    All properties required (formerly-optional ones become nullable),
    ``additionalProperties: false`` on every object, and unsupported keywords
    stripped. The input is deep-cloned via JSON round-trip, which also drops
    TypeBox's symbol keys.
    """
    import json

    cloned = json.loads(json.dumps(schema if schema is not None else {}))
    transformed = _transform_node(cloned)
    return transformed if _is_record(transformed) else {}
