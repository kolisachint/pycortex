# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false
"""Tests for the MCP module."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from cortex.agent.mcp import (
    McpConnection,
    McpToolDef,
    McpToolsServerConfig,
    _create_mcp_agent_tool,
    _install_exit_cleanup,
    _spawn_mcp_server,
    close_mcp_tools,
)


class TestMcpConnection:
    """Tests for McpConnection class."""

    def test_create_connection(self) -> None:
        """Test creating a connection."""
        conn = McpConnection(name="test-server")
        assert conn.name == "test-server"
        assert conn._alive is False
        assert conn.process is None

    def test_terminate_not_connected(self) -> None:
        """Test terminating a connection that was never connected."""
        conn = McpConnection(name="test-server")
        conn.terminate()  # Should not raise
        assert conn._alive is False


class TestMcpToolsServerConfig:
    """Tests for McpToolsServerConfig."""

    def test_create_config(self) -> None:
        """Test creating a server config."""
        config = McpToolsServerConfig(
            name="my-server",
            command="node",
            args=["server.js"],
            env={"PORT": "8080"},
        )
        assert config.name == "my-server"
        assert config.command == "node"
        assert config.args == ["server.js"]
        assert config.env == {"PORT": "8080"}

    def test_create_config_defaults(self) -> None:
        """Test creating a server config with defaults."""
        config = McpToolsServerConfig(name="my-server", command="node")
        assert config.args is None
        assert config.env is None


class TestMcpToolDef:
    """Tests for McpToolDef."""

    def test_create_tool_def(self) -> None:
        """Test creating a tool definition."""
        tool = McpToolDef(
            name="my-tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
        )
        assert tool.name == "my-tool"
        assert tool.description == "A test tool"
        assert tool.input_schema == {"type": "object", "properties": {}}

    def test_create_tool_def_defaults(self) -> None:
        """Test creating a tool definition with defaults."""
        tool = McpToolDef(name="my-tool")
        assert tool.description is None
        assert tool.input_schema is None


class TestCloseMcpTools:
    """Tests for close_mcp_tools."""

    def test_close_no_connections(self) -> None:
        """Test closing when no connections exist."""
        close_mcp_tools()  # Should not raise


class TestInstallExitCleanup:
    """Tests for _install_exit_cleanup."""

    def test_install_twice(self) -> None:
        """Test installing exit cleanup twice."""
        # Reset global state
        import cortex.agent.mcp as mcp_module

        original = mcp_module._exit_cleanup_installed
        mcp_module._exit_cleanup_installed = False

        try:
            _install_exit_cleanup()
            # Second call should be a no-op
            _install_exit_cleanup()
        finally:
            mcp_module._exit_cleanup_installed = original


class TestCreateMcpAgentTool:
    """Tests for _create_mcp_agent_tool."""

    def test_create_agent_tool(self) -> None:
        """Test creating an AgentTool from MCP tool definition."""
        conn = McpConnection(name="test-server")
        tool = McpToolDef(
            name="echo",
            description="Echo back the input",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string", "description": "Message to echo"}},
                "required": ["message"],
            },
        )

        agent_tool = _create_mcp_agent_tool("myserver", conn, tool)
        assert agent_tool.name == "mcp_myserver_echo"
        assert agent_tool.label == "[MCP] myserver \u203a echo"
        assert agent_tool.description == "Echo back the input"
        assert "properties" in agent_tool.parameters
        assert "message" in agent_tool.parameters["properties"]

    def test_create_agent_tool_no_description(self) -> None:
        """Test creating an AgentTool without description."""
        conn = McpConnection(name="test-server")
        tool = McpToolDef(name="simple")

        agent_tool = _create_mcp_agent_tool("myserver", conn, tool)
        assert agent_tool.description == "MCP tool simple from server myserver"


class TestSpawnMcpServer:
    """Tests for _spawn_mcp_server."""

    def test_spawn_server(self) -> None:
        """Test spawning an MCP server."""
        # Use echo as a simple command that won't work as MCP
        # but will test the spawn logic
        config = McpToolsServerConfig(
            name="test-server",
            command="echo",
            args=["hello"],
        )

        conn = _spawn_mcp_server(config)
        assert conn.name == "test-server"
        assert conn.process is not None
        assert conn._alive is True

        conn.terminate()


class TestLoadMcpTools:
    """Tests for load_mcp_tools."""

    def test_load_empty_config(self) -> None:
        """Test loading tools from empty config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"mcpServers": {}}, f)
            f.flush()

            from cortex.agent.mcp import load_mcp_tools

            tools = load_mcp_tools(f.name)
            assert tools == []

        os.unlink(f.name)

    def test_load_no_servers(self) -> None:
        """Test loading tools from config with no servers."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({}, f)
            f.flush()

            from cortex.agent.mcp import load_mcp_tools

            tools = load_mcp_tools(f.name)
            assert tools == []

        os.unlink(f.name)

    def test_load_remote_server_raises(self) -> None:
        """Test loading tools from config with remote server raises."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "mcpServers": {
                        "remote": {
                            "type": "http",
                            "url": "http://localhost:3000",
                        }
                    }
                },
                f,
            )
            f.flush()

            from cortex.agent.mcp import load_mcp_tools

            with pytest.raises(ValueError, match="Remote MCP servers are not yet supported"):
                load_mcp_tools(f.name)

        os.unlink(f.name)

    def test_load_missing_command_raises(self) -> None:
        """Test loading tools from config with missing command raises."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "mcpServers": {
                        "bad-server": {
                            "args": ["server.js"],
                        }
                    }
                },
                f,
            )
            f.flush()

            from cortex.agent.mcp import load_mcp_tools

            with pytest.raises(ValueError, match='missing a "command"'):
                load_mcp_tools(f.name)

        os.unlink(f.name)

    def test_load_file_not_found(self) -> None:
        """Test loading tools from non-existent file."""
        from cortex.agent.mcp import load_mcp_tools

        with pytest.raises(FileNotFoundError):
            load_mcp_tools("/nonexistent/path/mcp.json")
