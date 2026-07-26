"""MCP (Model Context Protocol) tool loader.

Port of hoocode's mcp-tools.ts, mcp-http-transport.ts, and mcp-oauth.ts.

This module provides:
- McpConnection: stdio connection to MCP servers
- loadMcpTools: load tools from mcp.json config
- closeMcpTools: close all connections
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class McpToolsServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None


@dataclass
class McpToolDef:
    """Tool definition from MCP server."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None


@dataclass
class McpConnection:
    """Connection to an MCP server via stdio."""

    name: str
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    _next_id: int = field(default=0, init=False)
    _pending: dict[int, tuple[Any, Any]] = field(default_factory=dict, init=False)
    _reader_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _alive: bool = field(default=False, init=False)

    def connect(self, config: McpToolsServerConfig) -> None:
        """Start the MCP server process."""
        args = config.args or []
        env = {**os.environ, **(config.env or {})}

        self.process = subprocess.Popen(
            [config.command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._alive = True
        self._start_reader()

    def _start_reader(self) -> None:
        """Start a thread to read responses from the server."""

        def reader() -> None:
            if not self.process or not self.process.stdout:
                return
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if "id" not in msg or msg["id"] is None:
                        continue
                    msg_id = msg["id"]
                    with self._lock:
                        if msg_id in self._pending:
                            resolve, reject = self._pending.pop(msg_id)
                            if "error" in msg:
                                reject(Exception(msg["error"].get("message", "Unknown error")))
                            else:
                                resolve(msg.get("result"))
                except json.JSONDecodeError:
                    pass
            # Process exited
            with self._lock:
                for _, (_, reject) in self._pending.items():
                    reject(Exception(f'MCP server "{self.name}" exited unexpectedly'))
                self._pending.clear()
            self._alive = False

        self._reader_thread = threading.Thread(target=reader, daemon=True)
        self._reader_thread.start()

    def rpc(self, method: str, params: Any = None, timeout_ms: int | None = None) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if not self.process or not self.process.stdin:
            raise Exception(f'MCP server "{self.name}" not connected')

        with self._lock:
            self._next_id += 1
            msg_id = self._next_id

        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        }

        event = threading.Event()
        result: list[Any] = [None]
        error: list[Exception | None] = [None]

        def resolve(r: Any) -> None:
            result[0] = r
            event.set()

        def reject(e: Exception) -> None:
            error[0] = e
            event.set()

        with self._lock:
            self._pending[msg_id] = (resolve, reject)

        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()

        if event.wait(timeout=timeout_ms / 1000 if timeout_ms else None):
            if error[0]:
                raise error[0]
            return result[0]
        else:
            with self._lock:
                self._pending.pop(msg_id, None)
            raise Exception(f'MCP server "{self.name}" timed out after {timeout_ms}ms on {method}')

    def notify(self, method: str, params: Any = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self.process.stdin.write(json.dumps(notification) + "\n")
        self.process.stdin.flush()

    def terminate(self) -> None:
        """Terminate the MCP server."""
        self._alive = False
        if self.process:
            try:
                self.process.kill()
            except OSError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        # Reject all pending requests
        with self._lock:
            for _, (_, reject) in self._pending.items():
                reject(Exception(f'MCP server "{self.name}" connection terminated'))
            self._pending.clear()


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

MCP_HANDSHAKE_TIMEOUT_MS = 15000

_live_connections: set[McpConnection] = set()
_exit_cleanup_installed = False


def _install_exit_cleanup() -> None:
    """Install exit cleanup handler."""
    global _exit_cleanup_installed
    if _exit_cleanup_installed:
        return
    _exit_cleanup_installed = True
    import atexit

    atexit.register(close_mcp_tools)


def close_mcp_tools() -> None:
    """Terminate every MCP server spawned by load_mcp_tools() in this process."""
    for conn in list(_live_connections):
        try:
            conn.terminate()
        except Exception:
            pass
    _live_connections.clear()


# ---------------------------------------------------------------------------
# Server connection
# ---------------------------------------------------------------------------


def _spawn_mcp_server(config: McpToolsServerConfig) -> McpConnection:
    """Spawn an MCP server process and return a connection."""
    conn = McpConnection(name=config.name)
    conn.connect(config)
    return conn


async def _handshake(conn: McpConnection) -> list[McpToolDef]:
    """Perform MCP handshake and return tool definitions."""
    await conn.rpc(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "pycortex-agent-core", "version": "1.0.0"},
        },
        MCP_HANDSHAKE_TIMEOUT_MS,
    )
    # Notify initialized
    conn.notify("notifications/initialized")
    # Get tools
    tools_result = await conn.rpc("tools/list", {}, MCP_HANDSHAKE_TIMEOUT_MS)
    tools_list = tools_result.get("tools", []) if isinstance(tools_result, dict) else []
    return [
        McpToolDef(
            name=t.get("name", ""),
            description=t.get("description"),
            input_schema=t.get("inputSchema"),
        )
        for t in tools_list
    ]


