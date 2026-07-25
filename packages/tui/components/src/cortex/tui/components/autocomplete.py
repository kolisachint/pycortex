"""Slash-command and file-path autocomplete for the editor.

Mechanical port of hoocode's ``packages/tui/src/autocomplete.ts``.

The concrete :class:`AutocompleteItem`, :class:`AutocompleteSuggestions` and
:class:`CompletionResult` defined here are the objects the TS file builds as
plain literals; the same names in :mod:`cortex.tui.editing` are the *protocols*
the editor consumes them through (the editing leaf cannot import components).

Four things read differently from the TS, all forced by the platform:

* ``AbortSignal`` here is the convention the rest of the port uses — an object
  with a boolean ``aborted`` — so there is no ``addEventListener("abort")`` to
  hang the child-process kill off. :func:`_walk_directory_with_fd` polls the
  flag while it waits instead, and kills ``fd`` the same way.
* ``node:path`` is reimplemented (:func:`_join`, :func:`_dirname`,
  :func:`_basename`). ``posixpath`` disagrees with node exactly where this
  module leans on it: ``dirname("up")`` is ``""`` rather than ``"."``, and
  ``basename("src/")`` is ``""`` rather than ``"src"`` — which would silently
  un-rank every directory in the fuzzy results.
* ``String.prototype.localeCompare`` becomes :func:`_locale_compare_key`, an
  approximation of ICU's default collation covering the two places it disagrees
  with code-point order on filenames: it is case-insensitive first, and breaks
  ties lowercase-before-uppercase.
* ``getArgumentCompletions`` may return a value or an awaitable, as in the TS's
  ``Awaitable<T>``; the port awaits only what is actually awaitable.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, TypeAlias, cast

from cortex.tui.fuzzy import fuzzy_filter

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cortex.tui.editing import AbortSignal
    from cortex.tui.editing import AutocompleteItem as AutocompleteItemLike

__all__ = [
    "AutocompleteItem",
    "AutocompleteSuggestions",
    "CombinedAutocompleteProvider",
    "CompletionResult",
    "SlashCommand",
]

PATH_DELIMITERS = frozenset([" ", "\t", '"', "'", "="])

#: How often :func:`_walk_directory_with_fd` re-reads ``signal.aborted`` while
#: waiting on the child. The TS gets the same effect for free from an event.
_ABORT_POLL_INTERVAL_S = 0.005


# ---------------------------------------------------------------------------
# node:path (posix)
# ---------------------------------------------------------------------------


def _normalize(path: str) -> str:
    """``path.posix.normalize``."""
    if path == "":
        return "."
    is_absolute = path.startswith("/")
    trailing_separator = path.endswith("/")

    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments and segments[-1] != "..":
                segments.pop()
            elif not is_absolute:
                segments.append("..")
            continue
        segments.append(segment)

    joined = "/".join(segments)
    if is_absolute:
        joined = f"/{joined}"
    elif joined == "":
        joined = "."
    if trailing_separator and not joined.endswith("/"):
        joined += "/"
    return joined


def _join(*parts: str) -> str:
    """``path.posix.join``: empty parts drop out, and the result is normalized."""
    joined = "/".join(part for part in parts if part != "")
    if joined == "":
        return "."
    return _normalize(joined)


def _dirname(path: str) -> str:
    """``path.posix.dirname``: ``"."`` for a bare name, ``"/"`` for a root child."""
    if path == "":
        return "."
    has_root = path.startswith("/")
    end = -1
    matched_slash = True
    for i in range(len(path) - 1, 0, -1):
        if path[i] == "/":
            if not matched_slash:
                end = i
                break
        else:
            matched_slash = False

    if end == -1:
        return "/" if has_root else "."
    if has_root and end == 1:
        return "//"
    return path[:end]


def _basename(path: str) -> str:
    """``path.posix.basename``: trailing separators are ignored, not returned."""
    start = 0
    end = -1
    matched_slash = True
    for i in range(len(path) - 1, -1, -1):
        if path[i] == "/":
            if not matched_slash:
                start = i + 1
                break
        elif end == -1:
            matched_slash = False
            end = i + 1

    if end == -1:
        return ""
    return path[start:end]


def _home_dir() -> str:
    """``os.homedir()``."""
    return os.path.expanduser("~")


def _locale_compare_key(value: str) -> tuple[str, tuple[int, ...]]:
    """Sort key approximating ``a.localeCompare(b)`` for filenames."""
    return (value.casefold(), tuple(0 if ch.islower() else 1 for ch in value))


# ---------------------------------------------------------------------------
# prefix parsing
# ---------------------------------------------------------------------------


def _to_display_path(value: str) -> str:
    return value.replace("\\", "/")


def _escape_regex(value: str) -> str:
    """The TS ``escapeRegex``. Narrower than :func:`re.escape` on purpose — the
    pattern is handed to ``fd``'s Rust regex engine, not to :mod:`re`."""
    return re.sub(r"([.*+?^${}()|\[\]\\])", r"\\\1", value)


