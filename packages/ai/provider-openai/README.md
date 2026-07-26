# cortexcode-ai-provider-openai

OpenAI AI provider for pycortex.

## Overview

This package provides OpenAI API providers, including:

- Completions API (GPT models)
- Responses API (GPT-4, etc.)
- Azure OpenAI support
- OpenAI Codex support
- Streaming responses

## Installation

```bash
pip install cortexcode-ai-provider-openai
```

## Usage

```python
from cortex.ai.providers.openai import create_openai_provider

provider = create_openai_provider(api_key="your-api-key")
```

## License

MIT
