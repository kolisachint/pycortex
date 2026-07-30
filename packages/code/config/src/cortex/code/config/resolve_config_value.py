"""Config values that may be a shell command, an env var name, or a literal.

Port of ``core/resolve-config-value.ts``. It arrives with step 7.11 because both
of that step's modules need it: :mod:`~cortex.code.config.auth_storage` resolves
a stored ``api_key`` through it, and :mod:`~cortex.code.config.model_registry`
resolves ``models.json``'s ``apiKey`` and every header value.

The three-way rule is the whole module: a value starting with ``!`` is a shell
command whose stdout is the value, anything naming an environment variable is
that variable's value, and anything else is itself. The literal fallback is what
makes ``"apiKey": "sk-..."`` work at all, and it is also why the env-var lookup
cannot be an error when the name is unset — an unset ``FOO`` resolves to the
string ``"FOO"``, exactly as in the TS.

**Command results are cached for the life of the process** (`resolve_config_value`)
because a key resolved per-request would shell out on every turn; the *uncached*
variant exists for the paths that must see a fresh value — the registry's auth
status, which reports what a key *is* rather than using it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

__all__ = [
    "clear_config_value_cache",
    "resolve_config_value",
    "resolve_config_value_or_throw",
    "resolve_config_value_uncached",
    "resolve_headers",
    "resolve_headers_or_throw",
]

#: 10 seconds, the TS's `timeout`.
_COMMAND_TIMEOUT_SECONDS = 10.0

#: Command → stdout, for the life of the process.
_command_result_cache: dict[str, str | None] = {}


def resolve_config_value(config: str) -> str | None:
    """Resolve a config value, caching shell-command results."""
    if config.startswith("!"):
        return _execute_command(config)
    env_value = os.environ.get(config)
    return env_value or config


def resolve_config_value_uncached(config: str) -> str | None:
    """Resolve a config value, re-running any shell command."""
    if config.startswith("!"):
        return _execute_command_uncached(config)
    env_value = os.environ.get(config)
    return env_value or config


def resolve_config_value_or_throw(config: str, description: str) -> str:
    """Resolve a config value, raising rather than returning ``None``.

    Used where a missing value has to become a message the user sees — the
    registry turns the raised error into ``ResolvedRequestAuth(ok=False)``.
    """
    resolved_value = resolve_config_value_uncached(config)
    if resolved_value is not None:
        return resolved_value

    if config.startswith("!"):
        raise RuntimeError(f"Failed to resolve {description} from shell command: {config[1:]}")

    raise RuntimeError(f"Failed to resolve {description}")


def resolve_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve every header value, dropping the ones that resolve to nothing."""
    if not headers:
        return None
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved_value = resolve_config_value(value)
        if resolved_value:
            resolved[key] = resolved_value
    return resolved or None


def resolve_headers_or_throw(
    headers: dict[str, str] | None, description: str
) -> dict[str, str] | None:
    """Resolve every header value, raising on the first that cannot be."""
    if not headers:
        return None
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        resolved[key] = resolve_config_value_or_throw(value, f'{description} header "{key}"')
    return resolved or None


def clear_config_value_cache() -> None:
    """Forget every cached command result. Exported for testing."""
    _command_result_cache.clear()


# ---------------------------------------------------------------------------
# Running the command
# ---------------------------------------------------------------------------


def _execute_command(command_config: str) -> str | None:
    if command_config in _command_result_cache:
        return _command_result_cache[command_config]

    result = _execute_command_uncached(command_config)
    _command_result_cache[command_config] = result
    return result


def _execute_command_uncached(command_config: str) -> str | None:
    """Run the command after the ``!`` and hand back its trimmed stdout.

    On Windows the TS prefers a *configured* shell (bash) and falls back to the
    default one only when that shell is not installed — a `models.json` written
    on a Unix box holds Unix commands. Everywhere else there is one shell and no
    choice to make.
    """
    command = command_config[1:]
    if sys.platform == "win32":
        configured = _execute_with_configured_shell(command)
        if configured is not _NOT_EXECUTED:
            return configured
    return _execute_with_default_shell(command)


class _NotExecuted:
    """The TS's ``{ executed: false }``: no shell to run this with.

    A distinct sentinel rather than ``None`` because ``None`` is a real answer —
    "the command ran and produced nothing" must not trigger the fallback that
    "there was no shell" does.
    """


_NOT_EXECUTED = _NotExecuted()


def _execute_with_configured_shell(command: str) -> str | None | _NotExecuted:
    shell = _find_bash()
    if shell is None:
        return _NOT_EXECUTED
    try:
        result = subprocess.run(  # noqa: S603 - a shell command the user configured
            [shell, "-c", command],
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _execute_with_default_shell(command: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S602 - a shell command the user configured
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _find_bash() -> str | None:
    """Git Bash in its two install locations, then any bash on PATH.

    The same order as the bash tool's ``_get_shell_config``, minus its raising
    behaviour: not finding one here is the ``executed: false`` case, which asks
    the caller to try the default shell rather than failing.
    """
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    candidates = [
        f"{root}\\Git\\bin\\bash.exe" for root in (program_files, program_files_x86) if root
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("bash")
