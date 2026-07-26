# cortexcode-agent-harness

Agent harness for the pycortex agent module.

## Overview

This package provides the harness layer that manages:

- System prompt construction
- Message formatting
- Skill management
- Tool preparation

## Installation

```bash
pip install cortexcode-agent-harness
```

## Usage

```python
from cortex.agent.harness import AgentHarness

harness = AgentHarness(config)
messages = await harness.prepare_messages(context)
```

## License

MIT
