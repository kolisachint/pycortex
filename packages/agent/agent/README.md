# cortexcode-agent-agent

High-level agent implementation for the pycortex agent module.

## Overview

This package provides the main `Agent` class that combines:

- Agent loop execution
- Message queue management
- Tool registration
- Session handling

## Installation

```bash
pip install cortexcode-agent-agent
```

## Usage

```python
from cortex.agent.agent import Agent

agent = Agent(options)
await agent.run(messages)
```

## License

MIT
