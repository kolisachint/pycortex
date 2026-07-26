# cortexcode-ai-provider-google

Google Generative AI (Gemini) provider for `cortex.ai`.

## Overview

This package provides Google AI providers, including:

- Google Generative AI (Gemini) with streaming
- Google Vertex AI support
- Tool use support
- Extended thinking support

## Installation

```bash
pip install cortexcode-ai-provider-google
```

## Usage

```python
from cortex.ai.providers.google import stream_google, stream_simple_google
from cortex.ai.providers.google import stream_vertex, stream_simple_vertex
```

## Notes

- Talks to `:streamGenerateContent?alt=sse` over `httpx` rather than depending on `@google/genai`
- Vertex AI uses `generateContent` REST API with Bearer token authentication
