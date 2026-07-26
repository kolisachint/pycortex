# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportCallIssue=false
"""Native (pure-Python) fallbacks for the ``find`` and ``grep`` tools.

Mechanical port of ``core/tools/native-search.ts``. The TS tools normally shell
out to ``fd`` / ``rg``; this reproduces the essential behaviour in Python so
search works without those binaries:

- hierarchical ``.gitignore`` handling (each ``.gitignore`` scoped to its own
  subtree, matching fd's ``--no-require-git`` behaviour),
- hidden files included,
- ``.git`` always skipped; ``node_modules`` skipped for ``find`` (mirrors the
  tool's built-in excludes) but left to ``.gitignore`` for ``grep``.

These are best-effort approximations, not byte-for-byte fd/rg parity: globs are
matched with a minimatch-style translator and patterns with Python ``re``, and
only ``.gitignore`` files are honoured (not ``.ignore`` or global excludes).
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from cortex.code.tools.fd_utils import to_posix_path

EntryType = Literal["f", "d", "l"]

MAX_ENTRIES = 200_000
"""Hard cap on entries enumerated during a single walk."""

MAX_GREP_FILE_BYTES = 20 * 1024 * 1024
"""Files larger than this are skipped by the grep fallback."""


# ---------------------------------------------------------------------------
# minimatch-style glob translation
# ---------------------------------------------------------------------------


def _segment_regex(seg: str, dot: bool) -> str:
    """Translate one path segment (no slashes) to a regex fragment."""
    res = ""
    if not dot and not seg.startswith("."):
        res += r"(?!\.)"
    i = 0
    brace_depth = 0
    while i < len(seg):
        c = seg[i]
        if c == "*":
            res += "[^/]*"
        elif c == "?":
            res += "[^/]"
        elif c == "[":
            j = i + 1
            if j < len(seg) and seg[j] in ("!", "^"):
                j += 1
            if j < len(seg) and seg[j] == "]":
                j += 1
            while j < len(seg) and seg[j] != "]":
                j += 1
            if j >= len(seg):
                res += re.escape("[")
            else:
                inner = seg[i + 1 : j]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                res += "[" + inner + "]"
                i = j
        elif c == "{":
            brace_depth += 1
            res += "(?:"
        elif c == "}" and brace_depth > 0:
            brace_depth -= 1
            res += ")"
        elif c == "," and brace_depth > 0:
            res += "|"
        else:
            res += re.escape(c)
        i += 1
    return res


def _glob_to_regex(pattern: str, dot: bool) -> str:
    """Translate a minimatch glob to a full-match regex string."""
    segs = pattern.split("/")
    n = len(segs)
    out = ""
    for i, seg in enumerate(segs):
        is_last = i == n - 1
        if seg == "**":
            if is_last:
                out += ".*"
            else:
                out += "(?:.*/)?"
        else:
            out += _segment_regex(seg, dot)
            if not is_last:
                out += "/"
    return out


def minimatch(
    path: str,
    pattern: str,
    *,
    dot: bool = False,
    nocase: bool = False,
    matchbase: bool = False,
) -> bool:
    """Minimatch-style glob match against a POSIX path."""
    target = path
    pat = pattern
    if matchbase and "/" not in pattern:
        target = path.rsplit("/", 1)[-1]
    flags = re.IGNORECASE if nocase else 0
    try:
        regex = re.compile(_glob_to_regex(pat, dot), flags)
    except re.error:
        return False
    return regex.fullmatch(target) is not None


# ---------------------------------------------------------------------------
# .gitignore matching
# ---------------------------------------------------------------------------


@dataclass
class _GitignoreRule:
    regex: re.Pattern[str]
    dir_only: bool
    negation: bool


class Gitignore:
    """A minimal ``.gitignore`` matcher scoped to a single directory."""

    def __init__(self, content: str) -> None:
        self._rules: list[_GitignoreRule] = []
        for raw in content.split("\n"):
            line = raw.rstrip("\r")
            if line.strip() == "" or line.lstrip().startswith("#"):
                continue
            self._rules.append(self._compile(line))

    @staticmethod
    def _compile(pattern: str) -> _GitignoreRule:
        negation = pattern.startswith("!")
        if negation:
            pattern = pattern[1:]
        dir_only = pattern.endswith("/")
        if dir_only:
            pattern = pattern[:-1]
        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern[1:]
        # A slash anywhere in the remaining pattern anchors it to the base.
        if "/" in pattern:
            anchored = True
        body = _glob_to_regex(pattern, dot=True)
        source = body if anchored else "(?:.*/)?" + body
        return _GitignoreRule(re.compile(source), dir_only, negation)

    def ignores(self, path: str) -> bool:
        """Whether ``path`` (POSIX, trailing slash for dirs) is ignored."""
        is_dir = path.endswith("/")
        clean = path.rstrip("/")
        if not clean:
            return False
        segs = clean.split("/")
        result = False
        for rule in self._rules:
            for i in range(len(segs)):
                prefix = "/".join(segs[: i + 1])
                is_prefix_dir = (i < len(segs) - 1) or is_dir
                if rule.dir_only and not is_prefix_dir:
                    continue
                if rule.regex.fullmatch(prefix):
                    result = not rule.negation
                    break
        return result


@dataclass
class _GitignoreMatcher:
    base_dir: str
    ig: Gitignore


def _load_gitignore(directory: str) -> Gitignore | None:
    try:
        with open(os.path.join(directory, ".gitignore"), encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return None
    return Gitignore(content)


def _is_git_ignored(abs_path: str, is_dir: bool, matchers: list[_GitignoreMatcher]) -> bool:
    for m in matchers:
        rel = os.path.relpath(abs_path, m.base_dir)
        if rel == "" or rel.startswith("..") or os.path.isabs(rel):
            continue
        rel_posix = to_posix_path(rel) + ("/" if is_dir else "")
        if m.ig.ignores(rel_posix):
            return True
    return False


# ---------------------------------------------------------------------------
# Directory walk
# ---------------------------------------------------------------------------


@dataclass
class CollectedEntry:
    abs: str
    rel: str
    type: EntryType


def collect_entries(
    root: str,
    *,
    max_depth: int | None = None,
    signal: Any = None,
    always_skip_dirs: set[str] | None = None,
) -> list[CollectedEntry]:
    """Walk ``root`` depth-first, honouring ``.gitignore`` and skip dirs.

    Symlinks are reported but never followed. Enumeration stops at MAX_ENTRIES.
    """
    out: list[CollectedEntry] = []
    always_skip = always_skip_dirs if always_skip_dirs is not None else {".git"}

    root_matchers: list[_GitignoreMatcher] = []
    root_ig = _load_gitignore(root)
    if root_ig:
        root_matchers.append(_GitignoreMatcher(root, root_ig))

    stack: list[tuple[str, int, list[_GitignoreMatcher]]] = [(root, 0, root_matchers)]

    while stack:
        if len(out) >= MAX_ENTRIES or (signal is not None and getattr(signal, "aborted", False)):
            break
        directory, depth, matchers = stack.pop()

        try:
            dirents = list(os.scandir(directory))
        except OSError:
            continue

        entry_depth = depth + 1
        for dirent in dirents:
            if len(out) >= MAX_ENTRIES:
                break

            name = dirent.name
            abs_path = os.path.join(directory, name)

            is_dir = False
            if dirent.is_symlink():
                entry_type: EntryType = "l"
            elif dirent.is_dir(follow_symlinks=False):
                entry_type = "d"
                is_dir = True
            elif dirent.is_file(follow_symlinks=False):
                entry_type = "f"
            else:
                continue

            if is_dir and name in always_skip:
                continue
            if _is_git_ignored(abs_path, is_dir, matchers):
                continue

            if max_depth is None or entry_depth <= max_depth:
                out.append(
                    CollectedEntry(
                        abs=abs_path,
                        rel=to_posix_path(os.path.relpath(abs_path, root)),
                        type=entry_type,
                    )
                )

            if is_dir and (max_depth is None or entry_depth < max_depth):
                child_ig = _load_gitignore(abs_path)
                next_matchers = (
                    [*matchers, _GitignoreMatcher(abs_path, child_ig)] if child_ig else matchers
                )
                stack.append((abs_path, entry_depth, next_matchers))

    return out


# ---------------------------------------------------------------------------
# Glob validation (mirror fd/globset error messages)
# ---------------------------------------------------------------------------


def validate_glob(pattern: str) -> None:
    """Reject globs fd's parser would reject (unclosed ``[`` or ``{``)."""
    in_class = False
    brace_depth = 0
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 1
        elif in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
        elif c == "{":
            brace_depth += 1
        elif c == "}":
            if brace_depth > 0:
                brace_depth -= 1
        i += 1
    if in_class:
        raise ValueError(f"error parsing glob '{pattern}': unclosed character class; missing ']'")
    if brace_depth > 0:
        raise ValueError(f"error parsing glob '{pattern}': unclosed alternate group; missing '}}'")


