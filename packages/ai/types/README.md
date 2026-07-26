# cortexcode-ai-types

Shared AI types for the pycortex AI module.

## Overview

This package provides type definitions used across the AI module, including:

- Provider types (Anthropic, OpenAI, Google, etc.)
- Message types (AssistantMessage, ToolCall, etc.)
- Model types (Model, ImagesModel, etc.)
- Stream types (StreamOptions, SimpleStreamOptions, etc.)
- Event types (StartEvent, TextDeltaEvent, DoneEvent, etc.)

## Installation

```bash
pip install cortexcode-ai-types
```

## Usage

```python
from cortex.ai.types import (
    Model,
    Provider,
    AssistantMessage,
    StreamOptions,
)
```

## License

MIT
