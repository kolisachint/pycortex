"""Terminal image protocols and capability detection.

Mechanical port of hoocode's ``packages/tui/src/terminal-image.ts`` (423 lines).

Two conventions, applied throughout this leaf:

* An **exported TS interface** becomes a dataclass (``TerminalCapabilities``,
  ``CellDimensions``, ``ImageDimensions``, ``ImageRenderOptions``); an
  **anonymous inline options object** becomes keyword arguments
  (``encode_kitty``, ``encode_iterm2``). ``render_image`` returns a
  ``RenderedImage`` because Python has no anonymous object literal.
* JS truthiness is preserved where the TS relies on it: ``if (options.columns)``
  skips ``0``, so the port writes ``if columns:`` rather than ``is not None``.
"""

from __future__ import annotations

import base64
import math
import os
import random
import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "CellDimensions",
    "ImageDimensions",
    "ImageProtocol",
    "ImageRenderOptions",
    "RenderedImage",
    "TerminalCapabilities",
    "allocate_image_id",
    "calculate_image_rows",
    "delete_all_kitty_images",
    "delete_kitty_image",
    "detect_capabilities",
    "encode_iterm2",
    "encode_kitty",
    "get_capabilities",
    "get_cell_dimensions",
    "get_gif_dimensions",
    "get_image_dimensions",
    "get_jpeg_dimensions",
    "get_png_dimensions",
    "get_webp_dimensions",
    "hyperlink",
    "image_fallback",
    "is_image_line",
    "render_image",
    "reset_capabilities_cache",
    "set_capabilities",
    "set_cell_dimensions",
]

ImageProtocol = Literal["kitty", "iterm2"] | None


@dataclass
class TerminalCapabilities:
    images: ImageProtocol
    true_color: bool
    hyperlinks: bool


@dataclass
class CellDimensions:
    width_px: int
    height_px: int


@dataclass
class ImageDimensions:
    width_px: int
    height_px: int


@dataclass
class ImageRenderOptions:
    max_width_cells: int | None = None
    max_height_cells: int | None = None
    preserve_aspect_ratio: bool | None = None
    #: Kitty image ID. If provided, reuses/replaces existing image with this ID.
    image_id: int | None = None
    #: Whether Kitty should apply its default cursor movement after placement.
    move_cursor: bool | None = None


@dataclass
class RenderedImage:
    """What ``renderImage`` returns — an anonymous object literal in the TS."""

    sequence: str
    rows: int
    image_id: int | None = None


_cached_capabilities: TerminalCapabilities | None = None

# Default cell dimensions - updated by TUI when terminal responds to query
_cell_dimensions = CellDimensions(width_px=9, height_px=18)


def get_cell_dimensions() -> CellDimensions:
    return _cell_dimensions


def set_cell_dimensions(dims: CellDimensions) -> None:
    global _cell_dimensions
    _cell_dimensions = dims


def _env(name: str) -> str:
    """``process.env.X?.toLowerCase() || ""``.

    ``os.environ.get`` returning ``""`` and returning ``None`` collapse to the
    same thing here, exactly as the TS ``?.``/``||`` pair does.
    """
    return (os.environ.get(name) or "").lower()


