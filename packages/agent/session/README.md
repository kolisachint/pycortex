# cortexcode-agent-session

Session management for the pycortex agent module.

## Overview

This package provides session storage and retrieval:

- `Session` - Active session with messages
- `SessionStorage` - Persistence layer (JSONL or in-memory)
- `SessionRepo` - Repository for session metadata

## Installation

```bash
pip install cortexcode-agent-session
```

## Usage

```python
from cortex.agent.session import Session, JsonlSessionStorage

storage = JsonlSessionStorage("~/.pysessions")
session = Session(metadata=metadata, storage=storage)
```

## License

MIT
