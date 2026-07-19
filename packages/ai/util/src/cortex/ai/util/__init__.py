"""AI utility modules — port of hoocode's packages/ai/src/utils/."""

from cortex.ai.util.overflow import get_overflow_patterns, isContextOverflow
from cortex.ai.util.validation import validate_tool_arguments, validate_tool_call

__all__ = [
    "isContextOverflow",
    "get_overflow_patterns",
    "validate_tool_call",
    "validate_tool_arguments",
]
