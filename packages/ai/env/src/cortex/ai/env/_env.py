"""Environment variable lookup for provider API keys.

Port of hoocode's packages/ai/src/env-api-keys.ts.

Note: the TS original also reads `/proc/self/environ` as a fallback for a Bun
compiled-binary bug (oven-sh/bun#27802) where `process.env` is empty inside
sandboxed environments, and lazily loads `node:fs`/`node:os`/`node:path` to stay
importable from browser bundles. Neither concern applies to Python: `os.environ`
is always populated and available synchronously, so both workarounds are omitted.
"""

import os
from pathlib import Path

_vertex_adc_credentials_exist: bool | None = None


def _has_vertex_adc_credentials() -> bool:
    global _vertex_adc_credentials_exist
    if _vertex_adc_credentials_exist is None:
        # Check GOOGLE_APPLICATION_CREDENTIALS env var first (standard way)
        gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if gac_path:
            _vertex_adc_credentials_exist = Path(gac_path).exists()
        else:
            # Fall back to default ADC path
            _vertex_adc_credentials_exist = (
                Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
            ).exists()
    return _vertex_adc_credentials_exist


def _get_api_key_env_vars(provider: str) -> tuple[str, ...] | None:
    if provider == "github-copilot":
        # Only the explicit COPILOT_GITHUB_TOKEN opts a GitHub token into Copilot
        # inference. GH_TOKEN / GITHUB_TOKEN are ambient in CI and GitHub-integrated
        # environments for *repository* access — treating them as Copilot credentials
        # made auto-selection silently pick Copilot (and fail against its blocked
        # host) whenever those were present. Use /login or COPILOT_GITHUB_TOKEN.
        return ("COPILOT_GITHUB_TOKEN",)

    # ANTHROPIC_OAUTH_TOKEN takes precedence over ANTHROPIC_API_KEY
    if provider == "anthropic":
        return ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY")

    env_map: dict[str, str] = {
        "openai": "OPENAI_API_KEY",
        "azure-openai-responses": "AZURE_OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "google": "GEMINI_API_KEY",
        "google-vertex": "GOOGLE_CLOUD_API_KEY",
        "groq": "GROQ_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "xai": "XAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
        "zai": "ZAI_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "minimax-cn": "MINIMAX_CN_API_KEY",
        "moonshotai": "MOONSHOT_API_KEY",
        "moonshotai-cn": "MOONSHOT_API_KEY",
        "huggingface": "HF_TOKEN",
        "fireworks": "FIREWORKS_API_KEY",
        "together": "TOGETHER_API_KEY",
        "opencode": "OPENCODE_API_KEY",
        "opencode-go": "OPENCODE_API_KEY",
        "kimi-coding": "KIMI_API_KEY",
        "xiaomi": "XIAOMI_API_KEY",
        "xiaomi-token-plan-cn": "XIAOMI_TOKEN_PLAN_CN_API_KEY",
        "xiaomi-token-plan-ams": "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
        "xiaomi-token-plan-sgp": "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
    }

    env_var = env_map.get(provider)
    return (env_var,) if env_var else None


def find_env_keys(provider: str) -> list[str] | None:
    """Find configured environment variables that can provide an API key for a provider.

    This only reports actual API key variables. It intentionally excludes ambient
    credential sources such as AWS profiles, AWS IAM credentials, and Google
    Application Default Credentials.
    """
    env_vars = _get_api_key_env_vars(provider)
    if not env_vars:
        return None

    found = [env_var for env_var in env_vars if os.environ.get(env_var)]
    return found if found else None


def get_env_api_key(provider: str) -> str | None:
    """Get API key for provider from known environment variables, e.g. OPENAI_API_KEY.

    Will not return API keys for providers that require OAuth tokens.
    """
    env_keys = find_env_keys(provider)
    if env_keys:
        return os.environ.get(env_keys[0])

    # Vertex AI supports either an explicit API key or Application Default Credentials.
    # Auth is configured via `gcloud auth application-default login`.
    if provider == "google-vertex":
        has_credentials = _has_vertex_adc_credentials()
        has_project = bool(
            os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
        )
        has_location = bool(os.environ.get("GOOGLE_CLOUD_LOCATION"))

        if has_credentials and has_project and has_location:
            return "<authenticated>"

    return None