def _build_fd_path_query(query: str) -> str:
    normalized = _to_display_path(query)
    if "/" not in normalized:
        return normalized

    has_trailing_separator = normalized.endswith("/")
    trimmed = normalized.strip("/")
    if not trimmed:
        return normalized

    separator_pattern = "[\\\\/]"
    segments = [_escape_regex(segment) for segment in trimmed.split("/") if segment]
    if not segments:
        return normalized

    pattern = separator_pattern.join(segments)
    if has_trailing_separator:
        pattern += separator_pattern
    return pattern


def _find_last_delimiter(text: str) -> int:
    for i in range(len(text) - 1, -1, -1):
        if text[i] in PATH_DELIMITERS:
            return i
    return -1


def _find_unclosed_quote_start(text: str) -> int | None:
    in_quotes = False
    quote_start = -1

    for i, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
            if in_quotes:
                quote_start = i

    return quote_start if in_quotes else None


def _is_token_start(text: str, index: int) -> bool:
    return index == 0 or text[index - 1] in PATH_DELIMITERS


def _extract_quoted_prefix(text: str) -> str | None:
    quote_start = _find_unclosed_quote_start(text)
    if quote_start is None:
        return None

    if quote_start > 0 and text[quote_start - 1] == "@":
        if not _is_token_start(text, quote_start - 1):
            return None
        return text[quote_start - 1 :]

    if not _is_token_start(text, quote_start):
        return None

    return text[quote_start:]


class _ParsedPathPrefix(NamedTuple):
    raw_prefix: str
    is_at_prefix: bool
    is_quoted_prefix: bool


def _parse_path_prefix(prefix: str) -> _ParsedPathPrefix:
    if prefix.startswith('@"'):
        return _ParsedPathPrefix(prefix[2:], True, True)
    if prefix.startswith('"'):
        return _ParsedPathPrefix(prefix[1:], False, True)
    if prefix.startswith("@"):
        return _ParsedPathPrefix(prefix[1:], True, False)
    return _ParsedPathPrefix(prefix, False, False)


def _build_completion_value(
    path: str, *, is_directory: bool, is_at_prefix: bool, is_quoted_prefix: bool
) -> str:
    # `is_directory` is in the TS options object and read by neither branch; it
    # stays so the three call sites keep passing what the TS passes.
    _ = is_directory
    needs_quotes = is_quoted_prefix or " " in path
    prefix = "@" if is_at_prefix else ""

    if not needs_quotes:
        return f"{prefix}{path}"

    return f'{prefix}"{path}"'


# ---------------------------------------------------------------------------
# fd
# ---------------------------------------------------------------------------


class _FdEntry(NamedTuple):
    path: str
    is_directory: bool


class _ScopedFuzzyQuery(NamedTuple):
    base_dir: str
    query: str
    display_base: str