def _is_smart_case_insensitive(pattern: str) -> bool:
    """fd smart-case: a pattern without uppercase letters matches case-insensitively."""
    return not re.search(r"[A-Z]", pattern)


def _matches_find_pattern(rel_posix: str, pattern: str) -> bool:
    """Match a ``find`` glob against a POSIX relative path (fd semantics)."""
    nocase = _is_smart_case_insensitive(pattern)
    if "/" in pattern:
        p = pattern
        if p.startswith("/"):
            p = p[1:]
        elif not p.startswith("**/") and p != "**":
            p = f"**/{p}"
        return minimatch(rel_posix, p, dot=True, nocase=nocase)
    return minimatch(rel_posix, pattern, dot=True, matchbase=True, nocase=nocase)


# ---------------------------------------------------------------------------
# native find
# ---------------------------------------------------------------------------


@dataclass
class NativeFindOptions:
    patterns: list[str]
    type: EntryType
    exclude_globs: list[str]
    max_depth: int | None = None
    always_skip_dirs: set[str] | None = None
    signal: Any = None


def native_find(root: str, opts: NativeFindOptions) -> list[str]:
    """Native replacement for the fd-backed search.

    Returns POSIX paths relative to ``root``, with a trailing slash on
    directories, unsorted/undeduped — the caller applies its own dedupe/sort.
    """
    for pattern in opts.patterns:
        validate_glob(pattern)
    for glob in opts.exclude_globs:
        validate_glob(glob)

    entries = collect_entries(
        root,
        max_depth=opts.max_depth,
        signal=opts.signal,
        always_skip_dirs=opts.always_skip_dirs,
    )

    results: list[str] = []
    for entry in entries:
        if entry.type != opts.type:
            continue
        if any(minimatch(entry.rel, g, dot=True) for g in opts.exclude_globs):
            continue
        if not any(_matches_find_pattern(entry.rel, pat) for pat in opts.patterns):
            continue
        results.append(f"{entry.rel}/" if entry.type == "d" else entry.rel)
    return results


