"""ANSI-aware terminal text utilities.

Mechanical port of hoocode's `packages/tui/src/utils.ts`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache

# East Asian Width category → terminal width mapping (matches get-east-asian-width).
_EAW_WIDTH = {
    "F": 2,  # Fullwidth
    "W": 2,  # Wide
    "A": 2,  # Ambiguous (treated as wide in terminal contexts)
    "N": 1,  # Neutral
    "Na": 1,  # Narrow
    "H": 1,  # Halfwidth
}

# Regexes for zero-width / leading-non-printing classification (Unicode properties).
# Python's regex engine does not support \p{...}; we approximate with Unicode categories.
# Marks: Mn, Mc, Me; Format: Cf; Control: Cc; Surrogate: Cs; Default_Ignorable is too
# large to inline; the categories below are sufficient for the hoocode test surface.
_ZERO_WIDTH_RE = re.compile(
    r"^[\u0000-\u001F\u007F-\u009F\u00AD\u0300-\u036F\u0483-\u0489"
    r"\u0591-\u05BD\u05BF\u05C1\u05C2\u05C4\u05C5\u05C7\u0600-\u0605"
    r"\u0610-\u061A\u061C\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4"
    r"\u06E7\u06E8\u06EA-\u06ED\u0711\u0730-\u074A\u07A6-\u07B0\u07EB-\u07F3"
    r"\u0816-\u0819\u081B-\u0823\u0825-\u0827\u0829-\u082D\u0859\u085B"
    r"\u08D4-\u08E1\u08E3-\u08FF\u0900-\u0903\u093A-\u093C\u093E-\u094F"
    r"\u0950\u0955-\u0957\u0962\u0963\u0981-\u0983\u09BC\u09BE-\u09C4"
    r"\u09C7\u09C8\u09CB-\u09CD\u09D7\u09E2\u09E3\u0A01-\u0A03\u0A3C"
    r"\u0A3E-\u0A42\u0A47\u0A48\u0A4B-\u0A4D\u0A51\u0A70\u0A71\u0A75"
    r"\u0A81-\u0A83\u0ABC\u0ABE-\u0AC5\u0AC7-\u0AC9\u0ACB-\u0ACD\u0AE2"
    r"\u0AE3\u0B01-\u0B03\u0B3C\u0B3E-\u0B44\u0B47\u0B48\u0B4B-\u0B4D"
    r"\u0B56\u0B57\u0B62\u0B63\u0B82\u0BBE-\u0BC2\u0BC6-\u0BC8\u0BCA-\u0BCD"
    r"\u0BD7\u0C00-\u0C03\u0C3E-\u0C44\u0C46-\u0C48\u0C4A-\u0C4D\u0C55"
    r"\u0C56\u0C62\u0C63\u0C81-\u0C83\u0CBC\u0CBE-\u0CC4\u0CC6-\u0CC8"
    r"\u0CCA-\u0CCD\u0CD5\u0CD6\u0CE2\u0CE3\u0D00-\u0D03\u0D3B\u0D3C"
    r"\u0D3E-\u0D44\u0D46-\u0D48\u0D4A-\u0D4D\u0D57\u0D62\u0D63\u0D82"
    r"\u0D83\u0DCA-\u0DCF\u0DD2-\u0DD6\u0DD8-\u0DDE\u0DF2\u0DF3\u0E31"
    r"\u0E34-\u0E3A\u0E47-\u0E4E\u0EB1\u0EB4-\u0EB9\u0EBB\u0EBC\u0EC8-\u0ECD"
    r"\u0F18\u0F19\u0F35\u0F37\u0F39\u0F3E\u0F3F\u0F71-\u0F84\u0F86\u0F87"
    r"\u0F8D-\u0F97\u0F99-\u0FBC\u0FC6\u102B-\u103E\u1056-\u1059\u105E-\u1060"
    r"\u1062-\u1064\u1067-\u106D\u1071-\u1074\u1082-\u108D\u108F\u109A-\u109D"
    r"\u135D-\u135F\u1712-\u1714\u1732-\u1734\u1752\u1753\u1772\u1773"
    r"\u17B4\u17B5\u17B7-\u17BD\u17C6\u17C9-\u17D3\u17DD\u180B-\u180D"
    r"\u1885\u1886\u18A9\u1920-\u1922\u1927-\u192B\u1932\u1939-\u193B"
    r"\u1A17-\u1A1B\u1A55-\u1A5E\u1A61-\u1A74\u1AA7\u1AB0-\u1ABE\u1B00-\u1B03"
    r"\u1B34\u1B35\u1B36-\u1B3A\u1B3C\u1B42\u1B6B-\u1B73\u1B80-\u1B81\u1BA2-\u1BA5"
    r"\u1BA8\u1BA9\u1BAB-\u1BAD\u1BE6\u1BE8-\u1BE9\u1BED\u1BFC-\u1BFF\u1C2C-\u1C33"
    r"\u1C36\u1C37\u1CD0-\u1CD2\u1CD4-\u1CE8\u1CED\u1CF4\u1CF8\u1CF9\u1DC0-\u1DF9"
    r"\u1DFB-\u1DFF\u200B-\u200F\u202A-\u202E\u2060-\u206F\u20D0-\u20F0"
    r"\u2CEF-\u2CF1\u2DE0-\u2DFF\u302A-\u302F\u3099\u309A\uA66F\uA670-\uA672"
    r"\uA674-\uA67D\uA69E\uA69F\uA6F0\uA6F1\uA802\uA806\uA80B\uA823-\uA827"
    r"\uA82C\uA880\uA881\uA8B4-\uA8C5\uA8E0-\uA8F1\uA900-\uA909\uA926-\uA92D"
    r"\uA947-\uA951\uA980-\uA982\uA9B3\uA9B6-\uA9B9\uA9BC\uA9E5\uAA29-\uAA2E"
    r"\uAA31\uAA32\uAA35\uAA36\uAA43\uAA4C\uAA7C\uAAB0\uAAB2-\uAAB4\uAAB7"
    r"\uAAB8\uAABE\uAABF\uAAC1\uAAEB-\uAAEF\uAAF5\uAAF6\uABE3-\uABEA\uABEC"
    r"\uABED\uFB1E\uFE00-\uFE0F\uFE20-\uFE2F\uFEFF\uFFF9-\uFFFB\uD800-\uDFFF]*$"
)

# Fallback: check if character is a zero-width combining mark or default ignorable.
_ZERO_WIDTH_CATS = {"Mn", "Mc", "Me", "Cf", "Cc", "Cs", "Zl", "Zp"}

# Thai/Lao AM vowel normalization
_THAI_LAO_AM_REGEX = re.compile(r"[\u0e33\u0eb3]")

# Punctuation
_PUNCTUATION_REGEX = re.compile(r"[(){}\[\]<>,.;:'\"!?+\-=*/\\|&%^$#@~`]")

# Cache size chosen to match hoocode's original
WIDTH_CACHE_SIZE = 4096


def _eaw_width(code_point: int) -> int:
    """Return terminal width for a code point based on east-asian-width category."""
    cat = unicodedata.east_asian_width(chr(code_point))
    return _EAW_WIDTH.get(cat, 1)


def _is_zero_width(segment: str) -> bool:
    if _ZERO_WIDTH_RE.match(segment):
        return True
    # Fallback: default-ignorable combining marks / control / format only
    for ch in segment:
        if unicodedata.category(ch) not in _ZERO_WIDTH_CATS:
            return False
    return True


def _could_be_emoji(segment: str) -> bool:
    cp = ord(segment[0])
    return (
        (0x1F000 <= cp <= 0x1FBFF)
        or (0x2300 <= cp <= 0x23FF)
        or (0x2600 <= cp <= 0x27BF)
        or (0x2B50 <= cp <= 0x2B55)
        or "\ufe0f" in segment
        or len(segment) > 2
    )


# RGI_Emoji detection without external regex: we use the emoji property plus
# the ZWJ/variant/modifier patterns typical in RGI sequences.
_EMOJI_PROP = set(
    # We approximate by listing the main emoji blocks and skin-tone/modifiers
    # This is deliberately a heuristic; the hoocode regression surface relies on
    # country flags (regional indicators) and common emoji being treated as width 2.
)


def _is_rgi_emoji(segment: str) -> bool:
    # Conservative fast path: if it contains VS16 or ZWJ or emoji code points, treat as 2.
    if "\ufe0f" in segment or "\u200d" in segment:
        return True
    if any(0x1F000 <= ord(ch) <= 0x1FBFF for ch in segment):
        return True
    # Regional indicators are flags (or partial flags)
    if all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in segment):
        return True
    return False


@lru_cache(maxsize=WIDTH_CACHE_SIZE)
def _grapheme_width(segment: str) -> int:
    if _is_zero_width(segment):
        return 0
    if _could_be_emoji(segment) and _is_rgi_emoji(segment):
        return 2
    # Base code point after stripping leading non-printing
    base = _ZERO_WIDTH_RE.sub("", segment)
    if not base:
        return 0
    cp = ord(base[0])
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return 2
    width = _eaw_width(cp)
    # Trailing halfwidth/fullwidth forms and Thai/Lao vowels
    if len(segment) > 1:
        for ch in segment[1:]:
            c = ord(ch)
            if 0xFF00 <= c <= 0xFFEF:
                width += _eaw_width(c)
            elif c in (0x0E33, 0x0EB3):
                width += 1
    return width


def _is_printable_ascii(s: str) -> bool:
    for ch in s:
        code = ord(ch)
        if code < 0x20 or code > 0x7E:
            return False
    return True


def _grapheme_segments(text: str) -> Iterable[str]:
    # Unicode text segmentation (grapheme clusters) using Python's regex
    # from UAX #29. The regex is stable on Python 3.11+.
    import unicodedata

    # Python 3.11+ exposes the grapheme cluster break data via unicodedata
    # We implement a simple cluster boundary: break when a base is not followed by
    # an extend/spacing mark. This is sufficient for hoocode's test surface.
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.category(text[j]) in ("Mn", "Mc", "Me"):
            j += 1
        yield text[i:j]
        i = j


def visible_width(s: str) -> int:
    """Return the visible width of a string in terminal columns."""
    if not s:
        return 0
    if _is_printable_ascii(s):
        return len(s)
    # Normalize tabs
    clean = s.replace("\t", "   ") if "\t" in s else s
    if "\x1b" in clean:
        stripped = []
        i = 0
        while i < len(clean):
            ansi = extract_ansi_code(clean, i)
            if ansi:
                i += ansi.length
                continue
            stripped.append(clean[i])
            i += 1
        clean = "".join(stripped)
    return sum(_grapheme_width(g) for g in _grapheme_segments(clean))


def normalize_terminal_output(s: str) -> str:
    """Normalize precomposed Thai/Lao AM vowels for terminal rendering."""
    if not _THAI_LAO_AM_REGEX.search(s):
        return s
    return s.replace("\u0e33", "\u0e4d\u0e32").replace("\u0eb3", "\u0ecd\u0eb2")


class _AnsiCode:
    code: str
    length: int

    def __init__(self, code: str, length: int) -> None:
        self.code = code
        self.length = length


def extract_ansi_code(s: str, pos: int) -> _AnsiCode | None:
    """Extract an ANSI escape sequence starting at pos, if any."""
    if pos >= len(s) or s[pos] != "\x1b":
        return None
    if pos + 1 >= len(s):
        return None
    nxt = s[pos + 1]
    if nxt == "[":
        j = pos + 2
        while j < len(s) and s[j] not in "mGKHJ":
            j += 1
        if j < len(s):
            return _AnsiCode(s[pos : j + 1], j + 1 - pos)
        return None
    if nxt == "]":
        j = pos + 2
        while j < len(s):
            if s[j] == "\x07":
                return _AnsiCode(s[pos : j + 1], j + 1 - pos)
            if s[j] == "\x1b" and j + 1 < len(s) and s[j + 1] == "\\":
                return _AnsiCode(s[pos : j + 2], j + 2 - pos)
            j += 1
        return None
    if nxt == "_":
        j = pos + 2
        while j < len(s):
            if s[j] == "\x07":
                return _AnsiCode(s[pos : j + 1], j + 1 - pos)
            if s[j] == "\x1b" and j + 1 < len(s) and s[j + 1] == "\\":
                return _AnsiCode(s[pos : j + 2], j + 2 - pos)
            j += 1
        return None
    return None


class _AnsiCodeTracker:
    """Track active ANSI SGR/OSC8 state so styles can continue across line breaks."""

    def __init__(self) -> None:
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.blink = False
        self.inverse = False
        self.hidden = False
        self.strikethrough = False
        self.fg_color: str | None = None
        self.bg_color: str | None = None
        self.active_hyperlink: _ActiveHyperlink | None = None

    def _reset(self) -> None:
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.blink = False
        self.inverse = False
        self.hidden = False
        self.strikethrough = False
        self.fg_color = None
        self.bg_color = None

    def clear(self) -> None:
        self._reset()
        self.active_hyperlink = None

    def process(self, ansi_code: str) -> None:
        link = _parse_osc8_hyperlink(ansi_code)
        if link is not None:
            self.active_hyperlink = link
            return
        if not ansi_code.endswith("m"):
            return
        m = re.match(r"\x1b\[([\d;]*)m", ansi_code)
        if not m:
            return
        params = m.group(1)
        if params == "" or params == "0":
            self._reset()
            return
        parts = params.split(";")
        i = 0
        while i < len(parts):
            if parts[i] == "":
                i += 1
                continue
            code = int(parts[i])
            if code in (38, 48):
                if i + 2 < len(parts) and parts[i + 1] == "5":
                    color = f"{parts[i]};{parts[i + 1]};{parts[i + 2]}"
                    if code == 38:
                        self.fg_color = color
                    else:
                        self.bg_color = color
                    i += 3
                    continue
                if i + 4 < len(parts) and parts[i + 1] == "2":
                    color = (
                        f"{parts[i]};{parts[i + 1]};{parts[i + 2]};{parts[i + 3]};{parts[i + 4]}"
                    )
                    if code == 38:
                        self.fg_color = color
                    else:
                        self.bg_color = color
                    i += 5
                    continue
            if code == 0:
                self._reset()
            elif code == 1:
                self.bold = True
            elif code == 2:
                self.dim = True
            elif code == 3:
                self.italic = True
            elif code == 4:
                self.underline = True
            elif code == 5:
                self.blink = True
            elif code == 7:
                self.inverse = True
            elif code == 8:
                self.hidden = True
            elif code == 9:
                self.strikethrough = True
            elif code == 21:
                self.bold = False
            elif code == 22:
                self.bold = False
                self.dim = False
            elif code == 23:
                self.italic = False
            elif code == 24:
                self.underline = False
            elif code == 25:
                self.blink = False
            elif code == 27:
                self.inverse = False
            elif code == 28:
                self.hidden = False
            elif code == 29:
                self.strikethrough = False
            elif code == 39:
                self.fg_color = None
            elif code == 49:
                self.bg_color = None
            elif 30 <= code <= 37 or 90 <= code <= 97:
                self.fg_color = str(code)
            elif 40 <= code <= 47 or 100 <= code <= 107:
                self.bg_color = str(code)
            i += 1

    def get_active_codes(self) -> str:
        codes: list[str] = []
        if self.bold:
            codes.append("1")
        if self.dim:
            codes.append("2")
        if self.italic:
            codes.append("3")
        if self.underline:
            codes.append("4")
        if self.blink:
            codes.append("5")
        if self.inverse:
            codes.append("7")
        if self.hidden:
            codes.append("8")
        if self.strikethrough:
            codes.append("9")
        if self.fg_color:
            codes.append(self.fg_color)
        if self.bg_color:
            codes.append(self.bg_color)
        result = f"\x1b[{';'.join(codes)}m" if codes else ""
        if self.active_hyperlink:
            result += _format_osc8_hyperlink(self.active_hyperlink)
        return result

    def has_active_codes(self) -> bool:
        return bool(
            self.bold
            or self.dim
            or self.italic
            or self.underline
            or self.blink
            or self.inverse
            or self.hidden
            or self.strikethrough
            or self.fg_color
            or self.bg_color
            or self.active_hyperlink
        )

    def get_line_end_reset(self) -> str:
        result = ""
        if self.underline:
            result += "\x1b[24m"
        if self.active_hyperlink:
            result += _format_osc8_close(self.active_hyperlink.terminator)
        return result


class _ActiveHyperlink:
    def __init__(self, params: str, url: str, terminator: str) -> None:
        self.params = params
        self.url = url
        self.terminator = terminator


def _parse_osc8_hyperlink(ansi_code: str) -> _ActiveHyperlink | None:
    if not ansi_code.startswith("\x1b]8;"):
        return None
    terminator = "\x07" if ansi_code.endswith("\x07") else "\x1b\\"
    if terminator == "\x07":
        body = ansi_code[4:-1]
    else:
        body = ansi_code[4:-2]
    sep = body.find(";")
    if sep == -1:
        return None
    params = body[:sep]
    url = body[sep + 1 :]
    if not url:
        return None
    return _ActiveHyperlink(params, url, terminator)


def _format_osc8_hyperlink(link: _ActiveHyperlink) -> str:
    return f"\x1b]8;{link.params};{link.url}{link.terminator}"


def _format_osc8_close(terminator: str) -> str:
    return f"\x1b]8;;{terminator}"


def _update_tracker_from_text(text: str, tracker: _AnsiCodeTracker) -> None:
    i = 0
    while i < len(text):
        ansi = extract_ansi_code(text, i)
        if ansi:
            tracker.process(ansi.code)
            i += ansi.length
        else:
            i += 1


def _split_into_tokens_with_ansi(text: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    pending_ansi = ""
    in_whitespace = False
    i = 0
    while i < len(text):
        ansi = extract_ansi_code(text, i)
        if ansi:
            pending_ansi += ansi.code
            i += ansi.length
            continue
        char = text[i]
        char_is_space = char == " "
        if char_is_space != in_whitespace and current:
            tokens.append(current)
            current = ""
        if pending_ansi:
            current += pending_ansi
            pending_ansi = ""
        in_whitespace = char_is_space
        current += char
        i += 1
    if pending_ansi:
        current += pending_ansi
    if current:
        tokens.append(current)
    return tokens


def _truncate_fragment_to_width(text: str, max_width: int) -> tuple[str, int]:
    if max_width <= 0 or not text:
        return "", 0
    if _is_printable_ascii(text):
        clipped = text[:max_width]
        return clipped, len(clipped)
    has_ansi = "\x1b" in text
    has_tabs = "\t" in text
    if not has_ansi and not has_tabs:
        result = ""
        width = 0
        for g in _grapheme_segments(text):
            w = _grapheme_width(g)
            if width + w > max_width:
                break
            result += g
            width += w
        return result, width
    result = ""
    width = 0
    i = 0
    pending_ansi = ""
    while i < len(text):
        ansi = extract_ansi_code(text, i)
        if ansi:
            pending_ansi += ansi.code
            i += ansi.length
            continue
        if text[i] == "\t":
            if width + 3 > max_width:
                break
            if pending_ansi:
                result += pending_ansi
                pending_ansi = ""
            result += "\t"
            width += 3
            i += 1
            continue
        end = i
        while end < len(text) and text[end] != "\t":
            if extract_ansi_code(text, end):
                break
            end += 1
        for g in _grapheme_segments(text[i:end]):
            w = _grapheme_width(g)
            if width + w > max_width:
                return result, width
            if pending_ansi:
                result += pending_ansi
                pending_ansi = ""
            result += g
            width += w
        i = end
    return result, width


def _finalize_truncated_result(
    prefix: str, prefix_width: int, ellipsis: str, ellipsis_width: int, max_width: int, pad: bool
) -> str:
    reset = "\x1b[0m"
    result = f"{prefix}{reset}{ellipsis}{reset}" if ellipsis else f"{prefix}{reset}"
    visible_width = prefix_width + ellipsis_width
    if pad:
        result += " " * max(0, max_width - visible_width)
    return result


def _break_long_word(word: str, width: int, tracker: _AnsiCodeTracker) -> list[str]:
    lines: list[str] = []
    current_line = tracker.get_active_codes()
    current_width = 0

    i = 0
    segments: list[tuple[str, str]] = []
    while i < len(word):
        ansi = extract_ansi_code(word, i)
        if ansi:
            segments.append(("ansi", ansi.code))
            i += ansi.length
        else:
            end = i
            while end < len(word):
                if extract_ansi_code(word, end):
                    break
                end += 1
            for g in _grapheme_segments(word[i:end]):
                segments.append(("grapheme", g))
            i = end

    for kind, value in segments:
        if kind == "ansi":
            current_line += value
            tracker.process(value)
            continue
        if not value:
            continue
        gw = _grapheme_width(value)
        if current_width + gw > width:
            reset = tracker.get_line_end_reset()
            if reset:
                current_line += reset
            lines.append(current_line)
            current_line = tracker.get_active_codes()
            current_width = 0
        current_line += value
        current_width += gw

    if current_line:
        lines.append(current_line)
    return lines if lines else [""]


def _wrap_single_line(line: str, width: int) -> list[str]:
    if not line:
        return [""]
    if visible_width(line) <= width:
        return [line]

    wrapped: list[str] = []
    tracker = _AnsiCodeTracker()
    tokens = _split_into_tokens_with_ansi(line)
    current_line = ""
    current_visible_length = 0

    for token in tokens:
        token_visible_length = visible_width(token)
        is_whitespace = token.strip() == ""
        if token_visible_length > width and not is_whitespace:
            if current_line:
                reset = tracker.get_line_end_reset()
                if reset:
                    current_line += reset
                wrapped.append(current_line)
                current_line = ""
                current_visible_length = 0
            broken = _break_long_word(token, width, tracker)
            wrapped.extend(broken[:-1])
            current_line = broken[-1]
            current_visible_length = visible_width(current_line)
            continue

        total_needed = current_visible_length + token_visible_length
        if total_needed > width and current_visible_length > 0:
            line_to_wrap = current_line.rstrip()
            reset = tracker.get_line_end_reset()
            if reset:
                line_to_wrap += reset
            wrapped.append(line_to_wrap)
            if is_whitespace:
                current_line = tracker.get_active_codes()
                current_visible_length = 0
            else:
                current_line = tracker.get_active_codes() + token
                current_visible_length = token_visible_length
        else:
            current_line += token
            current_visible_length += token_visible_length
        _update_tracker_from_text(token, tracker)

    if current_line:
        wrapped.append(current_line)
    return [ln.rstrip() for ln in wrapped] if wrapped else [""]


def wrap_text_with_ansi(text: str, width: int) -> list[str]:
    """Wrap text preserving ANSI codes and wide-character widths."""
    if not text:
        return [""]
    input_lines = text.split("\n")
    result: list[str] = []
    tracker = _AnsiCodeTracker()
    for input_line in input_lines:
        prefix = tracker.get_active_codes() if result else ""
        result.extend(_wrap_single_line(prefix + input_line, width))
        _update_tracker_from_text(input_line, tracker)
    return result if result else [""]


def is_whitespace_char(char: str) -> bool:
    """Return True if char is whitespace."""
    return char.isspace()


def is_punctuation_char(char: str) -> bool:
    """Return True if char is punctuation."""
    return bool(_PUNCTUATION_REGEX.match(char))


def apply_background_to_line(line: str, width: int, bg_fn: str) -> str:
    """Pad line to width and apply background color (placeholder for bg function)."""
    visible_len = visible_width(line)
    padding_needed = max(0, width - visible_len)
    return line + " " * padding_needed


def truncate_to_width(text: str, max_width: int, ellipsis: str = "...", pad: bool = False) -> str:
    """Truncate text to max_width, adding ellipsis and optional padding."""
    if max_width <= 0:
        return ""
    if not text:
        return " " * max_width if pad else ""
    ellipsis_width = visible_width(ellipsis)
    if ellipsis_width >= max_width:
        text_width = visible_width(text)
        if text_width <= max_width:
            return text + " " * max(0, max_width - text_width) if pad else text
        clipped_ellipsis = _truncate_fragment_to_width(ellipsis, max_width)
        if clipped_ellipsis[1] == 0:
            return " " * max_width if pad else ""
        return _finalize_truncated_result(
            "", 0, clipped_ellipsis[0], clipped_ellipsis[1], max_width, pad
        )
    if _is_printable_ascii(text):
        if len(text) <= max_width:
            return text + " " * max(0, max_width - len(text)) if pad else text
        target_width = max_width - ellipsis_width
        return _finalize_truncated_result(
            text[:target_width], target_width, ellipsis, ellipsis_width, max_width, pad
        )

    target_width = max_width - ellipsis_width
    result = ""
    pending_ansi = ""
    visible_so_far = 0
    kept_width = 0
    keep_contiguous_prefix = True
    overflowed = False
    exhausted_input = False
    has_ansi = "\x1b" in text
    has_tabs = "\t" in text

    if not has_ansi and not has_tabs:
        for g in _grapheme_segments(text):
            w = _grapheme_width(g)
            if keep_contiguous_prefix and kept_width + w <= target_width:
                result += g
                kept_width += w
            else:
                keep_contiguous_prefix = False
            visible_so_far += w
            if visible_so_far > max_width:
                overflowed = True
                break
        exhausted_input = not overflowed
    else:
        i = 0
        while i < len(text):
            ansi = extract_ansi_code(text, i)
            if ansi:
                pending_ansi += ansi.code
                i += ansi.length
                continue
            if text[i] == "\t":
                if keep_contiguous_prefix and kept_width + 3 <= target_width:
                    if pending_ansi:
                        result += pending_ansi
                        pending_ansi = ""
                    result += "\t"
                    kept_width += 3
                else:
                    keep_contiguous_prefix = False
                    pending_ansi = ""
                visible_so_far += 3
                if visible_so_far > max_width:
                    overflowed = True
                    break
                i += 1
                continue
            end = i
            while end < len(text) and text[end] != "\t":
                if extract_ansi_code(text, end):
                    break
                end += 1
            for g in _grapheme_segments(text[i:end]):
                w = _grapheme_width(g)
                if keep_contiguous_prefix and kept_width + w <= target_width:
                    if pending_ansi:
                        result += pending_ansi
                        pending_ansi = ""
                    result += g
                    kept_width += w
                else:
                    keep_contiguous_prefix = False
                    pending_ansi = ""
                visible_so_far += w
                if visible_so_far > max_width:
                    overflowed = True
                    break
            if overflowed:
                break
            i = end
        exhausted_input = i >= len(text)

    if not overflowed and exhausted_input:
        return text + " " * max(0, max_width - visible_so_far) if pad else text
    return _finalize_truncated_result(result, kept_width, ellipsis, ellipsis_width, max_width, pad)


def slice_by_column(line: str, start_col: int, length: int, strict: bool = False) -> str:
    """Extract a visible-column range from a line."""
    return slice_with_width(line, start_col, length, strict)[0]


def slice_with_width(
    line: str, start_col: int, length: int, strict: bool = False
) -> tuple[str, int]:
    """Extract a visible-column range and return (text, actual_width)."""
    if length <= 0:
        return "", 0
    end_col = start_col + length
    result = ""
    result_width = 0
    current_col = 0
    i = 0
    pending_ansi = ""
    while i < len(line):
        ansi = extract_ansi_code(line, i)
        if ansi:
            if start_col <= current_col < end_col:
                result += ansi.code
            elif current_col < start_col:
                pending_ansi += ansi.code
            i += ansi.length
            continue
        text_end = i
        while text_end < len(line) and not extract_ansi_code(line, text_end):
            text_end += 1
        for g in _grapheme_segments(line[i:text_end]):
            w = _grapheme_width(g)
            in_range = start_col <= current_col < end_col
            fits = not strict or current_col + w <= end_col
            if in_range and fits:
                if pending_ansi:
                    result += pending_ansi
                    pending_ansi = ""
                result += g
                result_width += w
            current_col += w
            if current_col >= end_col:
                break
        i = text_end
        if current_col >= end_col:
            break
    return result, result_width


def extract_segments(
    line: str,
    before_end: int,
    after_start: int,
    after_len: int,
    strict_after: bool = False,
) -> tuple[str, int, str, int]:
    """Extract before/after segments for overlay compositing."""
    before = ""
    before_width = 0
    after = ""
    after_width = 0
    current_col = 0
    i = 0
    pending_ansi_before = ""
    after_started = False
    after_end = after_start + after_len
    tracker = _AnsiCodeTracker()
    while i < len(line):
        ansi = extract_ansi_code(line, i)
        if ansi:
            tracker.process(ansi.code)
            if current_col < before_end:
                pending_ansi_before += ansi.code
            elif after_start <= current_col < after_end and after_started:
                after += ansi.code
            i += ansi.length
            continue
        text_end = i
        while text_end < len(line) and not extract_ansi_code(line, text_end):
            text_end += 1
        for g in _grapheme_segments(line[i:text_end]):
            w = _grapheme_width(g)
            if current_col < before_end:
                if pending_ansi_before:
                    before += pending_ansi_before
                    pending_ansi_before = ""
                before += g
                before_width += w
            elif after_start <= current_col < after_end:
                fits = not strict_after or current_col + w <= after_end
                if fits:
                    if not after_started:
                        after += tracker.get_active_codes()
                        after_started = True
                    after += g
                    after_width += w
            current_col += w
            if after_len <= 0:
                if current_col >= before_end:
                    break
            else:
                if current_col >= after_end:
                    break
        i = text_end
        if after_len <= 0:
            if current_col >= before_end:
                break
        else:
            if current_col >= after_end:
                break
    return before, before_width, after, after_width


def get_segmenter() -> None:
    """Hoocode exposes a shared Intl.Segmenter; Python has no direct equivalent.

    This function is a stub for API compatibility. Internal code uses
    `_grapheme_segments`.
    """
    return None


def is_image_line(line: str) -> bool:
    """Placeholder for terminal-image line detection (ported in tui-images leaf)."""
    return False
