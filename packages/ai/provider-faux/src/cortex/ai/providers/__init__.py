"""AI faux provider — port of hoocode's ``packages/ai/src/providers/faux.ts``."""

from cortex.ai.providers.faux import (
    DEFAULT_USAGE,
    FauxModelDefinition,
    FauxProviderRegistration,
    RegisterFauxProviderOptions,
    faux_assistant_message,
    faux_text,
    faux_thinking,
    faux_tool_call,
    register_faux_provider,
)

__all__ = [
    "DEFAULT_USAGE",
    "FauxModelDefinition",
    "FauxProviderRegistration",
    "RegisterFauxProviderOptions",
    "faux_assistant_message",
    "faux_text",
    "faux_thinking",
    "faux_tool_call",
    "register_faux_provider",
]
