"""JSON repair and parsing utilities.

Port of hoocode's `packages/ai/src/utils/json-parse.ts`.
"""

from typing import Any

VALID_JSON_ESCAPES = frozenset(['"', "\\", "/", "b", "f", "n", "r", "t", "u"])


def _is_control_character(char: str) -> bool:
    """Check if a character is a control character (0x00-0x1F)."""
    code_point = ord(char)
    return 0x00 <= code_point <= 0x1F


def _escape_control_character(char: str) -> str:
    """Escape a control character for JSON."""
    if char == "\b":
        return "\\b"
    elif char == "\f":
        return "\\f"
    elif char == "\n":
        return "\\n"
    elif char == "\r":
        return "\\r"
    elif char == "\t":
        return "\\t"
    else:
        return f"\\u{ord(char):04x}"


def repair_json(json_str: str) -> str:
    """Repair malformed JSON string literals.

    Handles:
    - Raw control characters inside strings
    - Doubled backslashes before invalid escape characters

    Args:
        json_str: The potentially malformed JSON string.

    Returns:
        The repaired JSON string.
    """
    repaired = []
    in_string = False
    index = 0
    length = len(json_str)

    while index < length:
        char = json_str[index]

        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue

        if char == '"':
            repaired.append(char)
            in_string = False
            index += 1
            continue

        if char == "\\":
            if index + 1 >= length:
                repaired.append("\\\\")
                index += 1
                continue

            next_char = json_str[index + 1]

            if next_char == "u":
                unicode_digits = json_str[index + 2 : index + 6]
                if len(unicode_digits) == 4 and all(
                    c in "0123456789abcdefABCDEF" for c in unicode_digits
                ):
                    repaired.append(f"\\u{unicode_digits}")
                    index += 6
                    continue

            if next_char in VALID_JSON_ESCAPES:
                repaired.append(f"\\{next_char}")
                index += 2
                continue

            repaired.append("\\\\")
            index += 1
            continue

        repaired.append(_escape_control_character(char) if _is_control_character(char) else char)
        index += 1

    return "".join(repaired)


def parse_json_with_repair(json_str: str) -> Any:
    """Parse JSON with automatic repair on failure.

    Args:
        json_str: The JSON string to parse.

    Returns:
        The parsed object.

    Raises:
        ValueError: If parsing fails even after repair.
    """
    import json

    try:
        return json.loads(json_str)
    except ValueError:
        repaired_json = repair_json(json_str)
        if repaired_json != json_str:
            return json.loads(repaired_json)
        raise


def parse_streaming_json(partial_json: str | None = None) -> dict[str, Any]:
    """Parse potentially incomplete JSON during streaming.

    Always returns a valid object, even if the JSON is incomplete.

    Args:
        partial_json: The partial JSON string from streaming.

    Returns:
        Parsed object or empty dict if parsing fails.
    """
    if not partial_json or partial_json.strip() == "":
        return {}

    try:
        result = parse_json_with_repair(partial_json)
        return result if isinstance(result, dict) else {}
    except (ValueError, Exception):
        return {}