def detect_capabilities() -> TerminalCapabilities:
    term_program = _env("TERM_PROGRAM")
    term = _env("TERM")
    color_term = _env("COLORTERM")

    # tmux and screen swallow OSC 8 by default (passthrough is opt-in and wraps
    # sequences differently). Force hyperlinks off whenever we detect them, even
    # when the outer terminal would otherwise support OSC 8. Image protocols are
    # also unreliable under tmux/screen, so leave `images: None` for safety.
    in_tmux_or_screen = (
        bool(os.environ.get("TMUX")) or term.startswith("tmux") or term.startswith("screen")
    )
    if in_tmux_or_screen:
        true_color = color_term in ("truecolor", "24bit")
        return TerminalCapabilities(images=None, true_color=true_color, hyperlinks=False)

    if os.environ.get("KITTY_WINDOW_ID") or term_program == "kitty":
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if term_program == "ghostty" or "ghostty" in term or os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if os.environ.get("WEZTERM_PANE") or term_program == "wezterm":
        return TerminalCapabilities(images="kitty", true_color=True, hyperlinks=True)

    if os.environ.get("ITERM_SESSION_ID") or term_program == "iterm.app":
        return TerminalCapabilities(images="iterm2", true_color=True, hyperlinks=True)

    if term_program == "vscode":
        return TerminalCapabilities(images=None, true_color=True, hyperlinks=True)

    if term_program == "alacritty":
        return TerminalCapabilities(images=None, true_color=True, hyperlinks=True)

    # Unknown terminal: be conservative. OSC 8 is rendered invisibly as "just
    # text" on terminals that swallow it, which means the URL disappears from
    # the rendered output. Default to the legacy `text (url)` behavior unless we
    # have positively identified a hyperlink-capable terminal above.
    true_color = color_term in ("truecolor", "24bit")
    return TerminalCapabilities(images=None, true_color=true_color, hyperlinks=False)


def get_capabilities() -> TerminalCapabilities:
    global _cached_capabilities
    if not _cached_capabilities:
        _cached_capabilities = detect_capabilities()
    return _cached_capabilities


def reset_capabilities_cache() -> None:
    global _cached_capabilities
    _cached_capabilities = None


def set_capabilities(caps: TerminalCapabilities) -> None:
    """Override the cached capabilities. Useful in tests to exercise both code paths."""
    global _cached_capabilities
    _cached_capabilities = caps


KITTY_PREFIX = "\x1b_G"
ITERM2_PREFIX = "\x1b]1337;File="


def is_image_line(line: str) -> bool:
    # Fast path: sequence at line start (single-row images)
    if line.startswith(KITTY_PREFIX) or line.startswith(ITERM2_PREFIX):
        return True
    # Slow path: sequence elsewhere (multi-row images have cursor-up prefix)
    return KITTY_PREFIX in line or ITERM2_PREFIX in line


def allocate_image_id() -> int:
    """Generate a random image ID for Kitty graphics protocol.

    Uses random IDs to avoid collisions between different module instances
    (e.g., main app vs extensions).
    """
    # Use random ID in range [1, 0xfffffffe] to avoid collisions
    return random.randrange(0xFFFFFFFE) + 1


def encode_kitty(
    base64_data: str,
    *,
    columns: int | None = None,
    rows: int | None = None,
    image_id: int | None = None,
    move_cursor: bool | None = None,
) -> str:
    """Encode a kitty graphics placement.

    ``move_cursor`` says whether Kitty should apply its default cursor movement
    after placement; the default (``None``) means yes, as in the TS.
    """
    chunk_size = 4096

    params: list[str] = ["a=T", "f=100", "q=2"]

    if move_cursor is False:
        params.append("C=1")
    if columns:
        params.append(f"c={columns}")
    if rows:
        params.append(f"r={rows}")
    if image_id:
        params.append(f"i={image_id}")

    if len(base64_data) <= chunk_size:
        return f"\x1b_G{','.join(params)};{base64_data}\x1b\\"

    chunks: list[str] = []
    offset = 0
    is_first = True

    while offset < len(base64_data):
        chunk = base64_data[offset : offset + chunk_size]
        is_last = offset + chunk_size >= len(base64_data)

        if is_first:
            chunks.append(f"\x1b_G{','.join(params)},m=1;{chunk}\x1b\\")
            is_first = False
        elif is_last:
            chunks.append(f"\x1b_Gm=0;{chunk}\x1b\\")
        else:
            chunks.append(f"\x1b_Gm=1;{chunk}\x1b\\")

        offset += chunk_size

    return "".join(chunks)


def delete_kitty_image(image_id: int) -> str:
    """Delete a Kitty graphics image by ID.

    Uses uppercase 'I' to also free the image data.
    """
    return f"\x1b_Ga=d,d=I,i={image_id},q=2\x1b\\"


def delete_all_kitty_images() -> str:
    """Delete all visible Kitty graphics images.

    Uses uppercase 'A' to also free the image data.
    """
    return "\x1b_Ga=d,d=A,q=2\x1b\\"


