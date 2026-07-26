# cortexcode-agent-tools

Tool execution and default tools for the pycortex agent module.

## Overview

This package provides:

- `ToolExecutor` - Execute tool calls from the model
- Default tools: bash, read, edit, write, grep, find, ls
- Tool preparation and validation

## Installation

```bash
pip install cortexcode-agent-tools
```

## Usage

```python
from cortex.agent.tools import ToolExecutor
from cortex.agent.tools.default_tools import get_default_tools

tools = get_default_tools()
executor = ToolExecutor(tools=tools)
prepared = await executor.prepare_tool_calls(message, context)
results = await executor.execute_tool_calls(prepared, context)
```

## License

MIT
