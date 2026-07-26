# cortexcode-agent-loop

Agent loop implementation for the pycortex agent module.

## Overview

This package provides the core agent loop that orchestrates:

- Message processing
- Tool execution
- LLM API calls
- State management

## Installation

```bash
pip install cortexcode-agent-loop
```

## Usage

```python
from cortex.agent.loop import AgentLoop

loop = AgentLoop(config)
result = await loop.run(context)
```

## License

MIT
