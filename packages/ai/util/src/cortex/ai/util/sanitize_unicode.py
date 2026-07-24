"""Remove unpaired Unicode surrogate characters from a string.

Port of hoocode's `packages/ai/src/utils/sanitize-unicode.ts`.

Unpaired surrogates (high surrogates 0xD800-0xDBFF without matching low
surrogates 0xDC00-0xDFFF, or vice versa) cause JSON serialization errors in many
API providers.

Valid emoji and other characters outside the Basic Multilingual Plane are stored
as single Python code points (not surrogate pairs) and will NOT be affected by
this function.
"""

import re

# In Python, str is a sequence of Unicode code points, so a lone surrogate is a
# single code point in the range 0xD800-0xDFFF. Properly encoded characters
# (including emoji) never appear as surrogate code points, so removing any
# surrogate code point is equivalent to the TS regex that strips unpaired
# surrogates while preserving valid surrogate pairs.
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def sanitize_surrogates(text: str) -> str:
    """Remove unpaired Unicode surrogate characters from a string.

    Args:
        text: The text to sanitize.

    Returns:
        The sanitized text with unpaired surrogates removed.

    Examples:
        Valid emoji (single code points) are preserved::

            sanitize_surrogates("Hello 🙈 World")  # => "Hello 🙈 World"

        A lone high surrogate is removed::

            unpaired = chr(0xD83D)
            sanitize_surrogates(f"Text {unpaired} here")  # => "Text  here"
    """
    return _SURROGATE_RE.sub("", text)
