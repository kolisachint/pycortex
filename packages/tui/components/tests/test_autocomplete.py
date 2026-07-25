"""Autocomplete tests, ported from hoocode's `test/autocomplete.test.ts`.

The exhaustive contract lives in `test_autocomplete_parity.py`, which diffs the
provider against results captured from the real TS. These are the TS's own unit
tests, kept in its order, plus the cases mutation testing showed the corpus
could not tell apart.

The `@` tests need `fd` on PATH, exactly as the TS suite does (Debian ships it
as `fdfind`, which is why both names are tried).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from cortex.tui.components.autocomplete import (
    AutocompleteItem,
    AutocompleteSuggestions,
    CombinedAutocompleteProvider,
    SlashCommand,
    _basename,  # pyright: ignore[reportPrivateUsage]
    _build_fd_path_query,  # pyright: ignore[reportPrivateUsage]
    _dirname,  # pyright: ignore[reportPrivateUsage]
    _extract_quoted_prefix,  # pyright: ignore[reportPrivateUsage]
    _join,  # pyright: ignore[reportPrivateUsage]
    _locale_compare_key,  # pyright: ignore[reportPrivateUsage]
    _parse_path_prefix,  # pyright: ignore[reportPrivateUsage]
)

FD_PATH = shutil.which("fd") or shutil.which("fdfind")
needs_fd = pytest.mark.skipif(FD_PATH is None, reason="fd is not installed")


class _Signal:
    def __init__(self, aborted: bool = False) -> None:
        self.aborted = aborted


def setup_folder(
    base_dir: Path,
    dirs: list[str] | None = None,
    files: dict[str, str] | None = None,
) -> None:
    for directory in dirs or []:
        (base_dir / directory).mkdir(parents=True, exist_ok=True)
    for file_path, contents in (files or {}).items():
        full_path = base_dir / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(contents)


async def get_suggestions(
    provider: CombinedAutocompleteProvider,
    lines: list[str],
    cursor_line: int,
    cursor_col: int,
    force: bool = False,
) -> AutocompleteSuggestions | None:
    return await provider.get_suggestions(
        lines, cursor_line, cursor_col, signal=_Signal(), force=force
    )


def values(result: AutocompleteSuggestions | None) -> list[str]:
    return [item.value for item in (result.items if result else [])]


def require_fd() -> str:
    assert FD_PATH is not None
    return FD_PATH


class TestExtractPathPrefix:
    async def test_extracts_slash_from_hey_slash_when_forced(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        result = await get_suggestions(provider, ["hey /"], 0, 5, True)

        assert result is not None, "should return suggestions for the root directory"
        assert result.prefix == "/"

    async def test_extracts_slash_a_from_slash_a_when_forced(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        result = await get_suggestions(provider, ["/A"], 0, 2, True)

        # May be None when nothing in / matches, which is fine — this is about
        # prefix extraction.
        if result is not None:
            assert result.prefix == "/A"

    async def test_does_not_trigger_for_slash_commands(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        result = await get_suggestions(provider, ["/model"], 0, 6, True)

        assert result is None

    async def test_triggers_for_absolute_paths_after_a_slash_command_argument(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        result = await get_suggestions(provider, ["/command /"], 0, 10, True)

        assert result is not None, "should trigger for absolute paths in command arguments"
        assert result.prefix == "/"


@needs_fd
class TestFdAtFileSuggestions:
    @pytest.fixture(autouse=True)
    def _dirs(self, tmp_path: Path) -> None:  # pyright: ignore[reportUnusedFunction]
        self.root_dir = tmp_path
        self.base_dir = tmp_path / "cwd"
        self.outside_dir = tmp_path / "outside"
        self.base_dir.mkdir(parents=True)
        self.outside_dir.mkdir(parents=True)

    def provider(self) -> CombinedAutocompleteProvider:
        return CombinedAutocompleteProvider([], str(self.base_dir), require_fd())

    async def test_returns_all_files_and_folders_for_an_empty_at_query(self) -> None:
        setup_folder(self.base_dir, dirs=["src"], files={"README.md": "readme"})

        line = "@"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert sorted(values(result)) == sorted(["@README.md", "@src/"])

    async def test_matches_a_file_with_an_extension_in_the_query(self) -> None:
        setup_folder(self.base_dir, files={"file.txt": "content"})

        line = "@file.txt"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@file.txt" in values(result)

    async def test_filters_are_case_insensitive(self) -> None:
        setup_folder(self.base_dir, dirs=["src"], files={"README.md": "readme"})

        line = "@re"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert sorted(values(result)) == ["@README.md"]

    async def test_ranks_directories_before_files(self) -> None:
        setup_folder(self.base_dir, dirs=["src"], files={"src.txt": "text"})

        line = "@src"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert values(result)[0] == "@src/"
        assert "@src.txt" in values(result)

    async def test_returns_nested_file_paths(self) -> None:
        setup_folder(self.base_dir, files={"src/index.ts": "export {};\n"})

        line = "@index"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@src/index.ts" in values(result)

    async def test_matches_deeply_nested_paths(self) -> None:
        setup_folder(
            self.base_dir,
            files={
                "packages/tui/src/autocomplete.ts": "export {};",
                "packages/ai/src/autocomplete.ts": "export {};",
            },
        )

        line = "@tui/src/auto"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@packages/tui/src/autocomplete.ts" in values(result)
        assert "@packages/ai/src/autocomplete.ts" not in values(result)

    async def test_matches_a_directory_in_the_middle_of_a_path_with_full_path(self) -> None:
        setup_folder(
            self.base_dir,
            files={
                "src/components/Button.tsx": "export {};",
                "src/utils/helpers.ts": "export {};",
            },
        )

        line = "@components/"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@src/components/Button.tsx" in values(result)
        assert "@src/utils/helpers.ts" not in values(result)

    async def test_scopes_the_search_to_a_relative_directory_and_recurses(self) -> None:
        setup_folder(
            self.outside_dir,
            files={
                "nested/alpha.ts": "export {};",
                "nested/deeper/also-alpha.ts": "export {};",
                "nested/deeper/zzz.ts": "export {};",
            },
        )

        line = "@../outside/a"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@../outside/nested/alpha.ts" in values(result)
        assert "@../outside/nested/deeper/also-alpha.ts" in values(result)
        assert "@../outside/nested/deeper/zzz.ts" not in values(result)

    async def test_quotes_paths_with_spaces_for_at_suggestions(self) -> None:
        setup_folder(self.base_dir, dirs=["my folder"], files={"my folder/test.txt": "content"})

        line = "@my"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert '@"my folder/"' in values(result)

    async def test_includes_hidden_paths_but_excludes_dot_git(self) -> None:
        setup_folder(
            self.base_dir,
            dirs=[".hoocode", ".github", ".git"],
            files={
                ".hoocode/config.json": "{}",
                ".github/workflows/ci.yml": "name: ci",
                ".git/config": "[core]",
            },
        )

        line = "@"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@.hoocode/" in values(result)
        assert "@.github/" in values(result)
        assert not any(value == "@.git" or value.startswith("@.git/") for value in values(result))

    async def test_follows_symlinked_directories_for_fuzzy_at_search(self) -> None:
        setup_folder(self.base_dir, files={"dir/some_file.txt": "real"})
        setup_folder(self.outside_dir, files={"some_file.txt": "symlinked"})
        os.symlink("../outside", self.base_dir / "symlinked_dir")

        line = "@some"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@dir/some_file.txt" in values(result)
        assert "@symlinked_dir/some_file.txt" in values(result)

    async def test_returns_symlinked_directories_when_matching_their_name(self) -> None:
        setup_folder(self.outside_dir, files={"nested/file.txt": "symlinked"})
        os.symlink("../outside", self.base_dir / "symlinked_dir")

        line = "@symlinked"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@symlinked_dir/" in values(result)

    async def test_returns_symlinked_files_without_requiring_type_l(self) -> None:
        setup_folder(self.base_dir, files={"original.txt": "content"})
        os.symlink("original.txt", self.base_dir / "link.txt")

        line = "@link"
        result = await get_suggestions(self.provider(), [line], 0, len(line))

        assert "@link.txt" in values(result)

    async def test_returns_the_same_suggestions_when_the_cwd_path_contains_the_query(self) -> None:
        normal_base_dir = self.root_dir / "cwd-normal"
        query_in_path_base_dir = self.root_dir / "cwd-plan-repro"
        normal_base_dir.mkdir(parents=True)
        query_in_path_base_dir.mkdir(parents=True)

        for base in (normal_base_dir, query_in_path_base_dir):
            setup_folder(
                base,
                dirs=["packages/coding-agent/examples/extensions/plan-mode"],
                files={
                    "packages/coding-agent/examples/extensions/plan-mode/README.md": "readme",
                    "packages/tui/docs/plan.md": "plan",
                },
            )

        query = "@plan"
        normal_provider = CombinedAutocompleteProvider([], str(normal_base_dir), require_fd())
        query_in_path_provider = CombinedAutocompleteProvider(
            [], str(query_in_path_base_dir), require_fd()
        )

        normal_result = await get_suggestions(normal_provider, [query], 0, len(query))
        query_in_path_result = await get_suggestions(query_in_path_provider, [query], 0, len(query))

        def normalize(result: AutocompleteSuggestions | None) -> list[str]:
            return sorted(
                f"{item.label} :: {item.description or ''}"
                for item in (result.items if result else [])
            )

        assert normalize(query_in_path_result) == normalize(normal_result)
        assert "plan-mode/ :: packages/coding-agent/examples/extensions/plan-mode" in normalize(
            normal_result
        )
        assert "plan.md :: packages/tui/docs/plan.md" in normalize(normal_result)

    async def test_continues_autocomplete_inside_quoted_at_paths(self) -> None:
        setup_folder(
            self.base_dir,
            files={"my folder/test.txt": "content", "my folder/other.txt": "content"},
        )

        line = '@"my folder/"'
        result = await get_suggestions(self.provider(), [line], 0, len(line) - 1)

        assert result is not None, "should return suggestions for a quoted folder path"
        assert '@"my folder/test.txt"' in values(result)
        assert '@"my folder/other.txt"' in values(result)

    async def test_applies_a_quoted_at_completion_without_duplicating_the_closing_quote(
        self,
    ) -> None:
        setup_folder(self.base_dir, files={"my folder/test.txt": "content"})

        provider = self.provider()
        line = '@"my folder/te"'
        cursor_col = len(line) - 1
        result = await get_suggestions(provider, [line], 0, cursor_col)

        assert result is not None, "should return suggestions for a quoted @ path"
        item = next(
            (entry for entry in result.items if entry.value == '@"my folder/test.txt"'), None
        )
        assert item is not None, "should find the test.txt suggestion"

        applied = provider.apply_completion([line], 0, cursor_col, item, result.prefix)
        assert applied.lines[0] == '@"my folder/test.txt" '


class TestDotSlashPathCompletion:
    async def test_preserves_the_dot_slash_prefix_when_completing_paths(
        self, tmp_path: Path
    ) -> None:
        setup_folder(tmp_path, files={"update.sh": "#!/bin/bash", "utils.ts": "export {};"})

        provider = CombinedAutocompleteProvider([], str(tmp_path))
        line = "./up"
        result = await get_suggestions(provider, [line], 0, len(line), True)

        assert result is not None, "should return suggestions for a ./ path"
        assert "./update.sh" in values(result)

    async def test_preserves_the_dot_slash_prefix_for_directory_completions(
        self, tmp_path: Path
    ) -> None:
        setup_folder(tmp_path, dirs=["src"], files={"src/index.ts": "export {};"})

        provider = CombinedAutocompleteProvider([], str(tmp_path))
        line = "./sr"
        result = await get_suggestions(provider, [line], 0, len(line), True)

        assert result is not None, "should return suggestions for a ./ directory path"
        assert "./src/" in values(result)


class TestQuotedPathCompletion:
    async def test_quotes_paths_with_spaces_for_direct_completion(self, tmp_path: Path) -> None:
        setup_folder(tmp_path, dirs=["my folder"], files={"my folder/test.txt": "content"})

        provider = CombinedAutocompleteProvider([], str(tmp_path))
        line = "my"
        result = await get_suggestions(provider, [line], 0, len(line), True)

        assert result is not None, "should return suggestions for path completion"
        assert '"my folder/"' in values(result)

    async def test_continues_completion_inside_quoted_paths(self, tmp_path: Path) -> None:
        setup_folder(
            tmp_path, files={"my folder/test.txt": "content", "my folder/other.txt": "content"}
        )

        provider = CombinedAutocompleteProvider([], str(tmp_path))
        line = '"my folder/"'
        result = await get_suggestions(provider, [line], 0, len(line) - 1, True)

        assert result is not None, "should return suggestions for a quoted folder path"
        assert '"my folder/test.txt"' in values(result)
        assert '"my folder/other.txt"' in values(result)

    async def test_applies_a_quoted_completion_without_duplicating_the_closing_quote(
        self, tmp_path: Path
    ) -> None:
        setup_folder(tmp_path, files={"my folder/test.txt": "content"})

        provider = CombinedAutocompleteProvider([], str(tmp_path))
        line = '"my folder/te"'
        cursor_col = len(line) - 1
        result = await get_suggestions(provider, [line], 0, cursor_col, True)

        assert result is not None, "should return suggestions for a quoted path"
        item = next(
            (entry for entry in result.items if entry.value == '"my folder/test.txt"'), None
        )
        assert item is not None, "should find the test.txt suggestion"

        applied = provider.apply_completion([line], 0, cursor_col, item, result.prefix)
        assert applied.lines[0] == '"my folder/test.txt"'


class TestNodePathHelpers:
    """`posixpath` disagrees with node exactly where this module leans on it."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("up", "."),
            ("./up", "."),
            ("a/b", "a"),
            ("/b", "/"),
            ("/", "/"),
            ("", "."),
            ("a/b/", "a"),
        ],
    )
    def test_dirname(self, path: str, expected: str) -> None:
        assert _dirname(path) == expected

    @pytest.mark.parametrize(
        ("path", "expected"),
        [("src/", "src"), ("a/b.txt", "b.txt"), ("b.txt", "b.txt"), ("/", ""), ("", "")],
    )
    def test_basename(self, path: str, expected: str) -> None:
        assert _basename(path) == expected

    @pytest.mark.parametrize(
        ("parts", "expected"),
        [
            (("/base", ""), "/base"),
            (("/base", "."), "/base"),
            (("/base", "sub/"), "/base/sub/"),
            (("/base", "../sibling"), "/sibling"),
            ((".", "name"), "name"),
            (("", ""), "."),
        ],
    )
    def test_join(self, parts: tuple[str, ...], expected: str) -> None:
        assert _join(*parts) == expected

    def test_locale_key_is_case_insensitive_first_then_lowercase_first(self) -> None:
        # Code-point order would put every capital ahead of every lowercase name.
        assert sorted(["Zed", "alpha", "Beta", "beta"], key=_locale_compare_key) == [
            "alpha",
            "beta",
            "Beta",
            "Zed",
        ]


