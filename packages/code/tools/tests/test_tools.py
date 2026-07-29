# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false
"""Tests for the code tools (port of ``coding-agent/test/tools.test.ts``).

Execute-level parity: TUI rendering, image auto-resize, and the file-mutation
queue are out of scope for this leaf, so their scenarios are omitted.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
from cortex.code.tools import (  # noqa: PLC2701
    DEFAULT_MAX_LINES,
    BashOperations,
    ToolsOptions,
    _count_occurrences,
    _text_result,
    create_bash_tool,
    create_coding_tools,
    create_edit_tool,
    create_find_tool,
    create_grep_tool,
    create_local_bash_operations,
    create_ls_tool,
    create_read_only_tools,
    create_read_tool,
    create_write_tool,
    format_size,
    truncate_head,
    truncate_line,
    truncate_tail,
)
from cortex.code.tools import bash as bash_module
from cortex.code.tools.edit_diff import Edit, compute_edits_diff


def text_output(result: Any) -> str:
    """Join the text content blocks of a tool result."""
    return "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")


CWD = os.getcwd()
read_tool = create_read_tool(CWD)
write_tool = create_write_tool(CWD)
edit_tool = create_edit_tool(CWD)
bash_tool = create_bash_tool(CWD)
grep_tool = create_grep_tool(CWD)
find_tool = create_find_tool(CWD)
ls_tool = create_ls_tool(CWD)


async def run(tool: Any, call_id: str, params: dict[str, Any], on_update: Any = None) -> Any:
    return await tool.execute(call_id, params, None, on_update)


# ===========================================================================
# read tool
# ===========================================================================


class TestReadTool:
    async def test_reads_within_limits(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        content = "Hello, world!\nLine 2\nLine 3"
        test_file.write_text(content)

        result = await run(read_tool, "test-call-1", {"path": str(test_file)})
        assert text_output(result) == content
        assert "Use offset=" not in text_output(result)
        assert result.details is None

    async def test_nonexistent_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "nonexistent.txt"
        with pytest.raises((FileNotFoundError, RuntimeError)):
            await run(read_tool, "test-call-2", {"path": str(test_file)})

    async def test_truncate_line_limit(self, tmp_path: Path) -> None:
        test_file = tmp_path / "large.txt"
        lines = [f"Line {i + 1}" for i in range(2500)]
        test_file.write_text("\n".join(lines))

        result = await run(read_tool, "test-call-3", {"path": str(test_file)})
        output = text_output(result)
        assert "Line 1" in output
        assert f"Line {DEFAULT_MAX_LINES}" in output
        assert f"Line {DEFAULT_MAX_LINES + 1}" not in output
        assert (
            f"[Showing lines 1-{DEFAULT_MAX_LINES} of 2500. "
            f"Use offset={DEFAULT_MAX_LINES + 1} to continue.]"
        ) in output

    async def test_truncate_byte_limit(self, tmp_path: Path) -> None:
        test_file = tmp_path / "large-bytes.txt"
        lines = [f"Line {i + 1}: {'x' * 200}" for i in range(500)]
        test_file.write_text("\n".join(lines))

        result = await run(read_tool, "test-call-4", {"path": str(test_file)})
        output = text_output(result)
        assert "Line 1:" in output
        assert re.search(
            r"\[Showing lines 1-\d+ of 500 \(.* limit\)\. Use offset=\d+ to continue\.\]",
            output,
        )

    async def test_offset(self, tmp_path: Path) -> None:
        test_file = tmp_path / "offset-test.txt"
        test_file.write_text("\n".join(f"Line {i + 1}" for i in range(100)))

        result = await run(read_tool, "test-call-5", {"path": str(test_file), "offset": 51})
        output = text_output(result)
        assert "Line 50" not in output
        assert "Line 51" in output
        assert "Line 100" in output
        assert "Use offset=" not in output

    async def test_limit(self, tmp_path: Path) -> None:
        test_file = tmp_path / "limit-test.txt"
        test_file.write_text("\n".join(f"Line {i + 1}" for i in range(100)))

        result = await run(read_tool, "test-call-6", {"path": str(test_file), "limit": 10})
        output = text_output(result)
        assert "Line 1" in output
        assert "Line 10" in output
        assert "Line 11" not in output
        assert "[90 more lines in file. Use offset=11 to continue.]" in output

    async def test_offset_plus_limit(self, tmp_path: Path) -> None:
        test_file = tmp_path / "offset-limit-test.txt"
        test_file.write_text("\n".join(f"Line {i + 1}" for i in range(100)))

        result = await run(
            read_tool, "test-call-7", {"path": str(test_file), "offset": 41, "limit": 20}
        )
        output = text_output(result)
        assert "Line 40" not in output
        assert "Line 41" in output
        assert "Line 60" in output
        assert "Line 61" not in output
        assert "[40 more lines in file. Use offset=61 to continue.]" in output

    async def test_offset_beyond_length(self, tmp_path: Path) -> None:
        test_file = tmp_path / "short.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3")
        with pytest.raises(
            RuntimeError, match=r"Offset 100 is beyond end of file \(3 lines total\)"
        ):
            await run(read_tool, "test-call-8", {"path": str(test_file), "offset": 100})

    async def test_truncation_details(self, tmp_path: Path) -> None:
        test_file = tmp_path / "large-file.txt"
        test_file.write_text("\n".join(f"Line {i + 1}" for i in range(2500)))

        result = await run(read_tool, "test-call-9", {"path": str(test_file)})
        assert result.details is not None
        assert result.details.truncation is not None
        assert result.details.truncation.truncated is True
        assert result.details.truncation.truncated_by == "lines"
        assert result.details.truncation.total_lines == 2500
        assert result.details.truncation.output_lines == DEFAULT_MAX_LINES

    async def test_detect_image_from_magic(self, tmp_path: Path) -> None:
        import base64

        png_1x1 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
            "YGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=="
        )
        png_buffer = base64.b64decode(png_1x1)
        test_file = tmp_path / "image.txt"
        test_file.write_bytes(png_buffer)

        result = await run(read_tool, "test-call-img-1", {"path": str(test_file)})
        assert result.content[0].type == "text"
        assert "Read image file [image/png]" in text_output(result)
        image_block = next((c for c in result.content if c.type == "image"), None)
        assert image_block is not None
        assert image_block.mime_type == "image/png"
        assert isinstance(image_block.data, str)
        assert len(image_block.data) > 0

    async def test_image_extension_non_image_content(self, tmp_path: Path) -> None:
        test_file = tmp_path / "not-an-image.png"
        test_file.write_text("definitely not a png")

        result = await run(read_tool, "test-call-img-2", {"path": str(test_file)})
        output = text_output(result)
        assert "definitely not a png" in output
        assert not any(c.type == "image" for c in result.content)


# ===========================================================================
# write tool
# ===========================================================================


class TestWriteTool:
    async def test_writes_contents(self, tmp_path: Path) -> None:
        test_file = tmp_path / "write-test.txt"
        result = await run(
            write_tool, "test-call-3", {"path": str(test_file), "content": "Test content"}
        )
        assert "Successfully wrote" in text_output(result)
        assert str(test_file) in text_output(result)
        assert result.details is None
        assert test_file.read_text() == "Test content"

    async def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        test_file = tmp_path / "nested" / "dir" / "test.txt"
        result = await run(
            write_tool, "test-call-4", {"path": str(test_file), "content": "Nested content"}
        )
        assert "Successfully wrote" in text_output(result)
        assert test_file.exists()


# ===========================================================================
# edit tool
# ===========================================================================


class TestEditTool:
    async def test_replace_text(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-test.txt"
        test_file.write_text("Hello, world!")

        result = await run(
            edit_tool,
            "test-call-5",
            {"path": str(test_file), "edits": [{"oldText": "world", "newText": "testing"}]},
        )
        assert "Successfully replaced" in text_output(result)
        assert result.details is not None
        assert isinstance(result.details.diff, str)
        assert "testing" in result.details.diff

    async def test_fail_not_found(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-test.txt"
        test_file.write_text("Hello, world!")
        with pytest.raises(ValueError, match="Could not find the exact text"):
            await run(
                edit_tool,
                "test-call-6",
                {
                    "path": str(test_file),
                    "edits": [{"oldText": "nonexistent", "newText": "testing"}],
                },
            )

    async def test_enoent(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.txt"
        with pytest.raises(
            RuntimeError, match=re.escape(f"Could not edit file: {missing}. Error code: ENOENT.")
        ):
            await run(
                edit_tool,
                "test-call-6b",
                {"path": str(missing), "edits": [{"oldText": "hello", "newText": "world"}]},
            )

    async def test_fail_multiple(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-test.txt"
        test_file.write_text("foo foo foo")
        with pytest.raises(ValueError, match="Found 3 occurrences"):
            await run(
                edit_tool,
                "test-call-7",
                {"path": str(test_file), "edits": [{"oldText": "foo", "newText": "bar"}]},
            )

    async def test_replace_all(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-replace-all.txt"
        test_file.write_text("foo foo foo")
        result = await run(
            edit_tool,
            "test-call-7b",
            {
                "path": str(test_file),
                "edits": [{"oldText": "foo", "newText": "bar", "replaceAll": True}],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert test_file.read_text() == "bar bar bar"

    async def test_replace_all_absent_requires_unique(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-replace-all-off.txt"
        test_file.write_text("foo foo foo")
        with pytest.raises(ValueError, match="Found 3 occurrences"):
            await run(
                edit_tool,
                "test-call-7c",
                {
                    "path": str(test_file),
                    "edits": [{"oldText": "foo", "newText": "bar", "replaceAll": False}],
                },
            )

    async def test_multiple_disjoint(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-multi.txt"
        test_file.write_text("alpha\nbeta\ngamma\ndelta\n")
        result = await run(
            edit_tool,
            "test-call-8",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "alpha\n", "newText": "ALPHA\n"},
                    {"oldText": "gamma\n", "newText": "GAMMA\n"},
                ],
            },
        )
        assert "Successfully replaced 2 block(s)" in text_output(result)
        assert test_file.read_text() == "ALPHA\nbeta\nGAMMA\ndelta\n"
        assert "ALPHA" in result.details.diff
        assert "GAMMA" in result.details.diff

    async def test_collapse_large_gaps(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-multi-large-gap.txt"
        lines = [f"line {str(i + 1).zfill(3)}" for i in range(600)]
        test_file.write_text("\n".join(lines) + "\n")
        result = await run(
            edit_tool,
            "test-call-8b",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "line 100\n", "newText": "LINE 100\n"},
                    {"oldText": "line 300\n", "newText": "LINE 300\n"},
                    {"oldText": "line 500\n", "newText": "LINE 500\n"},
                ],
            },
        )
        diff = result.details.diff
        assert "LINE 100" in diff
        assert "LINE 300" in diff
        assert "LINE 500" in diff
        assert "..." in diff
        assert "line 250" not in diff
        assert len(diff.split("\n")) < 50

    async def test_match_against_original(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-multi-original.txt"
        test_file.write_text("foo\nbar\nbaz\n")
        await run(
            edit_tool,
            "test-call-9",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "foo\n", "newText": "foo bar\n"},
                    {"oldText": "bar\n", "newText": "BAR\n"},
                ],
            },
        )
        assert test_file.read_text() == "foo bar\nBAR\nbaz\n"

    async def test_fail_empty(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-empty-edits.txt"
        test_file.write_text("hello\nworld\n")
        with pytest.raises(RuntimeError, match="edits must contain at least one replacement"):
            await run(edit_tool, "test-call-11", {"path": str(test_file), "edits": []})

    async def test_fail_overlap(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-overlap.txt"
        test_file.write_text("one\ntwo\nthree\n")
        with pytest.raises(ValueError, match="overlap"):
            await run(
                edit_tool,
                "test-call-12",
                {
                    "path": str(test_file),
                    "edits": [
                        {"oldText": "one\ntwo\n", "newText": "ONE\nTWO\n"},
                        {"oldText": "two\nthree\n", "newText": "TWO\nTHREE\n"},
                    ],
                },
            )

    async def test_no_partial_apply(self, tmp_path: Path) -> None:
        test_file = tmp_path / "edit-no-partial.txt"
        original = "alpha\nbeta\ngamma\n"
        test_file.write_text(original)
        with pytest.raises(ValueError, match="Could not find"):
            await run(
                edit_tool,
                "test-call-13",
                {
                    "path": str(test_file),
                    "edits": [
                        {"oldText": "alpha\n", "newText": "ALPHA\n"},
                        {"oldText": "missing\n", "newText": "MISSING\n"},
                    ],
                },
            )
        assert test_file.read_text() == original

    async def test_generic_access_error(self, tmp_path: Path) -> None:
        from cortex.code.tools.edit import EditToolOptions

        class BrokenOps:
            def access(self, absolute_path: str) -> None:
                raise Exception("disk offline")

            def read_file(self, absolute_path: str) -> bytes:
                return b"hello\n"

            def write_file(self, absolute_path: str, content: str) -> None:
                pass

        tool = create_edit_tool(str(tmp_path), EditToolOptions(operations=BrokenOps()))
        with pytest.raises(
            RuntimeError, match=re.escape("Could not edit file: broken.txt. Error: disk offline.")
        ):
            await run(
                tool,
                "test-call-16",
                {"path": "broken.txt", "edits": [{"oldText": "hello", "newText": "world"}]},
            )

    async def test_diff_preview_enoent(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing-preview.txt"
        result = compute_edits_diff(
            str(missing), [Edit(old_text="hello", new_text="world")], str(tmp_path)
        )
        from cortex.code.tools.edit_diff import EditDiffError

        assert isinstance(result, EditDiffError)
        assert result.error == f"Could not edit file: {missing}. Error code: ENOENT."


# ===========================================================================
# bash tool
# ===========================================================================


class TestBashTool:
    async def test_simple_command(self) -> None:
        result = await run(bash_tool, "test-call-8", {"command": "echo 'test output'"})
        assert "test output" in text_output(result)
        assert result.details is None

    async def test_command_error(self) -> None:
        with pytest.raises(RuntimeError, match=r"(Command exited|code 1)"):
            await run(bash_tool, "test-call-9", {"command": "exit 1"})

    async def test_timeout(self) -> None:
        with pytest.raises(RuntimeError, match=r"(?i)timed out"):
            await run(bash_tool, "test-call-10", {"command": "sleep 5", "timeout": 1})

    async def test_full_output_on_truncated_timeout_and_abort(self, tmp_path: Path) -> None:
        for error, expected in [
            ("timeout:5", "Command timed out after 5 seconds"),
            ("aborted", "Command aborted"),
        ]:

            class Ops:
                def __init__(self, err: str) -> None:
                    self._err = err

                def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):  # type: ignore[no-untyped-def]
                    for i in range(1, 3001):
                        on_data(f"{i}\n".encode())
                    raise RuntimeError(self._err)

            bash = create_bash_tool(str(tmp_path), _bash_opts(Ops(error)))

            with pytest.raises(RuntimeError) as exc:
                await run(bash, f"test-call-{error}", {"command": "chatty-fail"})

            message = str(exc.value)
            assert expected in message
            assert re.search(r"\[Showing lines \d+-\d+ of \d+\. Full output: ", message)
            assert "Full output: None" not in message
            match = re.search(r"Full output: ([^\]\n]+)", message)
            assert match is not None
            full_path = match.group(1)
            assert os.path.exists(full_path)
            full_output = Path(full_path).read_text()
            assert "1\n2\n3" in full_output
            assert "2998\n2999\n3000" in full_output

    async def test_cwd_does_not_exist(self) -> None:
        bad = create_bash_tool("/this/directory/definitely/does/not/exist/12345")
        with pytest.raises(RuntimeError, match="Working directory does not exist"):
            await run(bad, "test-call-11", {"command": "echo test"})

    async def test_command_prefix(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(command_prefix="export TEST_VAR=hello"))
        result = await run(bash, "test-prefix-1", {"command": "echo $TEST_VAR"})
        assert text_output(result).strip() == "hello"

    async def test_prefix_and_command_output(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(command_prefix="echo prefix-output"))
        result = await run(bash, "test-prefix-2", {"command": "echo command-output"})
        assert text_output(result).strip() == "prefix-output\ncommand-output"

    async def test_without_prefix(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts())
        result = await run(bash, "test-prefix-3", {"command": "echo no-prefix"})
        assert text_output(result).strip() == "no-prefix"

    async def test_decode_split_utf8(self, tmp_path: Path) -> None:
        euro = "€\n".encode()

        class Ops:
            def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):  # type: ignore[no-untyped-def]
                on_data(euro[0:1])
                on_data(euro[1:])
                from cortex.code.tools.bash import BashExecResult

                return BashExecResult(exit_code=0)

        bash = create_bash_tool(str(tmp_path), _bash_opts(Ops()))
        result = await run(bash, "test-call-split-utf8", {"command": "split-utf8"})
        assert text_output(result).strip() == "€"

    async def test_local_ops_env(self, tmp_path: Path) -> None:
        ops = create_local_bash_operations()
        chunks: list[bytes] = []
        env = {**os.environ, "TEST_LOCAL_BASH_OPS": "from-local-ops"}
        result = ops.exec(
            "echo $TEST_LOCAL_BASH_OPS", str(tmp_path), on_data=lambda d: chunks.append(d), env=env
        )
        assert result.exit_code == 0
        assert b"".join(chunks).decode().strip() == "from-local-ops"

    async def test_persist_full_output_line_truncation(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path))
        result = await run(bash, "test-call-line-truncation", {"command": "seq 3000"})
        output = text_output(result)
        full_path = result.details.full_output_path if result.details else None
        assert result.details.truncation.truncated is True
        assert result.details.truncation.truncated_by == "lines"
        assert full_path is not None
        assert re.search(r"\[Showing lines \d+-\d+ of \d+\. Full output: ", output)
        assert "Full output: None" not in output
        assert os.path.exists(full_path)
        full_output = Path(full_path).read_text()
        assert "1\n2\n3" in full_output
        assert "2998\n2999\n3000" in full_output

    async def test_allowed_commands_match(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(allowed_commands=[r"^echo\b"]))
        result = await run(bash, "allow-1", {"command": "echo hello"})
        assert text_output(result).strip() == "hello"

    async def test_allowed_commands_no_match(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(allowed_commands=[r"^echo\b"]))
        with pytest.raises(
            RuntimeError, match="Command blocked: does not match any allowed command pattern"
        ):
            await run(bash, "allow-2", {"command": "ls"})

    async def test_denied_commands_match(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(denied_commands=[r"\brm\b"]))
        with pytest.raises(RuntimeError, match="Command blocked: matches denied pattern"):
            await run(bash, "deny-1", {"command": "rm -rf /"})

    async def test_denied_commands_no_match(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(denied_commands=[r"\brm\b"]))
        result = await run(bash, "deny-2", {"command": "echo safe"})
        assert text_output(result).strip() == "safe"

    async def test_denied_before_allowed(self, tmp_path: Path) -> None:
        bash = create_bash_tool(
            str(tmp_path), _bash_opts(allowed_commands=[r".*"], denied_commands=[r"^echo\b"])
        )
        with pytest.raises(RuntimeError, match="Command blocked: matches denied pattern"):
            await run(bash, "deny-before-allow", {"command": "echo hi"})

    async def test_invalid_regex_denied_skipped(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts(denied_commands=["[invalid", r"\brm\b"]))
        with pytest.raises(RuntimeError, match="Command blocked: matches denied pattern"):
            await run(bash, "invalid-regex-deny", {"command": "rm foo"})

    async def test_invalid_regex_allowed_skipped(self, tmp_path: Path) -> None:
        bash = create_bash_tool(
            str(tmp_path), _bash_opts(allowed_commands=["[invalid", r"^echo\b"])
        )
        result = await run(bash, "invalid-regex-allow", {"command": "echo valid"})
        assert text_output(result).strip() == "valid"

    async def test_no_filter(self, tmp_path: Path) -> None:
        bash = create_bash_tool(str(tmp_path), _bash_opts())
        result = await run(bash, "no-filter", {"command": "echo unrestricted"})
        assert text_output(result).strip() == "unrestricted"


def _bash_opts(
    operations: BashOperations | None = None,
    *,
    command_prefix: str | None = None,
    allowed_commands: list[str] | None = None,
    denied_commands: list[str] | None = None,
) -> Any:
    from cortex.code.tools.bash import BashToolOptions

    return BashToolOptions(
        operations=operations,
        command_prefix=command_prefix,
        allowed_commands=allowed_commands,
        denied_commands=denied_commands,
    )


# ===========================================================================
# grep tool
# ===========================================================================


class TestGrepTool:
    async def test_filename_single_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "example.txt"
        test_file.write_text("first line\nmatch line\nlast line")
        result = await run(grep_tool, "test-call-11", {"pattern": "match", "path": str(test_file)})
        output = text_output(result)
        assert "example.txt" in output
        assert "2: match line" in output

    async def test_limit_and_context(self, tmp_path: Path) -> None:
        test_file = tmp_path / "context.txt"
        test_file.write_text(
            "\n".join(["before", "match one", "after", "middle", "match two", "after two"])
        )
        result = await run(
            grep_tool,
            "test-call-12",
            {"pattern": "match", "path": str(test_file), "limit": 1, "context": 1},
        )
        output = text_output(result)
        assert "context.txt" in output
        assert "1- before" in output
        assert "2: match one" in output
        assert "3- after" in output
        assert "[1 match limit reached. Use limit=2 for more, or refine pattern]" in output
        assert "match two" not in output

    async def test_flag_like_pattern(self, tmp_path: Path) -> None:
        payload = tmp_path / "payload.sh"
        marker = tmp_path / "grep-injection-marker"
        payload.write_text(f'#!/bin/sh\necho executed > {marker}\ncat "$1"\n')
        payload.chmod(0o755)
        (tmp_path / "target.txt").write_text("target\n")

        result = await run(
            grep_tool,
            "test-call-grep-injection",
            {"pattern": f"--pre={payload}", "path": str(tmp_path)},
        )
        assert "No matches found" in text_output(result)
        assert not marker.exists()

    async def test_invalid_regex_hint(self, tmp_path: Path) -> None:
        test_file = tmp_path / "regex.txt"
        test_file.write_text("a { options b\n")
        with pytest.raises(RuntimeError, match="literal: true"):
            await run(
                grep_tool,
                "test-call-grep-bad-regex",
                {"pattern": "[invalid", "path": str(test_file)},
            )

    async def test_literal_invalid_regex(self, tmp_path: Path) -> None:
        test_file = tmp_path / "regex-literal.txt"
        test_file.write_text("prefix { options suffix\n")
        result = await run(
            grep_tool,
            "test-call-grep-literal",
            {"pattern": "{ options", "path": str(test_file), "literal": True},
        )
        assert "1: prefix { options suffix" in text_output(result)

    async def test_empty_pattern(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="pattern must not be empty"):
            await run(grep_tool, "test-call-grep-empty", {"pattern": "", "path": str(tmp_path)})

    async def test_slash_glob(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "nested").mkdir(parents=True)
        (tmp_path / "src" / "nested" / "hit.ts").write_text("needle here\n")
        (tmp_path / "miss.ts").write_text("needle here\n")
        result = await run(
            grep_tool,
            "test-call-grep-slash-glob",
            {"pattern": "needle", "path": str(tmp_path), "glob": "src/**/*.ts"},
        )
        output = text_output(result)
        assert "src/nested/hit.ts" in output
        assert "miss.ts" not in output

    async def test_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("ignored-grep.txt\n")
        (tmp_path / "ignored-grep.txt").write_text("grep-ignore-needle\n")
        (tmp_path / "kept-grep.txt").write_text("grep-ignore-needle\n")
        result = await run(
            grep_tool,
            "test-call-grep-gitignore",
            {"pattern": "grep-ignore-needle", "path": str(tmp_path)},
        )
        output = text_output(result)
        assert "kept-grep.txt" in output
        assert "ignored-grep.txt" not in output

    async def test_never_searches_git(self, tmp_path: Path) -> None:
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        (tmp_path / ".git" / "hooks" / "sample.txt").write_text("git-dir-needle\n")
        (tmp_path / "outside.txt").write_text("git-dir-needle\n")
        result = await run(
            grep_tool,
            "test-call-grep-git-dir",
            {"pattern": "git-dir-needle", "path": str(tmp_path)},
        )
        output = text_output(result)
        assert "outside.txt" in output
        assert ".git/hooks/sample.txt" not in output


# ===========================================================================
# find tool
# ===========================================================================


class TestFindTool:
    async def test_hidden_files_not_gitignored(self, tmp_path: Path) -> None:
        hidden_dir = tmp_path / ".secret"
        hidden_dir.mkdir()
        (hidden_dir / "hidden.txt").write_text("hidden")
        (tmp_path / "visible.txt").write_text("visible")

        result = await run(
            find_tool, "test-call-13", {"pattern": "**/*.txt", "path": str(tmp_path)}
        )
        output_lines = [line.strip() for line in text_output(result).split("\n") if line.strip()]
        assert "visible.txt" in output_lines
        assert ".secret/hidden.txt" in output_lines

    async def test_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("ignored.txt\n")
        (tmp_path / "ignored.txt").write_text("ignored")
        (tmp_path / "kept.txt").write_text("kept")
        result = await run(
            find_tool, "test-call-14", {"pattern": "**/*.txt", "path": str(tmp_path)}
        )
        output = text_output(result)
        assert "kept.txt" in output
        assert "ignored.txt" not in output

    async def test_glob_parse_error(self, tmp_path: Path) -> None:
        with pytest.raises(
            (ValueError, RuntimeError),
            match=r"(?i)error parsing glob|fd exited with code 1|fd error",
        ):
            await run(find_tool, "test-call-15", {"pattern": "[", "path": str(tmp_path)})

    async def test_flag_like_pattern(self, tmp_path: Path) -> None:
        result = await run(
            find_tool, "test-call-find-flag-pattern", {"pattern": "--help", "path": str(tmp_path)}
        )
        assert "No files found matching pattern" in text_output(result)

    async def test_clamp_nonpositive_limit(self, tmp_path: Path) -> None:
        (tmp_path / "one.txt").write_text("1")
        (tmp_path / "two.txt").write_text("2")
        result = await run(
            find_tool,
            "test-call-find-limit-zero",
            {"pattern": "*.txt", "path": str(tmp_path), "limit": 0},
        )
        output = text_output(result)
        assert "one.txt" in output
        assert "[1 result limit reached. Use limit=2 for more, or refine pattern]" in output

    async def test_no_limit_when_exact(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = await run(
            find_tool,
            "test-call-find-exact-limit",
            {"pattern": "*.txt", "path": str(tmp_path), "limit": 2},
        )
        output = text_output(result)
        assert "a.txt" in output
        assert "b.txt" in output
        assert "limit reached" not in output


# ===========================================================================
# ls tool
# ===========================================================================


class TestLsTool:
    async def test_dotfiles_and_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden-file").write_text("secret")
        (tmp_path / ".hidden-dir").mkdir()
        result = await run(ls_tool, "test-call-15", {"path": str(tmp_path)})
        output = text_output(result)
        assert ".hidden-file" in output
        assert ".hidden-dir/" in output

    async def test_ignore_globs(self, tmp_path: Path) -> None:
        (tmp_path / "keep.ts").write_text("")
        (tmp_path / "debug.log").write_text("")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / ".git").mkdir()
        result = await run(
            ls_tool,
            "test-call-15b",
            {"path": str(tmp_path), "ignore": ["node_modules", "*.log", ".git"]},
        )
        output = text_output(result)
        assert "keep.ts" in output
        assert "debug.log" not in output
        assert "node_modules" not in output
        assert ".git" not in output


# ===========================================================================
# edit tool fuzzy matching
# ===========================================================================


class TestEditFuzzy:
    async def test_trailing_whitespace(self, tmp_path: Path) -> None:
        test_file = tmp_path / "trailing-ws.txt"
        test_file.write_text("line one   \nline two  \nline three\n")
        result = await run(
            edit_tool,
            "test-fuzzy-1",
            {
                "path": str(test_file),
                "edits": [{"oldText": "line one\nline two\n", "newText": "replaced\n"}],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert test_file.read_text() == "replaced\nline three\n"

    async def test_fullwidth_punctuation(self, tmp_path: Path) -> None:
        test_file = tmp_path / "chinese-punctuation.txt"
        test_file.write_text("你好，世界\n你好（世界）\n")
        result = await run(
            edit_tool,
            "test-fuzzy-chinese",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "你好,世界\n你好(世界)\n", "newText": "你好，pi\n你好(pi)\n"}
                ],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert test_file.read_text() == "你好，pi\n你好(pi)\n"

    async def test_compatibility_unicode(self, tmp_path: Path) -> None:
        test_file = tmp_path / "unicode-compatibility.txt"
        test_file.write_text("ＡＢＣ１２３\ncafe\u0301\n")
        result = await run(
            edit_tool,
            "test-fuzzy-unicode",
            {
                "path": str(test_file),
                "edits": [{"oldText": "ABC123\ncafé\n", "newText": "XYZ789\ncoffee\n"}],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert test_file.read_text() == "XYZ789\ncoffee\n"

    async def test_smart_single_quotes(self, tmp_path: Path) -> None:
        test_file = tmp_path / "smart-quotes.txt"
        test_file.write_text("console.log(\u2018hello\u2019);\n")
        result = await run(
            edit_tool,
            "test-fuzzy-2",
            {
                "path": str(test_file),
                "edits": [{"oldText": "console.log('hello');", "newText": "console.log('world');"}],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert "world" in test_file.read_text()

    async def test_smart_double_quotes(self, tmp_path: Path) -> None:
        test_file = tmp_path / "smart-double-quotes.txt"
        test_file.write_text("const msg = \u201cHello World\u201d;\n")
        result = await run(
            edit_tool,
            "test-fuzzy-3",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": 'const msg = "Hello World";', "newText": 'const msg = "Goodbye";'}
                ],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert "Goodbye" in test_file.read_text()

    async def test_unicode_dashes(self, tmp_path: Path) -> None:
        test_file = tmp_path / "unicode-dashes.txt"
        test_file.write_text("range: 1\u20135\nbreak\u2014here\n")
        result = await run(
            edit_tool,
            "test-fuzzy-4",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "range: 1-5\nbreak-here", "newText": "range: 10-50\nbreak--here"}
                ],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert "10-50" in test_file.read_text()

    async def test_nbsp(self, tmp_path: Path) -> None:
        test_file = tmp_path / "nbsp.txt"
        test_file.write_text("hello\u00a0world\n")
        result = await run(
            edit_tool,
            "test-fuzzy-5",
            {
                "path": str(test_file),
                "edits": [{"oldText": "hello world", "newText": "hello universe"}],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert "universe" in test_file.read_text()

    async def test_prefer_exact(self, tmp_path: Path) -> None:
        test_file = tmp_path / "exact-preferred.txt"
        test_file.write_text("const x = 'exact';\nconst y = 'other';\n")
        result = await run(
            edit_tool,
            "test-fuzzy-6",
            {
                "path": str(test_file),
                "edits": [{"oldText": "const x = 'exact';", "newText": "const x = 'changed';"}],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert test_file.read_text() == "const x = 'changed';\nconst y = 'other';\n"

    async def test_fuzzy_still_fails(self, tmp_path: Path) -> None:
        test_file = tmp_path / "no-match.txt"
        test_file.write_text("completely different content\n")
        with pytest.raises(ValueError, match="Could not find the exact text"):
            await run(
                edit_tool,
                "test-fuzzy-7",
                {
                    "path": str(test_file),
                    "edits": [{"oldText": "this does not exist", "newText": "replacement"}],
                },
            )

    async def test_fuzzy_duplicates(self, tmp_path: Path) -> None:
        test_file = tmp_path / "fuzzy-dups.txt"
        test_file.write_text("hello world   \nhello world\n")
        with pytest.raises(ValueError, match="Found 2 occurrences"):
            await run(
                edit_tool,
                "test-fuzzy-8",
                {
                    "path": str(test_file),
                    "edits": [{"oldText": "hello world", "newText": "replaced"}],
                },
            )

    async def test_fuzzy_multi_edit(self, tmp_path: Path) -> None:
        test_file = tmp_path / "fuzzy-multi.txt"
        test_file.write_text("console.log(\u2018hello\u2019);\nhello\u00a0world\n")
        await run(
            edit_tool,
            "test-fuzzy-9",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "console.log('hello');\n", "newText": "console.log('world');\n"},
                    {"oldText": "hello world\n", "newText": "hello universe\n"},
                ],
            },
        )
        assert test_file.read_text() == "console.log('world');\nhello universe\n"

    async def test_indent_tolerant(self, tmp_path: Path) -> None:
        test_file = tmp_path / "indent-tabs.txt"
        test_file.write_text("function f() {\n\tif (x) {\n\t\treturn 1;\n\t}\n}\n")
        result = await run(
            edit_tool,
            "test-indent-1",
            {
                "path": str(test_file),
                "edits": [
                    {
                        "oldText": "  if (x) {\n    return 1;\n  }",
                        "newText": "  if (x) {\n    return 2;\n  }",
                    }
                ],
            },
        )
        assert "Successfully replaced" in text_output(result)
        assert "return 2;" in test_file.read_text()

    async def test_indent_duplicates(self, tmp_path: Path) -> None:
        test_file = tmp_path / "indent-dups.txt"
        test_file.write_text("\tfoo();\n\tbar();\n  foo();\n  bar();\n")
        with pytest.raises(ValueError, match="Found 2 occurrences"):
            await run(
                edit_tool,
                "test-indent-2",
                {
                    "path": str(test_file),
                    "edits": [{"oldText": "foo();\nbar();", "newText": "baz();"}],
                },
            )


# ===========================================================================
# edit tool CRLF handling
# ===========================================================================


class TestEditCRLF:
    async def test_lf_oldtext_against_crlf(self, tmp_path: Path) -> None:
        test_file = tmp_path / "crlf-test.txt"
        test_file.write_bytes(b"line one\r\nline two\r\nline three\r\n")
        result = await run(
            edit_tool,
            "test-crlf-1",
            {
                "path": str(test_file),
                "edits": [{"oldText": "line two\n", "newText": "replaced line\n"}],
            },
        )
        assert "Successfully replaced" in text_output(result)

    async def test_preserve_crlf(self, tmp_path: Path) -> None:
        test_file = tmp_path / "crlf-preserve.txt"
        test_file.write_bytes(b"first\r\nsecond\r\nthird\r\n")
        await run(
            edit_tool,
            "test-crlf-2",
            {"path": str(test_file), "edits": [{"oldText": "second\n", "newText": "REPLACED\n"}]},
        )
        assert test_file.read_bytes() == b"first\r\nREPLACED\r\nthird\r\n"

    async def test_preserve_lf(self, tmp_path: Path) -> None:
        test_file = tmp_path / "lf-preserve.txt"
        test_file.write_bytes(b"first\nsecond\nthird\n")
        await run(
            edit_tool,
            "test-lf-1",
            {"path": str(test_file), "edits": [{"oldText": "second\n", "newText": "REPLACED\n"}]},
        )
        assert test_file.read_bytes() == b"first\nREPLACED\nthird\n"

    async def test_duplicates_across_crlf_lf(self, tmp_path: Path) -> None:
        test_file = tmp_path / "mixed-endings.txt"
        test_file.write_bytes(b"hello\r\nworld\r\n---\r\nhello\nworld\n")
        with pytest.raises(ValueError, match="Found 2 occurrences"):
            await run(
                edit_tool,
                "test-crlf-dup",
                {
                    "path": str(test_file),
                    "edits": [{"oldText": "hello\nworld\n", "newText": "replaced\n"}],
                },
            )

    async def test_preserve_bom(self, tmp_path: Path) -> None:
        test_file = tmp_path / "bom-test.txt"
        test_file.write_bytes("\ufefffirst\r\nsecond\r\nthird\r\n".encode())
        await run(
            edit_tool,
            "test-bom",
            {"path": str(test_file), "edits": [{"oldText": "second\n", "newText": "REPLACED\n"}]},
        )
        assert test_file.read_bytes() == "\ufefffirst\r\nREPLACED\r\nthird\r\n".encode()

    async def test_preserve_crlf_bom_multi(self, tmp_path: Path) -> None:
        test_file = tmp_path / "bom-crlf-multi.txt"
        test_file.write_bytes("\ufefffirst\r\nsecond\r\nthird\r\nfourth\r\n".encode())
        await run(
            edit_tool,
            "test-crlf-multi",
            {
                "path": str(test_file),
                "edits": [
                    {"oldText": "second\n", "newText": "SECOND\n"},
                    {"oldText": "fourth\n", "newText": "FOURTH\n"},
                ],
            },
        )
        assert test_file.read_bytes() == "\ufefffirst\r\nSECOND\r\nthird\r\nFOURTH\r\n".encode()


# ===========================================================================
# truncation utilities + bundles + helpers
# ===========================================================================


class TestTruncationUtilities:
    def test_format_size(self) -> None:
        assert format_size(100) == "100B"
        assert format_size(1024) == "1.0KB"
        assert format_size(1536) == "1.5KB"
        assert format_size(1024 * 1024) == "1.0MB"

    def test_truncate_line(self) -> None:
        assert truncate_line("Hello", 10) == ("Hello", False)
        text, was = truncate_line("Hello, World!", 5)
        assert text == "Hello... [truncated]"
        assert was is True

    def test_truncate_head_short(self) -> None:
        text = "Line 1\nLine 2\nLine 3"
        assert truncate_head(text).content == text

    def test_truncate_tail_short(self) -> None:
        text = "Line 1\nLine 2\nLine 3"
        assert truncate_tail(text).content == text

    def test_count_occurrences(self) -> None:
        assert _count_occurrences("hello world", "o") == 2
        assert _count_occurrences("hello world", "xyz") == 0
        assert _count_occurrences("hello", "") == 0

    def test_text_result(self) -> None:
        result = _text_result("Hello, World!")
        assert result.content[0].text == "Hello, World!"


class TestToolBundles:
    def test_create_coding_tools(self, tmp_path: Path) -> None:
        tools = create_coding_tools(None, ToolsOptions(cwd=str(tmp_path)))
        assert len(tools) == 7
        names = [t.name for t in tools]
        for name in ("bash", "read", "edit", "write", "grep", "find", "ls"):
            assert name in names

    def test_create_read_only_tools(self, tmp_path: Path) -> None:
        tools = create_read_only_tools(None, ToolsOptions(cwd=str(tmp_path)))
        assert len(tools) == 4
        names = [t.name for t in tools]
        for name in ("read", "grep", "find", "ls"):
            assert name in names
        for name in ("bash", "edit", "write"):
            assert name not in names


class _FakeCompletedProcess:
    """Enough of `subprocess.CompletedProcess` for the shell lookup."""

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


class _ShellProbe:
    """Fakes `os.path.exists` and the `where`/`which` lookup together.

    The shell resolution is a chain of existence checks and one subprocess, and
    both have to be faked to drive the Windows branch from a POSIX box (and the
    POSIX branch from a machine that does have `/bin/bash`).
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        platform: str,
        existing: tuple[str, ...] = (),
        lookup_stdout: str = "",
        lookup_returncode: int = 1,
    ) -> None:
        self.existing = set(existing)
        self.lookup_stdout = lookup_stdout
        self.lookup_returncode = lookup_returncode
        self.lookup_calls: list[list[str]] = []
        monkeypatch.setattr(bash_module.sys, "platform", platform)
        monkeypatch.setattr(bash_module.os.path, "exists", self._exists)
        monkeypatch.setattr(bash_module.subprocess, "run", self._run)

    def _exists(self, path: str) -> bool:
        return path in self.existing

    def _run(self, argv: list[str], **_kwargs: Any) -> _FakeCompletedProcess:
        self.lookup_calls.append(argv)
        return _FakeCompletedProcess(self.lookup_returncode, self.lookup_stdout)