def _connect_mcp_server(config: McpToolsServerConfig) -> tuple[McpConnection, list[McpToolDef]]:
    """Connect to an MCP server and return connection + tools."""
    conn = _spawn_mcp_server(config)
    try:
        import asyncio

        tools = asyncio.get_event_loop().run_until_complete(_handshake(conn))
        return conn, tools
    except Exception:
        conn.terminate()
        raise


# ---------------------------------------------------------------------------
# Tool creation
# ---------------------------------------------------------------------------


def _create_mcp_agent_tool(server_name: str, conn: McpConnection, tool: McpToolDef) -> Any:
    """Create an AgentTool from an MCP tool definition."""
    from cortex.agent.types import AgentTool, AgentToolResult
    from cortex.ai.types import TextContent

    name = f"mcp_{server_name}_{tool.name}"
    label = f"[MCP] {server_name} \u203a {tool.name}"
    description = tool.description or f"MCP tool {tool.name} from server {server_name}"

    # Convert MCP schema to parameters dict
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    if tool.input_schema:
        props = tool.input_schema.get("properties", {})
        required = set(tool.input_schema.get("required", []))
        for key, prop in props.items():
            parameters["properties"][key] = {
                "type": prop.get("type", "string"),
                "description": prop.get("description"),
            }
            if key in required:
                parameters["required"].append(key)

    async def execute(tool_call_id: str, params: Any, signal: Any = None) -> AgentToolResult[None]:
        result = await conn.rpc("tools/call", {"name": tool.name, "arguments": params})
        return AgentToolResult(
            content=[TextContent(type="text", text=json.dumps(result, indent=2))],
            details=None,
        )

    return AgentTool(
        name=name,
        label=label,
        description=description,
        parameters=parameters,
        execute=execute,
    )


# ---------------------------------------------------------------------------
# mcp.json loading
# ---------------------------------------------------------------------------


def load_mcp_tools(mcp_json_path: str) -> list[Any]:
    """Load MCP tools from an mcp.json file.

    Args:
        mcp_json_path: Path to the mcp.json config file.

    Returns:
        List of AgentTool instances.

    Raises:
        FileNotFoundError: If mcp.json doesn't exist.
        ValueError: If server config is invalid.
    """
    _install_exit_cleanup()

    # Read and parse mcp.json
    raw = Path(mcp_json_path).read_text(encoding="utf-8")
    parsed = json.loads(raw)

    servers = parsed.get("mcpServers", {})
    if not servers:
        return []

    tools: list[Any] = []

    for name, server_config in servers.items():
        if not isinstance(server_config, dict):
            continue

        is_remote = server_config.get("type") in ("http", "sse") or (
            not isinstance(server_config.get("command"), str)
            and isinstance(server_config.get("url"), str)
        )

        if is_remote:
            # Skip remote servers for now (requires HTTP transport + OAuth)
            raise ValueError(
                f'{mcp_json_path}: mcpServers["{name}"] is a remote server '
                f"(type={server_config.get('type')}). Remote MCP servers are not yet supported."
            )

        if not isinstance(server_config.get("command"), str):
            raise ValueError(f'{mcp_json_path}: mcpServers["{name}"] is missing a "command"')

        config = McpToolsServerConfig(
            name=name,
            command=server_config["command"],
            args=server_config.get("args"),
            env=server_config.get("env"),
        )

        conn, server_tools = _connect_mcp_server(config)
        _live_connections.add(conn)

        for tool_def in server_tools:
            tools.append(_create_mcp_agent_tool(name, conn, tool_def))

    return tools
