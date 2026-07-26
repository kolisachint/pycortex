"""Shared diff computation utilities for the edit tool.

Mechanical port of ``core/tools/edit-diff.ts``. Used by ``edit.py`` (execution)
and for preview rendering. Uses Python's ``difflib`` (SequenceMatcher on lines)
in place of the JS ``diff`` package for ``generate_diff_string``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass

from cortex.code.tools.path_utils import resolve_to_cwd

# Cache for normalized text to avoid redundant processing. Max 100 entries.
_normalize_cache: OrderedDict[str, str] = OrderedDict()
_MAX_CACHE_SIZE = 100


def detect_line_ending(content: str) -> str:
    """Return "\\r\\n" or "\\n" for the file's dominant line ending."""
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    """Normalize line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    """Restore line endings from LF to the original ending."""
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


_SMART_SINGLE_QUOTES = re.compile("[\u2018\u2019\u201a\u201b]")
_SMART_DOUBLE_QUOTES = re.compile("[\u201c\u201d\u201e\u201f]")
_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_SPECIAL_SPACES = re.compile("[\u00a0\u2002-\u200a\u202f\u205f\u3000]")
_LEADING_WS = re.compile(r"^(\s*)")
_MULTI_SPACE = re.compile(r" {2,}")


def normalize_for_fuzzy_match(text: str) -> str:
    """Normalize text for fuzzy matching (progressive transformations)."""
    cached = _normalize_cache.get(text)
    if cached is not None:
        return cached

    stage = unicodedata.normalize("NFKC", text)
    stage = stage.replace("\r\n", "\n").replace("\r", "\n")

    out_lines: list[str] = []
    for line in stage.split("\n"):
        normalized = line.replace("\t", "  ")
        leading_match = _LEADING_WS.match(normalized)
        leading_spaces = leading_match.group(1) if leading_match else ""
        rest = normalized[len(leading_spaces) :]
        normalized = leading_spaces + _MULTI_SPACE.sub(" ", rest)
        out_lines.append(normalized.rstrip())
    stage = "\n".join(out_lines)

    stage = _SMART_SINGLE_QUOTES.sub("'", stage)
    stage = _SMART_DOUBLE_QUOTES.sub('"', stage)
    stage = _DASHES.sub("-", stage)
    stage = _SPECIAL_SPACES.sub(" ", stage)

    if len(_normalize_cache) >= _MAX_CACHE_SIZE:
        _normalize_cache.popitem(last=False)
    _normalize_cache[text] = stage

    return stage


@dataclass
class FuzzyMatchResult:
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool
    content_for_replacement: str


@dataclass
class Edit:
    old_text: str
    new_text: str
    replace_all: bool = False


@dataclass
class _MatchedEdit:
    edit_index: int
    match_index: int
    match_length: int
    new_text: str


@dataclass
class AppliedEditsResult:
    base_content: str
    new_content: str


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatchResult:
    """Find ``old_text`` in ``content``: exact match first, then fuzzy match."""
    exact_index = content.find(old_text)
    if exact_index != -1:
        return FuzzyMatchResult(
            found=True,
            index=exact_index,
            match_length=len(old_text),
            used_fuzzy_match=False,
            content_for_replacement=content,
        )

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old_text)

    if fuzzy_index == -1:
        return FuzzyMatchResult(
            found=False,
            index=-1,
            match_length=0,
            used_fuzzy_match=False,
            content_for_replacement=content,
        )

    return FuzzyMatchResult(
        found=True,
        index=fuzzy_index,
        match_length=len(fuzzy_old_text),
        used_fuzzy_match=True,
        content_for_replacement=fuzzy_content,
    )


def strip_bom(content: str) -> tuple[str, str]:
    """Strip a leading UTF-8 BOM. Returns ``(bom, text)``."""
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


def _collect_match_indices(haystack: str, needle: str) -> list[int]:
    """Start index of every non-overlapping occurrence of ``needle``."""
    indices: list[int] = []
    if len(needle) == 0:
        return indices
    from_index = 0
    while True:
        idx = haystack.find(needle, from_index)
        if idx == -1:
            break
        indices.append(idx)
        from_index = idx + len(needle)
    return indices


def _block_normalize_line(line: str) -> str:
    """Per-line normalization for indentation-tolerant block matching."""
    return normalize_for_fuzzy_match(line).strip()


@dataclass
class _LineBlockMatch:
    match_index: int
    match_length: int


def _find_line_block_matches(content: str, old_text: str) -> list[_LineBlockMatch]:
    """Indentation-tolerant fallback matcher."""
    had_trailing_newline = old_text.endswith("\n")
    old_lines = old_text.split("\n")
    if had_trailing_newline:
        old_lines.pop()
    if len(old_lines) == 0:
        return []
    trimmed_old = [_block_normalize_line(line) for line in old_lines]

    content_lines = content.split("\n")
    k = len(trimmed_old)
    if k > len(content_lines):
        return []

    offsets = [0] * len(content_lines)
    acc = 0
    for i in range(len(content_lines)):
        offsets[i] = acc
        acc += len(content_lines[i]) + 1

    matches: list[_LineBlockMatch] = []
    i = 0
    while i + k <= len(content_lines):
        ok = True
        for j in range(k):
            if _block_normalize_line(content_lines[i + j]) != trimmed_old[j]:
                ok = False
                break
        if ok:
            match_index = offsets[i]
            match_length = 0
            for j in range(k):
                match_length += len(content_lines[i + j]) + (1 if j < k - 1 else 0)
            if had_trailing_newline and i + k < len(content_lines):
                match_length += 1
            matches.append(_LineBlockMatch(match_index, match_length))
        i += 1
    return matches


def _get_not_found_error(path: str, edit_index: int, total_edits: int) -> str:
    if total_edits == 1:
        return (
            f"Could not find the exact text in {path}. The old text must match "
            "exactly including all whitespace and newlines."
        )
    return (
        f"Could not find edits[{edit_index}] in {path}. The oldText must match "
        "exactly including all whitespace and newlines."
    )


def _get_duplicate_error(path: str, edit_index: int, total_edits: int, occurrences: int) -> str:
    if total_edits == 1:
        return (
            f"Found {occurrences} occurrences of the text in {path}. The text "
            "must be unique. Please provide more context to make it unique."
        )
    return (
        f"Found {occurrences} occurrences of edits[{edit_index}] in {path}. Each "
        "oldText must be unique. Please provide more context to make it unique."
    )


def _get_empty_old_text_error(path: str, edit_index: int, total_edits: int) -> str:
    if total_edits == 1:
        return f"oldText must not be empty in {path}."
    return f"edits[{edit_index}].oldText must not be empty in {path}."


def _get_no_change_error(path: str, total_edits: int) -> str:
    if total_edits == 1:
        return (
            f"No changes made to {path}. The replacement produced identical "
            "content. This might indicate an issue with special characters or the "
            "text not existing as expected."
        )
    return f"No changes made to {path}. The replacements produced identical content."


def apply_edits_to_normalized_content(
    normalized_content: str,
    edits: list[Edit],
    path: str,
) -> AppliedEditsResult:
    """Apply one or more exact-text replacements to LF-normalized content.

    All edits are matched against the same original content, then applied in
    reverse order so offsets remain stable. Raises ``ValueError`` on failure.
    """
    normalized_edits = [
        Edit(
            old_text=normalize_to_lf(edit.old_text),
            new_text=normalize_to_lf(edit.new_text),
            replace_all=edit.replace_all is True,
        )
        for edit in edits
    ]

    for i, edit in enumerate(normalized_edits):
        if len(edit.old_text) == 0:
            raise ValueError(_get_empty_old_text_error(path, i, len(normalized_edits)))

    initial_matches = [
        fuzzy_find_text(normalized_content, edit.old_text) for edit in normalized_edits
    ]
    base_content = (
        normalize_for_fuzzy_match(normalized_content)
        if any(m.used_fuzzy_match for m in initial_matches)
        else normalized_content
    )

    matched_edits: list[_MatchedEdit] = []
    for i, edit in enumerate(normalized_edits):
        match_result = fuzzy_find_text(base_content, edit.old_text)

        spans: list[_LineBlockMatch]
        if match_result.found:
            needle = (
                normalize_for_fuzzy_match(edit.old_text)
                if match_result.used_fuzzy_match
                else edit.old_text
            )
            spans = [
                _LineBlockMatch(match_index=idx, match_length=match_result.match_length)
                for idx in _collect_match_indices(base_content, needle)
            ]
        else:
            spans = _find_line_block_matches(base_content, edit.old_text)
            if len(spans) == 0:
                raise ValueError(_get_not_found_error(path, i, len(normalized_edits)))

        if edit.replace_all:
            for span in spans:
                matched_edits.append(
                    _MatchedEdit(
                        edit_index=i,
                        match_index=span.match_index,
                        match_length=span.match_length,
                        new_text=edit.new_text,
                    )
                )
            continue

        if len(spans) > 1:
            raise ValueError(_get_duplicate_error(path, i, len(normalized_edits), len(spans)))

        matched_edits.append(
            _MatchedEdit(
                edit_index=i,
                match_index=spans[0].match_index,
                match_length=spans[0].match_length,
                new_text=edit.new_text,
            )
        )

    matched_edits.sort(key=lambda m: m.match_index)
    for i in range(1, len(matched_edits)):
        previous = matched_edits[i - 1]
        current = matched_edits[i]
        if previous.match_index + previous.match_length > current.match_index:
            raise ValueError(
                f"edits[{previous.edit_index}] and edits[{current.edit_index}] overlap "
                f"in {path}. Merge them into one edit or target disjoint regions."
            )

    new_content = base_content
    for i in range(len(matched_edits) - 1, -1, -1):
        edit = matched_edits[i]
        new_content = (
            new_content[: edit.match_index]
            + edit.new_text
            + new_content[edit.match_index + edit.match_length :]
        )

    if base_content == new_content:
        raise ValueError(_get_no_change_error(path, len(normalized_edits)))

    return AppliedEditsResult(base_content=base_content, new_content=new_content)


@dataclass
class _DiffPart:
    value: str
    added: bool
    removed: bool


def _diff_lines(old_content: str, new_content: str) -> list[_DiffPart]:
    """Line-level diff mirroring the shape of the JS ``diff`` package output.

    Each part's ``value`` includes trailing newlines for its lines (the last
    part may omit one if the source did).
    """
    import difflib

    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    # Re-attach the newline terminators (split drops them). The final element has
    # no trailing newline in the source string.
    old_seq = [line + "\n" for line in old_lines[:-1]] + [old_lines[-1]]
    new_seq = [line + "\n" for line in new_lines[:-1]] + [new_lines[-1]]

    sm = difflib.SequenceMatcher(a=old_seq, b=new_seq, autojunk=False)
    parts: list[_DiffPart] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(_DiffPart(value="".join(old_seq[i1:i2]), added=False, removed=False))
        elif tag == "delete":
            parts.append(_DiffPart(value="".join(old_seq[i1:i2]), added=False, removed=True))
        elif tag == "insert":
            parts.append(_DiffPart(value="".join(new_seq[j1:j2]), added=True, removed=False))
        elif tag == "replace":
            parts.append(_DiffPart(value="".join(old_seq[i1:i2]), added=False, removed=True))
            parts.append(_DiffPart(value="".join(new_seq[j1:j2]), added=True, removed=False))
    return parts


@dataclass
class EditDiffResult:
    diff: str
    first_changed_line: int | None


@dataclass
class EditDiffError:
    error: str


def generate_diff_string(
    old_content: str,
    new_content: str,
    context_lines: int = 4,
) -> EditDiffResult:
    """Generate a unified diff string with line numbers and context."""
    parts = _diff_lines(old_content, new_content)
    output: list[str] = []

    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    max_line_num = max(len(old_lines), len(new_lines))
    line_num_width = len(str(max_line_num))

    old_line_num = 1
    new_line_num = 1
    last_was_change = False
    first_changed_line: int | None = None

    for i, part in enumerate(parts):
        raw = part.value.split("\n")
        if raw and raw[-1] == "":
            raw.pop()

        if part.added or part.removed:
            if first_changed_line is None:
                first_changed_line = new_line_num

            for line in raw:
                if part.added:
                    line_num = str(new_line_num).rjust(line_num_width)
                    output.append(f"+{line_num} {line}")
                    new_line_num += 1
                else:
                    line_num = str(old_line_num).rjust(line_num_width)
                    output.append(f"-{line_num} {line}")
                    old_line_num += 1
            last_was_change = True
        else:
            next_part_is_change = i < len(parts) - 1 and (
                parts[i + 1].added or parts[i + 1].removed
            )
            has_leading_change = last_was_change
            has_trailing_change = next_part_is_change

            if has_leading_change and has_trailing_change:
                if len(raw) <= context_lines * 2:
                    for line in raw:
                        line_num = str(old_line_num).rjust(line_num_width)
                        output.append(f" {line_num} {line}")
                        old_line_num += 1
                        new_line_num += 1
                else:
                    leading_lines = raw[:context_lines]
                    trailing_lines = raw[len(raw) - context_lines :]
                    skipped_lines = len(raw) - len(leading_lines) - len(trailing_lines)

                    for line in leading_lines:
                        line_num = str(old_line_num).rjust(line_num_width)
                        output.append(f" {line_num} {line}")
                        old_line_num += 1
                        new_line_num += 1

                    output.append(f" {''.rjust(line_num_width)} ...")
                    old_line_num += skipped_lines
                    new_line_num += skipped_lines

                    for line in trailing_lines:
                        line_num = str(old_line_num).rjust(line_num_width)
                        output.append(f" {line_num} {line}")
                        old_line_num += 1
                        new_line_num += 1
            elif has_leading_change:
                shown_lines = raw[:context_lines]
                skipped_lines = len(raw) - len(shown_lines)

                for line in shown_lines:
                    line_num = str(old_line_num).rjust(line_num_width)
                    output.append(f" {line_num} {line}")
                    old_line_num += 1
                    new_line_num += 1

                if skipped_lines > 0:
                    output.append(f" {''.rjust(line_num_width)} ...")
                    old_line_num += skipped_lines
                    new_line_num += skipped_lines
            elif has_trailing_change:
                skipped_lines = max(0, len(raw) - context_lines)
                if skipped_lines > 0:
                    output.append(f" {''.rjust(line_num_width)} ...")
                    old_line_num += skipped_lines
                    new_line_num += skipped_lines

                for line in raw[skipped_lines:]:
                    line_num = str(old_line_num).rjust(line_num_width)
                    output.append(f" {line_num} {line}")
                    old_line_num += 1
                    new_line_num += 1
            else:
                old_line_num += len(raw)
                new_line_num += len(raw)

            last_was_change = False

    return EditDiffResult(diff="\n".join(output), first_changed_line=first_changed_line)


def compute_edits_diff(
    path: str,
    edits: list[Edit],
    cwd: str,
) -> EditDiffResult | EditDiffError:
    """Compute the diff for one or more edit operations without applying them."""
    absolute_path = resolve_to_cwd(path, cwd)

    try:
        try:
            import os

            with open(absolute_path, "rb"):
                pass
            if not os.access(absolute_path, os.R_OK):
                raise PermissionError()
        except FileNotFoundError:
            return EditDiffError(error=f"Could not edit file: {path}. Error code: ENOENT.")
        except PermissionError:
            return EditDiffError(error=f"Could not edit file: {path}. Error code: EACCES.")

        with open(absolute_path, encoding="utf-8") as fh:
            raw_content = fh.read()

        _bom, content = strip_bom(raw_content)
        normalized_content = normalize_to_lf(content)
        applied = apply_edits_to_normalized_content(normalized_content, edits, path)
        return generate_diff_string(applied.base_content, applied.new_content)
    except ValueError as err:
        return EditDiffError(error=str(err))
    except OSError as err:
        return EditDiffError(error=str(err))


def compute_edit_diff(
    path: str,
    old_text: str,
    new_text: str,
    cwd: str,
) -> EditDiffResult | EditDiffError:
    """Compute the diff for a single edit operation without applying it."""
    return compute_edits_diff(path, [Edit(old_text=old_text, new_text=new_text)], cwd)
