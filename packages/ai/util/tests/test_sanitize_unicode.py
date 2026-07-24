"""Tests for sanitize_unicode utilities.

Mechanical port of hoocode's sanitize-unicode.ts (no TS test file existed; these
mirror the module's documented behavior).
"""

from cortex.ai.util.sanitize_unicode import sanitize_surrogates


def test_preserves_plain_text() -> None:
    """Plain ASCII text is unchanged."""
    assert sanitize_surrogates("Hello World") == "Hello World"


def test_preserves_valid_emoji() -> None:
    """Valid emoji (single code points) are preserved."""
    assert sanitize_surrogates("Hello 🙈 World") == "Hello 🙈 World"


def test_removes_unpaired_high_surrogate() -> None:
    """A lone high surrogate is removed."""
    unpaired = chr(0xD83D)
    assert sanitize_surrogates(f"Text {unpaired} here") == "Text  here"


def test_removes_unpaired_low_surrogate() -> None:
    """A lone low surrogate is removed."""
    unpaired = chr(0xDE00)
    assert sanitize_surrogates(f"Text {unpaired} here") == "Text  here"


def test_removes_multiple_unpaired_surrogates() -> None:
    """Multiple lone surrogates are all removed."""
    text = f"a{chr(0xD800)}b{chr(0xDFFF)}c"
    assert sanitize_surrogates(text) == "abc"


def test_handles_empty_string() -> None:
    """Empty input returns empty output."""
    assert sanitize_surrogates("") == ""


def test_preserves_bmp_unicode() -> None:
    """Non-surrogate Unicode characters are preserved."""
    assert sanitize_surrogates("café 世界") == "café 世界"
