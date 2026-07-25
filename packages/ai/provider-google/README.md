# cortexcode-ai-provider-google

Google Generative AI (Gemini) provider for `cortex.ai`.

Port of hoocode's `packages/ai/src/providers/google-shared.ts` and
`providers/google.ts`. Talks to `:streamGenerateContent?alt=sse` over `httpx`
rather than depending on `@google/genai` — the same choice the anthropic leaf
makes.

`providers/google-vertex.ts` is not here yet; it lands in migration step 2.16.

```python
from cortex.ai.providers.google import stream_google, stream_simple_google
```