class TestShellConfig:
    """`_get_shell_config`, the port of ``getShellConfig``.

    On Windows the old resolution (`$SHELL`, defaulting to `/bin/bash`) meant
    every bash call died with `spawn /bin/bash ENOENT`; on POSIX it ran the
    user's login shell, so a fish or zsh user got neither bash nor a warning.
    """

    def test_custom_shell_path_is_used_when_it_exists(self, monkeypatch: pytest.MonkeyPatch):
        _ShellProbe(monkeypatch, platform="linux", existing=("/opt/my/bash",))
        assert bash_module._get_shell_config("/opt/my/bash") == ("/opt/my/bash", ["-c"])

    def test_a_missing_custom_shell_path_raises(self, monkeypatch: pytest.MonkeyPatch):
        _ShellProbe(monkeypatch, platform="linux")
        with pytest.raises(RuntimeError, match="Custom shell path not found: /nope/bash"):
            bash_module._get_shell_config("/nope/bash")

    def test_posix_prefers_bin_bash_over_the_login_shell(self, monkeypatch: pytest.MonkeyPatch):
        """The TS never reads `$SHELL`, and the model writes bash, not fish."""
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        _ShellProbe(monkeypatch, platform="linux", existing=("/bin/bash",))
        assert bash_module._get_shell_config() == ("/bin/bash", ["-c"])

    def test_posix_falls_back_to_bash_on_path(self, monkeypatch: pytest.MonkeyPatch):
        probe = _ShellProbe(
            monkeypatch,
            platform="linux",
            lookup_stdout="/usr/local/bin/bash\n",
            lookup_returncode=0,
        )
        assert bash_module._get_shell_config() == ("/usr/local/bin/bash", ["-c"])
        assert probe.lookup_calls == [["which", "bash"]]

    def test_posix_falls_back_to_sh(self, monkeypatch: pytest.MonkeyPatch):
        _ShellProbe(monkeypatch, platform="linux")
        assert bash_module._get_shell_config() == ("sh", ["-c"])

    def test_windows_finds_git_bash(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        _ShellProbe(
            monkeypatch,
            platform="win32",
            existing=("C:\\Program Files\\Git\\bin\\bash.exe",),
        )
        shell, args = bash_module._get_shell_config()
        assert shell == "C:\\Program Files\\Git\\bin\\bash.exe"
        assert args == ["-c"], "Git Bash takes -c; only cmd.exe would need /c"

    def test_windows_falls_back_to_the_32_bit_install(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
        monkeypatch.setenv("ProgramFiles(x86)", "C:\\Program Files (x86)")
        _ShellProbe(
            monkeypatch,
            platform="win32",
            existing=("C:\\Program Files (x86)\\Git\\bin\\bash.exe",),
        )
        assert bash_module._get_shell_config()[0] == "C:\\Program Files (x86)\\Git\\bin\\bash.exe"

    def test_windows_falls_back_to_bash_on_path(self, monkeypatch: pytest.MonkeyPatch):
        """Cygwin/MSYS2. `where` prints CRLF, and may print more than one line."""
        monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
        probe = _ShellProbe(
            monkeypatch,
            platform="win32",
            existing=("C:\\msys64\\usr\\bin\\bash.exe",),
            lookup_stdout="C:\\msys64\\usr\\bin\\bash.exe\r\nC:\\other\\bash.exe\r\n",
            lookup_returncode=0,
        )
        assert bash_module._get_shell_config()[0] == "C:\\msys64\\usr\\bin\\bash.exe"
        assert probe.lookup_calls == [["where", "bash.exe"]]

    def test_windows_rejects_a_path_where_only_imagined(self, monkeypatch: pytest.MonkeyPatch):
        """`where` reports paths that are not there — the TS verifies them."""
        monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
        _ShellProbe(
            monkeypatch,
            platform="win32",
            lookup_stdout="C:\\gone\\bash.exe\r\n",
            lookup_returncode=0,
        )
        with pytest.raises(RuntimeError, match="No bash shell found"):
            bash_module._get_shell_config()

    def test_windows_says_what_it_searched(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ProgramFiles", "C:\\Program Files")
        monkeypatch.setenv("ProgramFiles(x86)", "C:\\Program Files (x86)")
        _ShellProbe(monkeypatch, platform="win32")
        with pytest.raises(RuntimeError) as error:
            bash_module._get_shell_config()
        message = str(error.value)
        assert "git-scm.com/download/win" in message
        assert "C:\\Program Files\\Git\\bin\\bash.exe" in message
        assert "C:\\Program Files (x86)\\Git\\bin\\bash.exe" in message

    def test_a_lookup_that_cannot_run_is_not_fatal(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bash_module.sys, "platform", "linux")

        def _nothing_exists(_path: str) -> bool:
            return False

        monkeypatch.setattr(bash_module.os.path, "exists", _nothing_exists)

        def _explode(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("no which on this system")

        monkeypatch.setattr(bash_module.subprocess, "run", _explode)
        assert bash_module._get_shell_config() == ("sh", ["-c"])


class TestShellEnv:
    """`_get_shell_env`, the port of ``getShellEnv``: `fd` and `rg` on PATH."""

    def test_the_bin_dir_is_prepended_to_path(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bash_module, "get_bin_dir", lambda: "/agent/bin")
        monkeypatch.setattr(bash_module.os, "environ", {"PATH": f"/usr/bin{os.pathsep}/bin"})
        env = bash_module._get_shell_env()
        assert env["PATH"] == os.pathsep.join(["/agent/bin", "/usr/bin", "/bin"])

    def test_windows_reuses_the_path_key_it_found(self, monkeypatch: pytest.MonkeyPatch):
        """Windows spells it `Path`; adding a second `PATH` would do nothing."""
        monkeypatch.setattr(bash_module, "get_bin_dir", lambda: "C:\\agent\\bin")
        monkeypatch.setattr(bash_module.os, "environ", {"Path": "C:\\Windows"})
        env = bash_module._get_shell_env()
        assert env["Path"] == os.pathsep.join(["C:\\agent\\bin", "C:\\Windows"])
        assert "PATH" not in env

    def test_an_already_present_bin_dir_is_not_added_twice(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bash_module, "get_bin_dir", lambda: "/agent/bin")
        monkeypatch.setattr(
            bash_module.os, "environ", {"PATH": os.pathsep.join(["/usr/bin", "/agent/bin"])}
        )
        env = bash_module._get_shell_env()
        assert env["PATH"] == os.pathsep.join(["/usr/bin", "/agent/bin"])

    def test_an_empty_path_becomes_the_bin_dir(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bash_module, "get_bin_dir", lambda: "/agent/bin")
        monkeypatch.setattr(bash_module.os, "environ", {})
        assert bash_module._get_shell_env()["PATH"] == "/agent/bin"


class TestKillProcessTree:
    """`kill_process_tree`, the port of ``killProcessTree``.

    The old code called `proc.kill()` on Windows, which kills the shell and
    leaves everything the shell started running — so a timed-out or aborted
    command was not actually stopped.
    """

    def test_windows_uses_taskkill_on_the_tree(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        monkeypatch.setattr(bash_module.sys, "platform", "win32")

        def _record(argv: list[str], **_kwargs: Any) -> None:
            calls.append(argv)

        monkeypatch.setattr(bash_module.subprocess, "Popen", _record)
        bash_module.kill_process_tree(4321)
        assert calls == [["taskkill", "/F", "/T", "/PID", "4321"]]

    def test_windows_survives_a_missing_taskkill(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bash_module.sys, "platform", "win32")

        def _explode(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("taskkill not found")

        monkeypatch.setattr(bash_module.subprocess, "Popen", _explode)
        bash_module.kill_process_tree(4321)  # must not raise

    def test_posix_kills_the_process_group(self, monkeypatch: pytest.MonkeyPatch):
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(bash_module.sys, "platform", "linux")

        def _record_group(pid: int, sig: int) -> None:
            killed.append((pid, sig))

        monkeypatch.setattr(bash_module.os, "killpg", _record_group)
        bash_module.kill_process_tree(4321)
        assert killed == [(4321, bash_module.signal_module.SIGKILL)]

    def test_posix_falls_back_to_the_bare_pid(self, monkeypatch: pytest.MonkeyPatch):
        """A child that left the group is still a child worth killing."""
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(bash_module.sys, "platform", "linux")

        def _no_group(_pid: int, _sig: int) -> None:
            raise ProcessLookupError

        def _record_pid(pid: int, sig: int) -> None:
            killed.append((pid, sig))

        monkeypatch.setattr(bash_module.os, "killpg", _no_group)
        monkeypatch.setattr(bash_module.os, "kill", _record_pid)
        bash_module.kill_process_tree(4321)
        assert killed == [(4321, bash_module.signal_module.SIGKILL)]


class TestInheritedStdioDoesNotHang:
    """Port of ``bash-close-hang-windows.test.ts``, which the TS skips off Windows.

    A descendant that inherits the shell's stdout keeps the pipe open after the
    shell has exited. Waiting for the readers therefore waits for the
    *descendant* — a daemonised one on Windows means forever, and this is why
    ``waitForChildProcess`` exists. Driven here through a background job, which
    reproduces the same shape on POSIX in three seconds rather than never.
    """

    async def test_exec_returns_when_the_shell_exits(self, tmp_path: Path) -> None:
        import time

        ops = create_local_bash_operations()
        chunks: list[bytes] = []
        started = time.monotonic()
        result = ops.exec(
            "echo started; sleep 5 &", str(tmp_path), on_data=lambda data: chunks.append(data)
        )
        elapsed = time.monotonic() - started
        assert result.exit_code == 0
        assert elapsed < 2, f"waited for the background job rather than the shell: {elapsed:.2f}s"
        assert b"started" in b"".join(chunks), "output was dropped instead of merely delayed"

    async def test_the_tool_returns_too(self, tmp_path: Path) -> None:
        import time

        bash = create_bash_tool(str(tmp_path))
        started = time.monotonic()
        result = await run(bash, "inherited-stdio", {"command": "echo started; sleep 5 &"})
        elapsed = time.monotonic() - started
        assert "started" in text_output(result)
        assert elapsed < 2, f"the tool hung on the background job: {elapsed:.2f}s"

    async def test_output_after_the_grace_period_is_dropped(self, tmp_path: Path) -> None:
        """`exec` has returned and the snapshot is taken; the TS destroys the
        stream here, so a late write from the descendant reaches nobody. Without
        this the abandoned reader appends to an accumulator whose temp file has
        already been closed."""
        import asyncio

        ops = create_local_bash_operations()
        chunks: list[bytes] = []
        ops.exec(
            "(sleep 0.5; echo late) & echo early",
            str(tmp_path),
            on_data=lambda data: chunks.append(data),
        )
        assert b"early" in b"".join(chunks)
        await asyncio.sleep(1.0)
        assert b"late" not in b"".join(chunks), "output arrived after the command had finished"
