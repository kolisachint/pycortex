# pyright: reportUnusedFunction=false
"""Image MIME-type detection from file magic bytes.

Mechanical port of ``utils/mime.ts``. The TS uses the ``file-type`` package to
sniff the leading bytes; this reimplements detection for the four supported
image formats (PNG, JPEG, GIF, WebP) from their magic numbers so detection is
by content, not extension.
"""

from __future__ import annotations

from pathlib import Path

_FILE_TYPE_SNIFF_BYTES = 4100

IMAGE_MIME_TYPES = frozenset(["image/jpeg", "image/png", "image/gif", "image/webp"])


def _sniff_image_mime(buffer: bytes) -> str | None:
    if len(buffer) >= 8 and buffer[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(buffer) >= 3 and buffer[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(buffer) >= 6 and (buffer[:6] == b"GIF87a" or buffer[:6] == b"GIF89a"):
        return "image/gif"
    if len(buffer) >= 12 and buffer[:4] == b"RIFF" and buffer[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_supported_image_mime_type_from_file(file_path: str) -> str | None:
    """Return the image MIME type for a file, or None if it is not a supported image."""
    try:
        with open(file_path, "rb") as fh:
            buffer = fh.read(_FILE_TYPE_SNIFF_BYTES)
    except OSError:
        return None
    if not buffer:
        return None
    mime = _sniff_image_mime(buffer)
    if mime is None or mime not in IMAGE_MIME_TYPES:
        return None
    return mime


def _detect_from_path(file_path: str) -> str | None:
    return detect_supported_image_mime_type_from_file(str(Path(file_path)))
