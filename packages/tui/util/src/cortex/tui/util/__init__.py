"""ANSI-aware terminal text utilities."""

from cortex.tui.util._util import (
    apply_background_to_line,
    extract_ansi_code,
    extract_segments,
    get_segmenter,
    is_image_line,
    is_punctuation_char,
    is_whitespace_char,
    normalize_terminal_output,
    slice_by_column,
    slice_with_width,
    truncate_to_width,
    visible_width,
    wrap_text_with_ansi,
)

__all__ = [
    "apply_background_to_line",
    "extract_ansi_code",
    "extract_segments",
    "is_image_line",
    "is_punctuation_char",
    "is_whitespace_char",
    "normalize_terminal_output",
    "slice_by_column",
    "slice_with_width",
    "truncate_to_width",
    "visible_width",
    "wrap_text_with_ansi",
    "get_segmenter",
]