# ---------------------------------------------------------------------------
# native grep
# ---------------------------------------------------------------------------


@dataclass
class NativeGrepMatch:
    filePath: str
    lineNumber: int
    lineText: str


@dataclass
class NativeGrepOptions:
    pattern: str
    isDirectory: bool
    limit: int
    readFile: Callable[[str], str | Awaitable[str]]
    ignoreCase: bool = False
    literal: bool = False
    glob: str | None = None
    signal: Any = None


@dataclass
class NativeGrepResult:
    matches: list[NativeGrepMatch]
    matchLimitReached: bool


class InvalidRegexError(Exception):
    """A non-literal grep pattern that is not a valid regex."""

    invalid_regex = True


def _looks_binary(content: str) -> bool:
    sample_length = min(len(content), 8192)
    return "\x00" in content[:sample_length]


async def native_grep(root: str, opts: NativeGrepOptions) -> NativeGrepResult:
    """Native replacement for the rg-backed content search.

    Raises :class:`InvalidRegexError` when a non-literal pattern is not a valid
    regex, so the caller can surface the "pass literal: true" hint.
    """
    flags = re.IGNORECASE if opts.ignoreCase else 0
    if opts.literal:
        regex = re.compile(re.escape(opts.pattern), flags)
    else:
        try:
            regex = re.compile(opts.pattern, flags)
        except re.error as e:
            raise InvalidRegexError(str(e)) from e

    if not opts.isDirectory:
        files = [root]
    else:
        entries = collect_entries(root, signal=opts.signal)
        files = [
            e.abs
            for e in entries
            if e.type == "f"
            and (
                opts.glob is None
                or minimatch(e.rel, opts.glob, dot=True, matchbase="/" not in opts.glob)
            )
        ]

    matches: list[NativeGrepMatch] = []
    match_limit_reached = False

    for file_path in files:
        if (opts.signal is not None and getattr(opts.signal, "aborted", False)) or len(
            matches
        ) >= opts.limit:
            break

        try:
            if os.path.getsize(file_path) > MAX_GREP_FILE_BYTES:
                continue
        except OSError:
            continue

        try:
            result = opts.readFile(file_path)
            content = await result if hasattr(result, "__await__") else result  # type: ignore[misc]
        except OSError:
            continue
        if _looks_binary(content):
            continue

        # content is str here (awaitable resolved above)
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")  # type: ignore[union-attr]
        for i, line in enumerate(lines):
            if regex.search(line):
                matches.append(NativeGrepMatch(filePath=file_path, lineNumber=i + 1, lineText=line))
                if len(matches) >= opts.limit:
                    match_limit_reached = True
                    break

    return NativeGrepResult(matches=matches, matchLimitReached=match_limit_reached)


def is_native_search_forced() -> bool:
    """Whether the native search path is forced regardless of fd/rg availability."""
    return os.environ.get("HOOCODE_NATIVE_SEARCH") == "1"
