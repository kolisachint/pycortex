# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportOptionalCall=false, reportUnusedFunction=false
"""Tests for the tools module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cortex.agent.tools import ToolExecutor
from cortex.agent.tools.default_tools import (
    DefaultToolsOptions,
    _count_occurrences,
    _format_size,
    _truncate_line,
    create_bash_tool,
    create_edit_tool,
    create_find_tool,
    create_grep_tool,
    create_ls_tool,
    create_read_tool,
    create_write_tool,
    get_default_tools,
)
from cortex.ai.types import AssistantMessage, ToolCall, Usage


class MockExecutionEnv:
    """Mock execution environment for testing."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd

    def exec(
        self,
        command: str,
        timeout: int | None = None,
        signal: Any = None,
    ) -> dict[str, Any]:
        """Execute a shell command."""
        import subprocess

        result = subprocess.run(
            command,
            shell=True,
            cwd=self._cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }

    def read_text_file(self, path: str) -> str:
        """Read a text file."""
        return Path(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        """Write content to a file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    def list_dir(self, path: str) -> list[dict[str, str]]:
        """List directory contents."""
        entries: list[dict[str, str]] = []
        for entry in Path(path).iterdir():
            kind = "directory" if entry.is_dir() else "file"
            entries.append({"name": entry.name, "kind": kind})
        return entries

    def file_info(self, path: str) -> dict[str, str]:
        """Get file info."""
        p = Path(path)
        if p.is_dir():
            return {"kind": "directory", "name": p.name}
        elif p.is_file():
            return {"kind": "file", "name": p.name}
        else:
            return {"kind": "unknown", "name": p.name}


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_format_size_bytes(self) -> None:
        """Test formatting bytes."""
        assert _format_size(100) == "100B"

    def test_format_size_kb(self) -> None:
        """Test formatting kilobytes."""
        assert _format_size(1024) == "1.0KB"
        assert _format_size(1536) == "1.5KB"

    def test_format_size_mb(self) -> None:
        """Test formatting megabytes."""
        assert _format_size(1024 * 1024) == "1.0MB"

    def test_truncate_line_short(self) -> None:
        """Test truncating a short line."""
        assert _truncate_line("Hello", 10) == "Hello"

    def test_truncate_line_long(self) -> None:
        """Test truncating a long line."""
        result = _truncate_line("Hello, World!", 5)
        assert result == "Hell\u2026"

    def test_count_occurrences(self) -> None:
        """Test counting occurrences."""
        assert _count_occurrences("hello world", "o") == 2
        assert _count_occurrences("hello world", "xyz") == 0
        assert _count_occurrences("hello", "") == 0


class TestBashTool:
    """Tests for bash tool."""

    @pytest.mark.asyncio
    async def test_bash_echo(self, tmp_path: Path) -> None:
        """Test bash echo command."""
        env = MockExecutionEnv(str(tmp_path))
        tool = create_bash_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"command": "echo hello"})
        assert "hello" in result.content[0].text

    @pytest.mark.asyncio
    async def test_bash_exit_code(self, tmp_path: Path) -> None:
        """Test bash with non-zero exit code."""
        env = MockExecutionEnv(str(tmp_path))
        tool = create_bash_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"command": "exit 1"})
        assert "Exit code: 1" in result.content[0].text


class TestReadTool:
    """Tests for read tool."""

    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path: Path) -> None:
        """Test reading a file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_read_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"path": "test.txt"})
        assert "Hello, World!" in result.content[0].text

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tmp_path: Path) -> None:
        """Test reading with offset."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_read_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"path": "test.txt", "offset": 2})
        assert "Line 2" in result.content[0].text
        assert "Line 1" not in result.content[0].text

    @pytest.mark.asyncio
    async def test_read_with_limit(self, tmp_path: Path) -> None:
        """Test reading with limit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_read_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"path": "test.txt", "limit": 2})
        assert "Line 1" in result.content[0].text
        assert "Line 2" in result.content[0].text
        assert "Line 3" not in result.content[0].text


class TestEditTool:
    """Tests for edit tool."""

    @pytest.mark.asyncio
    async def test_edit_file(self, tmp_path: Path) -> None:
        """Test editing a file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_edit_tool(env, str(tmp_path))
        result = await tool.execute(
            "call-1",
            {
                "path": "test.txt",
                "edits": [{"oldText": "Hello", "newText": "Goodbye"}],
            },
        )
        assert "Applied 1 edit" in result.content[0].text
        assert test_file.read_text() == "Goodbye, World!"

    @pytest.mark.asyncio
    async def test_edit_multiple(self, tmp_path: Path) -> None:
        """Test multiple edits."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_edit_tool(env, str(tmp_path))
        result = await tool.execute(
            "call-1",
            {
                "path": "test.txt",
                "edits": [
                    {"oldText": "Hello", "newText": "Goodbye"},
                    {"oldText": "World", "newText": "Python"},
                ],
            },
        )
        assert "Applied 2 edits" in result.content[0].text
        assert test_file.read_text() == "Goodbye, Python!"

    @pytest.mark.asyncio
    async def test_edit_not_found(self, tmp_path: Path) -> None:
        """Test edit with non-existent text."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_edit_tool(env, str(tmp_path))
        with pytest.raises(ValueError, match="not found"):
            await tool.execute(
                "call-1",
                {
                    "path": "test.txt",
                    "edits": [{"oldText": "Nonexistent", "newText": "Test"}],
                },
            )

    @pytest.mark.asyncio
    async def test_edit_no_edits(self, tmp_path: Path) -> None:
        """Test edit with no edits provided."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_edit_tool(env, str(tmp_path))
        with pytest.raises(ValueError, match="No edits provided"):
            await tool.execute("call-1", {"path": "test.txt", "edits": []})


