# cortexcode-agent-mcp

MCP (Model Context Protocol) integration for the pycortex agent module.

## Overview

This package provides:

- MCP server connection via stdio
- Tool loading from mcp.json configuration
- JSON-RPC communication

## Installation

```bash
pip install cortexcode-agent-mcp
```

## Usage

```python
from cortex.agent.mcp import load_mcp_tools

tools = load_mcp_tools("~/.config/mcp.json")
```

## License

MIT
