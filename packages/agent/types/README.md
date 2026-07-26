# cortexcode-agent-types

Shared agent types for the pycortex agent module.

## Overview

This package provides type definitions used across the agent module, including:

- `AgentLoopConfig` - Configuration for the agent loop
- `AgentContext` - Context passed into the agent loop
- `AgentState` - Public agent state
- `AgentTool` - Tool definition with execute function
- `AgentToolResult` - Result from tool execution
- `PendingMessageQueue` - Queue for pending messages

## Installation

```bash
pip install cortexcode-agent-types
```

## Usage

```python
from cortex.agent.types import (
    AgentLoopConfig,
    AgentContext,
    AgentTool,
    AgentToolResult,
)
```

## License

MIT
