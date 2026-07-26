# cortexcode-agent-compaction

Conversation compaction for the pycortex agent module.

## Overview

This package provides tools for managing conversation length:

- Token estimation
- Message selection for compaction
- Branch summarization
- File operation extraction

## Installation

```bash
pip install cortexcode-agent-compaction
```

## Usage

```python
from cortex.agent.compaction import (
    estimate_tokens,
    should_compact,
    collect_messages_for_compaction,
)

tokens = estimate_tokens(messages)
if should_compact(messages, max_tokens):
    result = collect_messages_for_compaction(messages)
```

## License

MIT