def encode_iterm2(
    base64_data: str,
    *,
    width: int | str | None = None,
    height: int | str | None = None,
    name: str | None = None,
    preserve_aspect_ratio: bool | None = None,
    inline: bool | None = None,
) -> str:
    params: list[str] = [f"inline={1 if inline is not False else 0}"]

    if width is not None:
        params.append(f"width={width}")
    if height is not None:
        params.append(f"height={height}")
    if name:
        name_base64 = base64.b64encode(name.encode()).decode("ascii")
        params.append(f"name={name_base64}")
    if preserve_aspect_ratio is False:
        params.append("preserveAspectRatio=0")

    return f"\x1b]1337;File={';'.join(params)}:{base64_data}\x07"


def calculate_image_rows(
    image_dimensions: ImageDimensions,
    target_width_cells: int,
    cell_dimensions: CellDimensions | None = None,
) -> int:
    if cell_dimensions is None:
        cell_dimensions = CellDimensions(width_px=9, height_px=18)
    target_width_px = target_width_cells * cell_dimensions.width_px
    scale = target_width_px / image_dimensions.width_px
    scaled_height_px = image_dimensions.height_px * scale
    rows = math.ceil(scaled_height_px / cell_dimensions.height_px)
    return max(1, rows)


_BASE64_ALPHABET_RE = re.compile(r"[^A-Za-z0-9+/\-_]")


def _buffer_from_base64(base64_data: str) -> bytes:
    """``Buffer.from(data, "base64")``.

    Node is lenient where :func:`base64.b64decode` is strict: it ignores every
    character outside the alphabet (whitespace included), accepts the URL-safe
    alphabet, stops at the first padding character and does not require the
    input to be padded at all. Emulated here because every dimension parser
    below decides "is this a PNG?" from the *decoded* bytes — a strict decoder
    would turn an unpadded but perfectly readable header into ``None``.
    """
    cleaned = _BASE64_ALPHABET_RE.sub("", base64_data.split("=")[0])
    cleaned = cleaned.replace("-", "+").replace("_", "/")
    # A trailing group of one character carries no whole byte; node drops it.
    remainder = len(cleaned) % 4
    if remainder == 1:
        cleaned = cleaned[:-1]
        remainder = 0
    if remainder:
        cleaned += "=" * (4 - remainder)
    return base64.b64decode(cleaned)


def _ascii(buffer: bytes) -> str:
    """``buffer.toString("ascii")`` — node masks each byte to 7 bits."""
    return "".join(chr(byte & 0x7F) for byte in buffer)


def get_png_dimensions(base64_data: str) -> ImageDimensions | None:
    try:
        buffer = _buffer_from_base64(base64_data)

        if len(buffer) < 24:
            return None

        if buffer[0] != 0x89 or buffer[1] != 0x50 or buffer[2] != 0x4E or buffer[3] != 0x47:
            return None

        width = int.from_bytes(buffer[16:20], "big")
        height = int.from_bytes(buffer[20:24], "big")

        return ImageDimensions(width_px=width, height_px=height)
    except Exception:
        return None


def get_jpeg_dimensions(base64_data: str) -> ImageDimensions | None:
    try:
        buffer = _buffer_from_base64(base64_data)

        if len(buffer) < 2:
            return None

        if buffer[0] != 0xFF or buffer[1] != 0xD8:
            return None

        offset = 2
        while offset < len(buffer) - 9:
            if buffer[offset] != 0xFF:
                offset += 1
                continue

            marker = buffer[offset + 1]

            if 0xC0 <= marker <= 0xC2:
                height = int.from_bytes(buffer[offset + 5 : offset + 7], "big")
                width = int.from_bytes(buffer[offset + 7 : offset + 9], "big")
                return ImageDimensions(width_px=width, height_px=height)

            if offset + 3 >= len(buffer):
                return None
            length = int.from_bytes(buffer[offset + 2 : offset + 4], "big")
            if length < 2:
                return None
            offset += 2 + length

        return None
    except Exception:
        return None


