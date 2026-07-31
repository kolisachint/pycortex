"""Whether this install reports itself. Port of ``core/telemetry.ts``.

One question, asked from one place: the environment overrides the setting
outright rather than merely enabling it, so ``HOOCODE_TELEMETRY=0`` turns
attribution off on a machine whose ``settings.json`` says otherwise.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["is_install_telemetry_enabled"]

_UNSET = object()


def _is_truthy_env_flag(value: str | None) -> bool:
    if not value:
        return False
    return value == "1" or value.lower() in ("true", "yes")


def is_install_telemetry_enabled(settings_manager: Any, telemetry_env: Any = _UNSET) -> bool:
    """The setting, unless ``HOOCODE_TELEMETRY`` is set — then that, either way."""
    env = os.environ.get("HOOCODE_TELEMETRY") if telemetry_env is _UNSET else telemetry_env
    if env is not None:
        return _is_truthy_env_flag(env)
    return bool(settings_manager.get_enable_install_telemetry())
