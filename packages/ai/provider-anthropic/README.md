# cortexcode-ai-provider-anthropic

Anthropic AI provider for pycortex.

## Overview

This package provides an Anthropic Claude API provider, including:

- Messages API support
- Streaming responses
- Tool use support
- Extended thinking support

## Installation

```bash
pip install cortexcode-ai-provider-anthropic
```

## Usage

```python
from cortex.ai.providers.anthropic import create_anthropic_provider

provider = create_anthropic_provider(api_key="your-api-key")
```

## License

MIT
