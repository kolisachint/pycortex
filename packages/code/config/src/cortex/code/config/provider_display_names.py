"""How each built-in provider's name is spelled on screen.

Port of ``core/provider-display-names.ts``. Two callers, and they read it for
different reasons: the registry falls back to this table when nothing has
registered a nicer name, and ``login_controller.is_api_key_login_provider`` uses
*membership* in it as the test for "this provider takes an API key" — which is
why the table is a port rather than something regenerated from the provider list.
"""

from __future__ import annotations

__all__ = ["BUILT_IN_PROVIDER_DISPLAY_NAMES"]

BUILT_IN_PROVIDER_DISPLAY_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "azure-openai-responses": "Azure OpenAI Responses",
    "cerebras": "Cerebras",
    "deepseek": "DeepSeek",
    "fireworks": "Fireworks",
    "google": "Google Gemini",
    "google-vertex": "Google Vertex AI",
    "groq": "Groq",
    "huggingface": "Hugging Face",
    "kimi-coding": "Kimi For Coding",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax (China)",
    "moonshotai": "Moonshot AI",
    "moonshotai-cn": "Moonshot AI (China)",
    "opencode": "OpenCode Zen",
    "opencode-go": "OpenCode Go",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "together": "Together AI",
    "vercel-ai-gateway": "Vercel AI Gateway",
    "xai": "xAI",
    "zai": "ZAI",
    "xiaomi": "Xiaomi MiMo",
    "xiaomi-token-plan-cn": "Xiaomi MiMo Token Plan (China)",
    "xiaomi-token-plan-ams": "Xiaomi MiMo Token Plan (Amsterdam)",
    "xiaomi-token-plan-sgp": "Xiaomi MiMo Token Plan (Singapore)",
    "nvidia": "NVIDIA",
}
