"""Header conversion utilities.

Port of hoocode's `packages/ai/src/utils/headers.ts`.
"""

from collections.abc import Mapping


def headers_to_record(headers: Mapping[str, str]) -> dict[str, str]:
    """Convert a Headers-like mapping to a plain dictionary.

    Args:
        headers: A mapping of header names to values.

    Returns:
        A plain dictionary of header name-value pairs.
    """
    return dict(headers)