class TestPrefixParsing:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("plain", "plain"),
            ("a/b", "a[\\\\/]b"),
            ("a/b/", "a[\\\\/]b[\\\\/]"),
            ("/", "/"),
            ("//", "//"),
            ("a.b/c+d", "a\\.b[\\\\/]c\\+d"),
        ],
    )
    def test_build_fd_path_query(self, query: str, expected: str) -> None:
        assert _build_fd_path_query(query) == expected

    @pytest.mark.parametrize(
        ("prefix", "expected"),
        [
            ('@"a', ("a", True, True)),
            ('"a', ("a", False, True)),
            ("@a", ("a", True, False)),
            ("a", ("a", False, False)),
        ],
    )
    def test_parse_path_prefix(self, prefix: str, expected: tuple[str, bool, bool]) -> None:
        assert tuple(_parse_path_prefix(prefix)) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('"open', '"open'),
            ('"closed" and "open', '"open'),
            ('"closed"', None),
            ('@"open', '@"open'),
            # Not a token start: the quote is mid-word, so it is not a path.
            ('x"open', None),
            ('x@"open', None),
        ],
    )
    def test_extract_quoted_prefix(self, text: str, expected: str | None) -> None:
        assert _extract_quoted_prefix(text) == expected


class TestNaturalTriggers:
    """`extractPathPrefix` without `force` — the branch the editor takes on every
    keystroke, where returning a prefix means popping the menu open."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("src/", "src/"),
            ("./s", "./s"),
            (".env", ".env"),
            ("~/s", "~/s"),
            ("cat ", ""),
            ("cat src/i", "src/i"),
            ("", None),
            ("word", None),
            ("two words", None),
            ("~notatilde", None),
        ],
    )
    def test_only_path_shaped_text_triggers(self, text: str, expected: str | None) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")

        assert provider._extract_path_prefix(text) == expected  # pyright: ignore[reportPrivateUsage]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("", ""), ("word", "word"), ("two words", "words")],
    )
    def test_forcing_always_returns_a_prefix(self, text: str, expected: str) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")

        assert provider._extract_path_prefix(text, True) == expected  # pyright: ignore[reportPrivateUsage]


class TestSlashCommands:
    def provider(self, commands: list[Any]) -> CombinedAutocompleteProvider:
        return CombinedAutocompleteProvider(commands, "/tmp")

    async def test_a_command_is_matched_by_name_not_by_description(self) -> None:
        commands = [SlashCommand(name="clear", description="delete everything")]
        result = await get_suggestions(self.provider(commands), ["/delete"], 0, 7)

        assert result is None

    async def test_an_argument_completer_is_awaited_only_when_it_returns_an_awaitable(
        self,
    ) -> None:
        calls: list[str] = []

        def completer(prefix: str) -> list[AutocompleteItem]:
            calls.append(prefix)
            return [AutocompleteItem(value="one", label="one")]

        commands = [SlashCommand(name="run", get_argument_completions=completer)]
        result = await get_suggestions(self.provider(commands), ["/run pre"], 0, 8)

        assert calls == ["pre"]
        assert values(result) == ["one"]

    async def test_an_argument_completer_sees_the_text_after_the_first_space_only(self) -> None:
        seen: list[str] = []

        async def completer(prefix: str) -> list[AutocompleteItem]:
            seen.append(prefix)
            return [AutocompleteItem(value="x", label="x")]

        commands = [SlashCommand(name="run", get_argument_completions=completer)]
        await get_suggestions(self.provider(commands), ["/run a b c"], 0, 10)

        assert seen == ["a b c"]

    async def test_forcing_skips_the_command_branch_entirely(self) -> None:
        calls: list[str] = []

        async def completer(prefix: str) -> list[AutocompleteItem]:
            calls.append(prefix)
            return [AutocompleteItem(value="x", label="x")]

        commands = [SlashCommand(name="run", get_argument_completions=completer)]
        await get_suggestions(self.provider(commands), ["/run a"], 0, 6, True)

        assert calls == []


class TestAtPrefixExtraction:
    """The `@` branch runs before the command branch and before file paths, so
    which text counts as an `@` token decides which branch answers at all."""

    async def test_an_at_inside_a_word_is_not_a_token(self, tmp_path: Path) -> None:
        provider = CombinedAutocompleteProvider(
            [], str(tmp_path), require_fd() if FD_PATH else None
        )
        line = "user@example"

        result = await get_suggestions(provider, [line], 0, len(line))

        # Falls through to the path branch, which finds nothing to offer.
        assert result is None

    @needs_fd
    async def test_an_at_after_a_delimiter_is_a_token(self, tmp_path: Path) -> None:
        setup_folder(tmp_path, files={"README.md": "readme"})
        provider = CombinedAutocompleteProvider([], str(tmp_path), require_fd())

        for line in ("@RE", "hey @RE", 'say="@RE'):
            result = await get_suggestions(provider, [line], 0, len(line))
            assert values(result) == ["@README.md"], line


class TestApplyCompletion:
    """`apply_completion` is pure, so the branches the filesystem cannot reach
    portably are exercised here rather than through the corpus."""

    def test_a_slash_prefix_away_from_the_line_start_is_a_path_not_a_command(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        line = "/edit /usr"
        item = AutocompleteItem(value="/usr/", label="usr/")

        applied = provider.apply_completion([line], 0, len(line), item, "/usr")

        # Not "/edit //usr ": a command completion only happens at the start of
        # the line, and this one is an argument.
        assert applied.lines[0] == "/edit /usr/"
        assert applied.cursor_col == len("/edit /usr/")

    def test_a_slash_prefix_at_the_line_start_is_a_command(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        item = AutocompleteItem(value="model", label="model")

        applied = provider.apply_completion(["/mo"], 0, 3, item, "/mo")

        assert applied.lines[0] == "/model "
        assert applied.cursor_col == len("/model ")

    def test_leading_whitespace_still_counts_as_the_line_start(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        item = AutocompleteItem(value="model", label="model")

        applied = provider.apply_completion(["  /mo"], 0, 5, item, "/mo")

        assert applied.lines[0] == "  /model "

    def test_a_directory_keeps_the_cursor_inside_the_closing_quote(self) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")
        item = AutocompleteItem(value='@"my folder/"', label="my folder/")

        applied = provider.apply_completion(['@"my'], 0, 4, item, '@"my')

        assert applied.lines[0] == '@"my folder/"'
        # Inside the quote, so typing continues the path rather than escaping it.
        assert applied.cursor_col == len('@"my folder/')


class TestFdOutputParsing:
    """`fd` is told to exclude `.git`, so the parser's own `.git` filter never
    fires for the real binary. A stub that prints the lines anyway is the only
    way to reach it — and it pins the rest of the parsing at the same time."""

    def stub_fd(self, tmp_path: Path, output: str) -> str:
        script = tmp_path / "stub-fd"
        script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\n")
        script.chmod(0o755)
        return str(script)

    async def test_git_paths_are_dropped_whatever_fd_prints(self, tmp_path: Path) -> None:
        fd_path = self.stub_fd(
            tmp_path,
            "\n".join(
                [
                    ".git",
                    ".git/",
                    ".git/config",
                    "nested/.git/config",
                    ".gitignore",
                    "src/",
                    "kept.txt",
                    "",
                ]
            ),
        )
        provider = CombinedAutocompleteProvider([], str(tmp_path), fd_path)

        line = "@"
        result = await get_suggestions(provider, [line], 0, len(line))

        assert values(result) == ["@.gitignore", "@src/", "@kept.txt"]
        # The trailing separator is what marks a directory, and it is not part
        # of the label's name.
        assert [item.label for item in (result.items if result else [])] == [
            ".gitignore",
            "src/",
            "kept.txt",
        ]

    async def test_a_failing_fd_yields_nothing(self, tmp_path: Path) -> None:
        script = tmp_path / "failing-fd"
        script.write_text("#!/bin/sh\necho kept.txt\nexit 1\n")
        script.chmod(0o755)
        provider = CombinedAutocompleteProvider([], str(tmp_path), str(script))

        line = "@"
        assert await get_suggestions(provider, [line], 0, len(line)) is None


class TestAbort:
    @needs_fd
    async def test_an_aborted_signal_yields_no_at_suggestions(self, tmp_path: Path) -> None:
        setup_folder(tmp_path, files={"README.md": "readme"})
        provider = CombinedAutocompleteProvider([], str(tmp_path), require_fd())

        line = "@RE"
        result = await provider.get_suggestions(
            [line], 0, len(line), signal=_Signal(aborted=True), force=False
        )

        assert result is None

    async def test_an_aborted_signal_never_spawns_fd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        setup_folder(tmp_path, files={"README.md": "readme"})
        spawned: list[Any] = []

        async def record(*args: Any, **kwargs: Any) -> Any:
            spawned.append(args)
            raise AssertionError("fd should not be spawned for an aborted request")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", record)
        provider = CombinedAutocompleteProvider([], str(tmp_path), "/usr/bin/fd")

        line = "@RE"
        result = await provider.get_suggestions(
            [line], 0, len(line), signal=_Signal(aborted=True), force=False
        )

        assert result is None
        assert spawned == []

    async def test_aborting_mid_flight_kills_fd_instead_of_waiting_for_it(
        self, tmp_path: Path
    ) -> None:
        """The TS hangs a `SIGKILL` off the signal's abort event; with a plain
        boolean there is no event, so the wait polls it. Either way a superseded
        request must not block on a slow walk."""
        slow_fd = tmp_path / "slow-fd"
        # `exec`, so the process that gets killed is the one holding the pipe:
        # both this port and the TS resolve when stdout closes, and an orphaned
        # grandchild would keep it open.
        slow_fd.write_text("#!/bin/sh\nexec sleep 30\n")
        slow_fd.chmod(0o755)

        class _AbortsWhileWaiting:
            def __init__(self) -> None:
                self.reads = 0

            @property
            def aborted(self) -> bool:
                self.reads += 1
                # False for the checks before the spawn, then True once the
                # wait loop starts polling.
                return self.reads > 2

        signal = _AbortsWhileWaiting()
        provider = CombinedAutocompleteProvider([], str(tmp_path), str(slow_fd))

        line = "@RE"
        started = time.monotonic()
        result = await provider.get_suggestions([line], 0, len(line), signal=signal, force=False)
        elapsed = time.monotonic() - started

        assert result is None
        assert elapsed < 5, f"waited {elapsed:.1f}s for a killed child"

    async def test_no_fd_binary_yields_no_at_suggestions(self, tmp_path: Path) -> None:
        setup_folder(tmp_path, files={"README.md": "readme"})
        provider = CombinedAutocompleteProvider([], str(tmp_path))

        line = "@RE"
        result = await get_suggestions(provider, [line], 0, len(line))

        assert result is None

    async def test_a_missing_fd_binary_yields_no_at_suggestions(self, tmp_path: Path) -> None:
        setup_folder(tmp_path, files={"README.md": "readme"})
        provider = CombinedAutocompleteProvider([], str(tmp_path), str(tmp_path / "no-such-binary"))

        line = "@RE"
        result = await get_suggestions(provider, [line], 0, len(line))

        assert result is None


class TestShouldTriggerFileCompletion:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("/model", False),
            ("  /model", False),
            ("/model gpt", True),
            ("src/", True),
            ("", True),
        ],
    )
    def test_slash_commands_suppress_forced_file_completion(
        self, line: str, expected: bool
    ) -> None:
        provider = CombinedAutocompleteProvider([], "/tmp")

        assert provider.should_trigger_file_completion([line], 0, len(line)) is expected
