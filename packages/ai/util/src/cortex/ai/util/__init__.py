"""AI utility modules — port of hoocode's packages/ai/src/utils/."""

from cortex.ai.util.hash import short_hash
from cortex.ai.util.headers import headers_to_record
from cortex.ai.util.json_repair import parse_json_with_repair, parse_streaming_json, repair_json
from cortex.ai.util.overflow import get_overflow_patterns, isContextOverflow
from cortex.ai.util.validation import validate_tool_arguments, validate_tool_call

__all__ = [
    "headers_to_record",
    "isContextOverflow",
    "get_overflow_patterns",
    "parse_json_with_repair",
    "parse_streaming_json",
    "repair_json",
    "short_hash",
    "validate_tool_call",
    "validate_tool_arguments",
]