async def _walk_directory_with_fd(
    base_dir: str, fd_path: str, query: str, max_results: int, signal: AbortSignal
) -> list[_FdEntry]:
    """Walk the tree with ``fd`` — fast, and it respects ``.gitignore``."""
    args = [
        "--base-directory",
        base_dir,
        "--max-results",
        str(max_results),
        "--type",
        "f",
        "--type",
        "d",
        "--follow",
        "--hidden",
        "--exclude",
        ".git",
        "--exclude",
        ".git/*",
        "--exclude",
        ".git/**",
    ]

    if "/" in _to_display_path(query):
        args.append("--full-path")

    if query:
        args.append(_build_fd_path_query(query))

    if signal.aborted:
        return []

    try:
        child = await asyncio.create_subprocess_exec(
            fd_path,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        # The TS `child.on("error")` path: the binary could not be spawned.
        return []

    communicate = asyncio.ensure_future(child.communicate())
    while True:
        done, _ = await asyncio.wait({communicate}, timeout=_ABORT_POLL_INTERVAL_S)
        if done:
            break
        if signal.aborted:
            if child.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    child.kill()
            break

    stdout_bytes, _ = await communicate

    if signal.aborted or child.returncode != 0 or not stdout_bytes:
        return []

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    lines = [line for line in stdout.strip().split("\n") if line]
    results: list[_FdEntry] = []

    for line in lines:
        display_line = _to_display_path(line)
        has_trailing_separator = display_line.endswith("/")
        normalized_path = display_line[:-1] if has_trailing_separator else display_line
        if (
            normalized_path == ".git"
            or normalized_path.startswith(".git/")
            or "/.git/" in normalized_path
        ):
            continue

        results.append(_FdEntry(path=display_line, is_directory=has_trailing_separator))

    return results


# ---------------------------------------------------------------------------
# public data
# ---------------------------------------------------------------------------


@dataclass
class AutocompleteItem:
    """One offered completion."""

    value: str
    label: str
    description: str | None = None


#: The TS `Awaitable<AutocompleteItem[] | null>`: a value, or something to await.
ArgumentCompletions: TypeAlias = (
    "Awaitable[list[AutocompleteItem] | None] | list[AutocompleteItem] | None"
)


@dataclass
class SlashCommand:
    """A command the provider offers after a leading ``/``.

    ``get_argument_completions`` returns the completions for this command's
    argument, or ``None`` when it has none; it may be a coroutine.
    """

    name: str
    description: str | None = None
    argument_hint: str | None = None
    get_argument_completions: Callable[[str], ArgumentCompletions] | None = None


@dataclass
class AutocompleteSuggestions:
    """What to offer, and the text it is being matched against."""

    items: list[AutocompleteItem]
    prefix: str


@dataclass
class CompletionResult:
    """The text and cursor position after applying a completion."""

    lines: list[str]
    cursor_line: int
    cursor_col: int


class _CommandItem(NamedTuple):
    """The intermediate the TS builds before handing the list to ``fuzzyFilter``."""

    name: str
    label: str
    description: str | None


def _str_attr(command: object, attribute: str) -> str | None:
    """One ``"x" in cmd ? cmd.x : undefined``, for a command of either shape.

    The TS discriminates the union structurally and so does this: any object
    carrying the right attributes can be handed to the provider, not only the
    :class:`SlashCommand` and :class:`AutocompleteItem` this module defines.
    """
    value = getattr(command, attribute, None)
    return value if isinstance(value, str) else None


def _command_name(command: object) -> str:
    """``"name" in cmd ? cmd.name : cmd.value``."""
    name = _str_attr(command, "name")
    if name is not None:
        return name
    return _str_attr(command, "value") or ""


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------


class CombinedAutocompleteProvider:
    """Handles both slash commands and file paths.

    Satisfies :class:`cortex.tui.editing.AutocompleteProvider` structurally.
    """

    def __init__(
        self,
        commands: Sequence[SlashCommand | AutocompleteItem],
        base_path: str,
        fd_path: str | None = None,
    ) -> None:
        self.commands = list(commands)
        self.base_path = base_path
        self.fd_path = fd_path

    async def get_suggestions(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        *,
        signal: AbortSignal,
        force: bool = False,
    ) -> AutocompleteSuggestions | None:
        """Suggestions for the current text and cursor, or ``None`` if there are none."""
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        text_before_cursor = current_line[:cursor_col]

        at_prefix = self._extract_at_prefix(text_before_cursor)
        if at_prefix:
            parsed = _parse_path_prefix(at_prefix)
            suggestions = await self._get_fuzzy_file_suggestions(
                parsed.raw_prefix, is_quoted_prefix=parsed.is_quoted_prefix, signal=signal
            )
            if len(suggestions) == 0:
                return None

            return AutocompleteSuggestions(items=suggestions, prefix=at_prefix)

        if not force and text_before_cursor.startswith("/"):
            space_index = text_before_cursor.find(" ")

            if space_index == -1:
                prefix = text_before_cursor[1:]
                command_items: list[_CommandItem] = []
                for cmd in self.commands:
                    name = _command_name(cmd)
                    hint = _str_attr(cmd, "argument_hint") or None
                    desc = _str_attr(cmd, "description") or ""
                    full_desc = (f"{hint} — {desc}" if desc else hint) if hint else desc
                    command_items.append(
                        _CommandItem(name=name, label=name, description=full_desc or None)
                    )

                filtered = [
                    AutocompleteItem(
                        value=item.name, label=item.label, description=item.description or None
                    )
                    for item in fuzzy_filter(command_items, prefix, lambda item: item.name)
                ]

                if len(filtered) == 0:
                    return None

                return AutocompleteSuggestions(items=filtered, prefix=text_before_cursor)

            command_name = text_before_cursor[1:space_index]
            argument_text = text_before_cursor[space_index + 1 :]

            command = next(
                (cmd for cmd in self.commands if _command_name(cmd) == command_name),
                None,
            )
            get_argument_completions = getattr(command, "get_argument_completions", None)
            if command is None or not callable(get_argument_completions):
                return None

            argument_suggestions: object = get_argument_completions(argument_text)
            if inspect.isawaitable(argument_suggestions):
                argument_suggestions = await argument_suggestions
            if not isinstance(argument_suggestions, list) or len(argument_suggestions) == 0:
                return None

            return AutocompleteSuggestions(
                items=cast("list[AutocompleteItem]", argument_suggestions), prefix=argument_text
            )

        path_match = self._extract_path_prefix(text_before_cursor, force)
        if path_match is None:
            return None

        suggestions = self._get_file_suggestions(path_match)
        if len(suggestions) == 0:
            return None

        return AutocompleteSuggestions(items=suggestions, prefix=path_match)

    def apply_completion(
        self,
        lines: list[str],
        cursor_line: int,
        cursor_col: int,
        item: AutocompleteItemLike,
        prefix: str,
    ) -> CompletionResult:
        """Replace ``prefix`` with the selected item, and place the cursor after it.

        ``item`` is typed as the editing leaf's *protocol* rather than the
        dataclass above: the editor hands back whatever the provider produced,
        and a parameter narrower than the interface it implements would not
        satisfy :class:`cortex.tui.editing.AutocompleteProvider` at all (TS
        method parameters are bivariant; Python's are not).
        """
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        before_prefix = current_line[: cursor_col - len(prefix)]
        after_cursor = current_line[cursor_col:]
        is_quoted_prefix = prefix.startswith('"') or prefix.startswith('@"')
        has_leading_quote_after_cursor = after_cursor.startswith('"')
        has_trailing_quote_in_item = item.value.endswith('"')
        adjusted_after_cursor = (
            after_cursor[1:]
            if is_quoted_prefix and has_trailing_quote_in_item and has_leading_quote_after_cursor
            else after_cursor
        )

        # A slash command, as opposed to a file path: at the start of the line,
        # with no path separator after the leading "/".
        is_slash_command = (
            prefix.startswith("/") and before_prefix.strip() == "" and "/" not in prefix[1:]
        )
        if is_slash_command:
            new_line = f"{before_prefix}/{item.value} {adjusted_after_cursor}"
            new_lines = list(lines)
            new_lines[cursor_line] = new_line

            return CompletionResult(
                lines=new_lines,
                cursor_line=cursor_line,
                # +2 for the "/" and the trailing space
                cursor_col=len(before_prefix) + len(item.value) + 2,
            )

        if prefix.startswith("@"):
            # A file attachment. No space after a directory, so the user can keep
            # completing into it.
            is_directory = item.label.endswith("/")
            suffix = "" if is_directory else " "
            new_line = f"{before_prefix + item.value}{suffix}{adjusted_after_cursor}"
            new_lines = list(lines)
            new_lines[cursor_line] = new_line

            has_trailing_quote = item.value.endswith('"')
            cursor_offset = (
                len(item.value) - 1 if is_directory and has_trailing_quote else len(item.value)
            )

            return CompletionResult(
                lines=new_lines,
                cursor_line=cursor_line,
                cursor_col=len(before_prefix) + cursor_offset + len(suffix),
            )

        text_before_cursor = current_line[:cursor_col]
        if "/" in text_before_cursor and " " in text_before_cursor:
            # Likely a command argument.
            new_line = before_prefix + item.value + adjusted_after_cursor
            new_lines = list(lines)
            new_lines[cursor_line] = new_line

            is_directory = item.label.endswith("/")
            has_trailing_quote = item.value.endswith('"')
            cursor_offset = (
                len(item.value) - 1 if is_directory and has_trailing_quote else len(item.value)
            )

            return CompletionResult(
                lines=new_lines,
                cursor_line=cursor_line,
                cursor_col=len(before_prefix) + cursor_offset,
            )

        # A plain file path.
        new_line = before_prefix + item.value + adjusted_after_cursor
        new_lines = list(lines)
        new_lines[cursor_line] = new_line

        is_directory = item.label.endswith("/")
        has_trailing_quote = item.value.endswith('"')
        cursor_offset = (
            len(item.value) - 1 if is_directory and has_trailing_quote else len(item.value)
        )

        return CompletionResult(
            lines=new_lines,
            cursor_line=cursor_line,
            cursor_col=len(before_prefix) + cursor_offset,
        )

    def _extract_at_prefix(self, text: str) -> str | None:
        """The ``@`` prefix that drives fuzzy file suggestions."""
        quoted_prefix = _extract_quoted_prefix(text)
        if quoted_prefix is not None and quoted_prefix.startswith('@"'):
            return quoted_prefix

        last_delimiter_index = _find_last_delimiter(text)
        token_start = 0 if last_delimiter_index == -1 else last_delimiter_index + 1

        if token_start < len(text) and text[token_start] == "@":
            return text[token_start:]

        return None

    def _extract_path_prefix(self, text: str, force_extract: bool = False) -> str | None:
        """The path-like prefix in the text before the cursor."""
        quoted_prefix = _extract_quoted_prefix(text)
        if quoted_prefix is not None:
            return quoted_prefix

        last_delimiter_index = _find_last_delimiter(text)
        path_prefix = text if last_delimiter_index == -1 else text[last_delimiter_index + 1 :]

        # Forced extraction (the Tab key) always returns something.
        if force_extract:
            return path_prefix

        # Otherwise only when it looks like a path.
        if "/" in path_prefix or path_prefix.startswith(".") or path_prefix.startswith("~/"):
            return path_prefix

        # An empty prefix counts only after a space — completely empty text is
        # for forced Tab completion, not for a natural trigger.
        if path_prefix == "" and text.endswith(" "):
            return path_prefix

        return None

    def _expand_home_path(self, path: str) -> str:
        if path.startswith("~/"):
            expanded_path = _join(_home_dir(), path[2:])
            # Preserve a trailing slash the original had.
            return (
                f"{expanded_path}/"
                if path.endswith("/") and not expanded_path.endswith("/")
                else expanded_path
            )
        elif path == "~":
            return _home_dir()
        return path

    def _resolve_scoped_fuzzy_query(self, raw_query: str) -> _ScopedFuzzyQuery | None:
        normalized_query = _to_display_path(raw_query)
        slash_index = normalized_query.rfind("/")
        if slash_index == -1:
            return None

        display_base = normalized_query[: slash_index + 1]
        query = normalized_query[slash_index + 1 :]

        base_dir: str
        if display_base.startswith("~/"):
            base_dir = self._expand_home_path(display_base)
        elif display_base.startswith("/"):
            base_dir = display_base
        else:
            base_dir = _join(self.base_path, display_base)

        if not os.path.isdir(base_dir):
            return None

        return _ScopedFuzzyQuery(base_dir=base_dir, query=query, display_base=display_base)

    def _scoped_path_for_display(self, display_base: str, relative_path: str) -> str:
        normalized_relative_path = _to_display_path(relative_path)
        if display_base == "/":
            return f"/{normalized_relative_path}"
        return f"{_to_display_path(display_base)}{normalized_relative_path}"

    def _get_file_suggestions(self, prefix: str) -> list[AutocompleteItem]:
        """File and directory suggestions for a path prefix."""
        try:
            search_dir: str
            search_prefix: str
            raw_prefix, is_at_prefix, is_quoted_prefix = _parse_path_prefix(prefix)
            expanded_prefix = raw_prefix

            if expanded_prefix.startswith("~"):
                expanded_prefix = self._expand_home_path(expanded_prefix)

            is_root_prefix = (
                raw_prefix == ""
                or raw_prefix == "./"
                or raw_prefix == "../"
                or raw_prefix == "~"
                or raw_prefix == "~/"
                or raw_prefix == "/"
                or (is_at_prefix and raw_prefix == "")
            )

            if is_root_prefix:
                # Complete from the given position.
                if raw_prefix.startswith("~") or expanded_prefix.startswith("/"):
                    search_dir = expanded_prefix
                else:
                    search_dir = _join(self.base_path, expanded_prefix)
                search_prefix = ""
            elif raw_prefix.endswith("/"):
                # A prefix ending in "/" shows that directory's contents.
                if raw_prefix.startswith("~") or expanded_prefix.startswith("/"):
                    search_dir = expanded_prefix
                else:
                    search_dir = _join(self.base_path, expanded_prefix)
                search_prefix = ""
            else:
                # Split into a directory and a file prefix.
                directory = _dirname(expanded_prefix)
                file = _basename(expanded_prefix)
                if raw_prefix.startswith("~") or expanded_prefix.startswith("/"):
                    search_dir = directory
                else:
                    search_dir = _join(self.base_path, directory)
                search_prefix = file

            with os.scandir(search_dir) as scan:
                entries = list(scan)
            suggestions: list[AutocompleteItem] = []

            for entry in entries:
                if not entry.name.lower().startswith(search_prefix.lower()):
                    continue

                # A directory, or a symlink pointing at one.
                is_directory = entry.is_dir(follow_symlinks=False)
                if not is_directory and entry.is_symlink():
                    try:
                        full_path = _join(search_dir, entry.name)
                        is_directory = os.path.isdir(full_path)
                    except OSError:
                        # Broken symlink or permission error — treat it as a file.
                        pass

                relative_path: str
                name = entry.name
                display_prefix = raw_prefix

                if display_prefix.endswith("/"):
                    relative_path = display_prefix + name
                elif "/" in display_prefix or "\\" in display_prefix:
                    if display_prefix.startswith("~/"):
                        # Keep the ~/ form for home-relative paths.
                        home_relative_dir = display_prefix[2:]
                        directory = _dirname(home_relative_dir)
                        relative_path = (
                            f"~/{name}" if directory == "." else f"~/{_join(directory, name)}"
                        )
                    elif display_prefix.startswith("/"):
                        directory = _dirname(display_prefix)
                        if directory == "/":
                            relative_path = f"/{name}"
                        else:
                            relative_path = f"{directory}/{name}"
                    else:
                        relative_path = _join(_dirname(display_prefix), name)
                        # join() normalizes the "./" away; put it back.
                        if display_prefix.startswith("./") and not relative_path.startswith("./"):
                            relative_path = f"./{relative_path}"
                else:
                    # A standalone entry keeps the ~/ the prefix had.
                    if display_prefix.startswith("~"):
                        relative_path = f"~/{name}"
                    else:
                        relative_path = name

                relative_path = _to_display_path(relative_path)
                path_value = f"{relative_path}/" if is_directory else relative_path
                value = _build_completion_value(
                    path_value,
                    is_directory=is_directory,
                    is_at_prefix=is_at_prefix,
                    is_quoted_prefix=is_quoted_prefix,
                )

                suggestions.append(
                    AutocompleteItem(value=value, label=name + ("/" if is_directory else ""))
                )

            # Directories first, then alphabetically. The directory test is on
            # `value`, so a quoted directory — whose value ends in `"` — sorts
            # with the files. That is what the TS does.
            suggestions.sort(
                key=lambda item: (
                    0 if item.value.endswith("/") else 1,
                    _locale_compare_key(item.label),
                )
            )

            return suggestions
        except OSError:
            # The directory does not exist, or is not accessible.
            return []

    def _score_entry(self, file_path: str, query: str, is_directory: bool) -> int:
        """Score an entry against the query; higher is better, 0 is no match."""
        file_name = _basename(file_path)
        lower_file_name = file_name.lower()
        lower_query = query.lower()

        score = 0

        if lower_file_name == lower_query:
            score = 100
        elif lower_file_name.startswith(lower_query):
            score = 80
        elif lower_query in lower_file_name:
            score = 50
        elif lower_query in file_path.lower():
            score = 30

        # Directories get a bonus so they appear first.
        if is_directory and score > 0:
            score += 10

        return score

    async def _get_fuzzy_file_suggestions(
        self, query: str, *, is_quoted_prefix: bool, signal: AbortSignal
    ) -> list[AutocompleteItem]:
        """Fuzzy file search with ``fd`` — fast, and it respects ``.gitignore``."""
        if not self.fd_path or signal.aborted:
            return []

        try:
            scoped_query = self._resolve_scoped_fuzzy_query(query)
            fd_base_dir = scoped_query.base_dir if scoped_query else self.base_path
            fd_query = scoped_query.query if scoped_query else query
            entries = await _walk_directory_with_fd(
                fd_base_dir, self.fd_path, fd_query, 100, signal
            )
            if signal.aborted:
                return []

            scored_entries = [
                (
                    entry,
                    self._score_entry(entry.path, fd_query, entry.is_directory) if fd_query else 1,
                )
                for entry in entries
            ]
            scored_entries = [pair for pair in scored_entries if pair[1] > 0]

            scored_entries.sort(key=lambda pair: -pair[1])
            top_entries = scored_entries[:20]

            suggestions: list[AutocompleteItem] = []
            for entry, _score in top_entries:
                entry_path = entry.path
                is_directory = entry.is_directory
                path_without_slash = entry_path[:-1] if is_directory else entry_path
                display_path = (
                    self._scoped_path_for_display(scoped_query.display_base, path_without_slash)
                    if scoped_query
                    else path_without_slash
                )
                entry_name = _basename(path_without_slash)
                completion_path = f"{display_path}/" if is_directory else display_path
                value = _build_completion_value(
                    completion_path,
                    is_directory=is_directory,
                    is_at_prefix=True,
                    is_quoted_prefix=is_quoted_prefix,
                )

                suggestions.append(
                    AutocompleteItem(
                        value=value,
                        label=entry_name + ("/" if is_directory else ""),
                        description=display_path,
                    )
                )

            return suggestions
        except OSError:
            return []

    def should_trigger_file_completion(
        self, lines: list[str], cursor_line: int, cursor_col: int
    ) -> bool:
        """Should the Tab key trigger file completion here?"""
        current_line = lines[cursor_line] if 0 <= cursor_line < len(lines) else ""
        text_before_cursor = current_line[:cursor_col]

        # Not while a slash command is being typed at the start of the line.
        if text_before_cursor.strip().startswith("/") and " " not in text_before_cursor.strip():
            return False

        return True