class TestWriteTool:
    """Tests for write tool."""

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path: Path) -> None:
        """Test writing a file."""
        test_file = tmp_path / "test.txt"

        env = MockExecutionEnv(str(tmp_path))
        tool = create_write_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"path": "test.txt", "content": "Hello, World!"})
        assert "Wrote" in result.content[0].text
        assert test_file.read_text() == "Hello, World!"

    @pytest.mark.asyncio
    async def test_write_creates_dirs(self, tmp_path: Path) -> None:
        """Test writing creates parent directories."""
        test_file = tmp_path / "subdir" / "test.txt"

        env = MockExecutionEnv(str(tmp_path))
        tool = create_write_tool(env, str(tmp_path))
        await tool.execute("call-1", {"path": "subdir/test.txt", "content": "Hello"})
        assert test_file.exists()


class TestGrepTool:
    """Tests for grep tool."""

    @pytest.mark.asyncio
    async def test_grep_simple(self, tmp_path: Path) -> None:
        """Test simple grep."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello\nWorld\nHello")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_grep_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"pattern": "Hello"})
        assert "Hello" in result.content[0].text

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tmp_path: Path) -> None:
        """Test grep with no matches."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_grep_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"pattern": "Nonexistent"})
        assert "No matches found" in result.content[0].text


class TestFindTool:
    """Tests for find tool."""

    @pytest.mark.asyncio
    async def test_find_simple(self, tmp_path: Path) -> None:
        """Test simple find."""
        (tmp_path / "test.txt").write_text("Hello")
        (tmp_path / "test.py").write_text("World")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_find_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"pattern": "*.txt"})
        assert "test.txt" in result.content[0].text
        assert "test.py" not in result.content[0].text

    @pytest.mark.asyncio
    async def test_find_no_files(self, tmp_path: Path) -> None:
        """Test find with no matching files."""
        (tmp_path / "test.txt").write_text("Hello")

        env = MockExecutionEnv(str(tmp_path))
        tool = create_find_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {"pattern": "*.xyz"})
        assert "No files found" in result.content[0].text


class TestLsTool:
    """Tests for ls tool."""

    @pytest.mark.asyncio
    async def test_ls_simple(self, tmp_path: Path) -> None:
        """Test simple ls."""
        (tmp_path / "file.txt").write_text("Hello")
        (tmp_path / "subdir").mkdir()

        env = MockExecutionEnv(str(tmp_path))
        tool = create_ls_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {})
        assert "file.txt" in result.content[0].text
        assert "subdir/" in result.content[0].text

    @pytest.mark.asyncio
    async def test_ls_empty(self, tmp_path: Path) -> None:
        """Test ls on empty directory."""
        env = MockExecutionEnv(str(tmp_path))
        tool = create_ls_tool(env, str(tmp_path))
        result = await tool.execute("call-1", {})
        assert "(empty directory)" in result.content[0].text


class TestGetDefaultTools:
    """Tests for get_default_tools function."""

    def test_get_default_tools(self) -> None:
        """Test getting default tools."""
        tools = get_default_tools()
        assert len(tools) == 7
        tool_names = [t.name for t in tools]
        assert "bash" in tool_names
        assert "read" in tool_names
        assert "edit" in tool_names
        assert "write" in tool_names
        assert "grep" in tool_names
        assert "find" in tool_names
        assert "ls" in tool_names

    def test_get_default_tools_with_cwd(self, tmp_path: Path) -> None:
        """Test getting default tools with custom cwd."""
        tools = get_default_tools(DefaultToolsOptions(cwd=str(tmp_path)))
        assert len(tools) == 7


class TestToolExecutor:
    """Tests for ToolExecutor class."""

    @pytest.mark.asyncio
    async def test_get_tool(self) -> None:
        """Test getting a tool by name."""
        tools = get_default_tools()
        executor = ToolExecutor(tools=tools)
        assert executor.get_tool("bash") is not None
        assert executor.get_tool("nonexistent") is None

    @pytest.mark.asyncio
    async def test_prepare_tool_calls(self, tmp_path: Path) -> None:
        """Test preparing tool calls."""
        from cortex.agent.types import AgentContext

        tools = get_default_tools(DefaultToolsOptions(cwd=str(tmp_path)))
        executor = ToolExecutor(tools=tools)

        msg = AssistantMessage(
            role="assistant",
            content=[
                ToolCall(
                    name="bash",
                    arguments={"command": "echo hello"},
                    id="call-1",
                )
            ],
            api="test-api",
            provider="test-provider",
            model="test-model",
            usage=Usage(
                input=10,
                output=5,
                cache_read=0,
                cache_write=0,
                total_tokens=15,
                cost={
                    "input": 0.001,
                    "output": 0.002,
                    "cache_read": 0.0,
                    "cache_write": 0.0,
                    "total": 0.003,
                },
            ),
            stop_reason="stop",
            timestamp=1704067200000,
        )

        context = AgentContext(system_prompt="", messages=[])
        prepared = await executor.prepare_tool_calls(msg, context)
        assert len(prepared) == 1
        assert prepared[0]["tool"].name == "bash"
