"""What to tell a user who has no model, or no key for the one they picked.

Port of ``core/auth-guidance.ts``. It lives beside ``config.py`` because the help
text is built from :func:`~cortex.code.config.config.get_docs_path`, and it is
ported now because :meth:`cortex.code.session.AgentSession.prompt` refuses a turn
with no model and owes the user this message rather than a stack trace.
"""

from __future__ import annotations

import os

from cortex.code.config.config import get_docs_path

__all__ = [
    "format_no_api_key_found_message",
    "format_no_model_selected_message",
    "format_no_models_available_message",
    "get_provider_login_help",
]

UNKNOWN_PROVIDER = "unknown"


def get_provider_login_help() -> str:
    """The two-line pointer at ``/login`` and the provider docs."""
    docs = get_docs_path()
    return "\n".join(
        [
            "Use /login to log into a provider via OAuth or API key. See:",
            f"  {os.path.join(docs, 'providers.md')}",
            f"  {os.path.join(docs, 'models.md')}",
        ]
    )


def format_no_models_available_message() -> str:
    return f"No models available. {get_provider_login_help()}"


def format_no_model_selected_message() -> str:
    return (
        f"No model selected.\n\n{get_provider_login_help()}\n\nThen use /model to select a model."
    )


def format_no_api_key_found_message(provider: str) -> str:
    provider_display = "the selected model" if provider == UNKNOWN_PROVIDER else provider
    return f"No API key found for {provider_display}.\n\n{get_provider_login_help()}"