def get_gif_dimensions(base64_data: str) -> ImageDimensions | None:
    try:
        buffer = _buffer_from_base64(base64_data)

        if len(buffer) < 10:
            return None

        sig = _ascii(buffer[0:6])
        if sig not in ("GIF87a", "GIF89a"):
            return None

        width = int.from_bytes(buffer[6:8], "little")
        height = int.from_bytes(buffer[8:10], "little")

        return ImageDimensions(width_px=width, height_px=height)
    except Exception:
        return None


def get_webp_dimensions(base64_data: str) -> ImageDimensions | None:
    try:
        buffer = _buffer_from_base64(base64_data)

        if len(buffer) < 30:
            return None

        riff = _ascii(buffer[0:4])
        webp = _ascii(buffer[8:12])
        if riff != "RIFF" or webp != "WEBP":
            return None

        chunk = _ascii(buffer[12:16])
        if chunk == "VP8 ":
            if len(buffer) < 30:
                return None
            width = int.from_bytes(buffer[26:28], "little") & 0x3FFF
            height = int.from_bytes(buffer[28:30], "little") & 0x3FFF
            return ImageDimensions(width_px=width, height_px=height)
        elif chunk == "VP8L":
            if len(buffer) < 25:
                return None
            bits = int.from_bytes(buffer[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return ImageDimensions(width_px=width, height_px=height)
        elif chunk == "VP8X":
            if len(buffer) < 30:
                return None
            width = (buffer[24] | (buffer[25] << 8) | (buffer[26] << 16)) + 1
            height = (buffer[27] | (buffer[28] << 8) | (buffer[29] << 16)) + 1
            return ImageDimensions(width_px=width, height_px=height)

        return None
    except Exception:
        return None


def get_image_dimensions(base64_data: str, mime_type: str) -> ImageDimensions | None:
    if mime_type == "image/png":
        return get_png_dimensions(base64_data)
    if mime_type == "image/jpeg":
        return get_jpeg_dimensions(base64_data)
    if mime_type == "image/gif":
        return get_gif_dimensions(base64_data)
    if mime_type == "image/webp":
        return get_webp_dimensions(base64_data)
    return None


def render_image(
    base64_data: str,
    image_dimensions: ImageDimensions,
    options: ImageRenderOptions | None = None,
) -> RenderedImage | None:
    if options is None:
        options = ImageRenderOptions()
    caps = get_capabilities()

    if not caps.images:
        return None

    max_width = options.max_width_cells if options.max_width_cells is not None else 80
    rows = calculate_image_rows(image_dimensions, max_width, get_cell_dimensions())

    if caps.images == "kitty":
        sequence = encode_kitty(
            base64_data,
            columns=max_width,
            rows=rows,
            image_id=options.image_id,
            move_cursor=options.move_cursor,
        )
        return RenderedImage(sequence=sequence, rows=rows, image_id=options.image_id)

    if caps.images == "iterm2":
        sequence = encode_iterm2(
            base64_data,
            width=max_width,
            height="auto",
            preserve_aspect_ratio=(
                options.preserve_aspect_ratio if options.preserve_aspect_ratio is not None else True
            ),
        )
        return RenderedImage(sequence=sequence, rows=rows)

    return None


def hyperlink(text: str, url: str) -> str:
    """Wrap text in an OSC 8 hyperlink sequence.

    The text is rendered as a clickable hyperlink in terminals that support
    OSC 8 (Ghostty, Kitty, WezTerm, iTerm2, VSCode, and others). In terminals
    that do not support OSC 8, the escape sequences are ignored and only the
    plain text is displayed.

    :param text: The visible text to display
    :param url: The URL to link to
    """
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def image_fallback(
    mime_type: str,
    dimensions: ImageDimensions | None = None,
    filename: str | None = None,
) -> str:
    parts: list[str] = []
    if filename:
        parts.append(filename)
    parts.append(f"[{mime_type}]")
    if dimensions:
        parts.append(f"{dimensions.width_px}x{dimensions.height_px}")
    return f"[Image: {' '.join(parts)}]"
