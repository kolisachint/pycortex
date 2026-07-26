"""Configuration utilities for package detection and path resolution."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# =============================================================================
# Package Detection
# =============================================================================

# In Python, we don't have Bun binary detection like in TypeScript
# These are False by default since we're running in Python
is_bun_binary = False
is_bun_runtime = False

# =============================================================================
# Install Method Detection
# =============================================================================

InstallMethod = Literal["bun-binary", "npm", "pnpm", "yarn", "bun", "unknown"]


@dataclass
class SelfUpdateCommandStep:
    command: str
    args: list[str]
    display: str


@dataclass
class SelfUpdateCommand(SelfUpdateCommandStep):
    steps: list[SelfUpdateCommandStep] | None = None


def _make_self_update_command(
    install_step: SelfUpdateCommandStep,
    uninstall_step: SelfUpdateCommandStep | None = None,
) -> SelfUpdateCommand:
    if not uninstall_step:
        return SelfUpdateCommand(
            command=install_step.command,
            args=install_step.args,
            display=install_step.display,
        )
    return SelfUpdateCommand(
        command=install_step.command,
        args=install_step.args,
        display=f"{uninstall_step.display} && {install_step.display}",
        steps=[uninstall_step, install_step],
    )


def _make_self_update_command_step(command: str, args: list[str]) -> SelfUpdateCommandStep:
    display_parts = [command] + [f'"{arg}"' if " " in arg else arg for arg in args]
    return SelfUpdateCommandStep(
        command=command,
        args=args,
        display=" ".join(display_parts),
    )


def detect_install_method() -> InstallMethod:
    """Detect the installation method used to install this package."""
    if is_bun_binary:
        return "bun-binary"

    resolved_path = f"{Path(__file__).parent}\0{sys.executable or ''}".lower().replace("\\", "/")

    if "/pnpm/" in resolved_path or "/.pnpm/" in resolved_path:
        return "pnpm"
    if "/yarn/" in resolved_path or "/.yarn/" in resolved_path:
        return "yarn"
    if is_bun_runtime or "/install/global/node_modules/" in resolved_path:
        return "bun"
    if "/npm/" in resolved_path or "/node_modules/" in resolved_path:
        return "npm"

    return "unknown"


def _get_inferred_npm_install() -> dict[str, str] | None:
    """Get inferred npm install location."""
    package_dir = get_package_dir()
    parent = Path(package_dir).parent
    root = None

    if parent.name.startswith("@") and parent.parent.name == "node_modules":
        root = parent.parent.parent
    elif parent.name == "node_modules":
        root = parent

    if root is None:
        return None

    root_parent = root.parent
    if root_parent.name == "lib":
        return {"root": str(root), "prefix": str(root_parent.parent)}

    return None


def _get_self_update_command_for_method(
    method: InstallMethod,
    installed_package_name: str,
    update_package_name: str | None = None,
    npm_command: list[str] | None = None,
) -> SelfUpdateCommand | None:
    """Get the self-update command for a specific install method."""
    if update_package_name is None:
        update_package_name = installed_package_name

    if method == "bun-binary":
        return None
    elif method == "pnpm":
        install_step = _make_self_update_command_step(
            "pnpm", ["install", "-g", update_package_name]
        )
        uninstall_step = None
        if update_package_name != installed_package_name:
            uninstall_step = _make_self_update_command_step(
                "pnpm", ["remove", "-g", installed_package_name]
            )
        return _make_self_update_command(install_step, uninstall_step)
    elif method == "yarn":
        install_step = _make_self_update_command_step(
            "yarn", ["global", "add", update_package_name]
        )
        uninstall_step = None
        if update_package_name != installed_package_name:
            uninstall_step = _make_self_update_command_step(
                "yarn", ["global", "remove", installed_package_name]
            )
        return _make_self_update_command(install_step, uninstall_step)
    elif method == "bun":
        install_step = _make_self_update_command_step("bun", ["install", "-g", update_package_name])
        uninstall_step = None
        if update_package_name != installed_package_name:
            uninstall_step = _make_self_update_command_step(
                "bun", ["uninstall", "-g", installed_package_name]
            )
        return _make_self_update_command(install_step, uninstall_step)
    elif method == "npm":
        command = "npm"
        npm_args: list[str] = []
        if npm_command:
            command = npm_command[0]
            npm_args = npm_command[1:]

        inferred = _get_inferred_npm_install() if not npm_command else None
        prefix_args = npm_args + (["--prefix", inferred["prefix"]] if inferred else [])
        install_step = _make_self_update_command_step(
            command, prefix_args + ["install", "-g", update_package_name]
        )
        uninstall_step = None
        if update_package_name != installed_package_name:
            uninstall_step = _make_self_update_command_step(
                command, prefix_args + ["uninstall", "-g", installed_package_name]
            )
        return _make_self_update_command(install_step, uninstall_step)

    return None


def _read_command_output(
    command: str,
    args: list[str],
    require_success: bool = False,
) -> str | None:
    """Read output from a command."""
    try:
        result = subprocess.run(
            [command] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        if require_success:
            reason = result.stderr.strip() or f"exit code {result.returncode}"
            raise RuntimeError(f"Failed to run {command} {' '.join(args)}: {reason}")
        return None
    except (subprocess.SubprocessError, OSError):
        if require_success:
            raise
        return None


def _get_global_package_roots(
    method: InstallMethod,
    package_name: str,
    npm_command: list[str] | None = None,
) -> list[str]:
    """Get global package roots for a specific install method."""
    if method == "npm":
        configured = bool(npm_command)
        command = "npm"
        npm_args: list[str] = []
        if npm_command:
            command = npm_command[0]
            npm_args = npm_command[1:]

        if configured and command == "bun":
            bun_bin = _read_command_output(
                command, npm_args + ["pm", "bin", "-g"], require_success=True
            )
            roots = [str(Path.home() / ".bun" / "install" / "global" / "node_modules")]
            if bun_bin:
                roots.append(str(Path(bun_bin).parent / "install" / "global" / "node_modules"))
            return roots

        root = _read_command_output(command, npm_args + ["root", "-g"], require_success=configured)
        inferred = None if configured else _get_inferred_npm_install()
        return [r for r in [root, inferred["root"] if inferred else None] if r]

    elif method == "pnpm":
        root = _read_command_output("pnpm", ["root", "-g"])
        if root:
            return [root, str(Path(root).parent)]
        return []

    elif method == "yarn":
        dir_path = _read_command_output("yarn", ["global", "dir"])
        if dir_path:
            return [dir_path, str(Path(dir_path) / "node_modules")]
        return []

    elif method == "bun":
        bun_bin = _read_command_output("bun", ["pm", "bin", "-g"])
        roots = [str(Path.home() / ".bun" / "install" / "global" / "node_modules")]
        if bun_bin:
            roots.append(str(Path(bun_bin).parent / "install" / "global" / "node_modules"))
        return roots

    return []


def _normalize_existing_path_for_comparison(path: str) -> str | None:
    """Normalize a path for comparison purposes."""
    resolved = Path(path).resolve()
    if not resolved.exists():
        return None
    try:
        normalized = resolved.resolve()
    except (OSError, ValueError):
        return None
    if sys.platform == "win32":
        normalized = Path(str(normalized).lower())
    return str(normalized)


def _is_self_update_path_writable() -> bool:
    """Check if the self-update path is writable."""
    package_dir = Path(get_package_dir())
    try:
        return os.access(package_dir, os.W_OK) and os.access(package_dir.parent, os.W_OK)
    except (OSError, ValueError):
        return False


def _is_managed_by_global_package_manager(
    method: InstallMethod,
    package_name: str,
    npm_command: list[str] | None = None,
) -> bool:
    """Check if the installation is managed by a global package manager."""
    package_dir = _normalize_existing_path_for_comparison(get_package_dir())
    if not package_dir:
        return False

    for root in _get_global_package_roots(method, package_name, npm_command):
        normalized_root = _normalize_existing_path_for_comparison(root)
        if normalized_root:
            sep = os.sep
            root_with_sep = (
                normalized_root if normalized_root.endswith(sep) else f"{normalized_root}{sep}"
            )
            if package_dir.startswith(root_with_sep):
                return True

    return False


def get_self_update_command(
    package_name: str,
    npm_command: list[str] | None = None,
    update_package_name: str | None = None,
) -> SelfUpdateCommand | None:
    """Get the command to self-update this package."""
    if update_package_name is None:
        update_package_name = package_name

    method = detect_install_method()
    command = _get_self_update_command_for_method(
        method, package_name, update_package_name, npm_command
    )
    if (
        not command
        or not _is_managed_by_global_package_manager(method, package_name, npm_command)
        or not _is_self_update_path_writable()
    ):
        return None
    return command


def get_self_update_unavailable_instruction(
    package_name: str,
    npm_command: list[str] | None = None,
    update_package_name: str | None = None,
) -> str:
    """Get instructions for updating when self-update is not available."""
    if update_package_name is None:
        update_package_name = package_name

    method = detect_install_method()
    if method == "bun-binary":
        return "Download from: https://github.com/kolisachint/hoocode/releases/latest"

    command = _get_self_update_command_for_method(
        method, package_name, update_package_name, npm_command
    )
    if command:
        if (
            _is_managed_by_global_package_manager(method, package_name, npm_command)
            and not _is_self_update_path_writable()
        ):
            return (
                f"This installation is managed by a global {method} install, "
                f"but the install path is not writable. "
                f"Update it yourself with: {command.display}"
            )
        return (
            f"This installation is not managed by a global {method} install. "
            f"Update it with the package manager, wrapper, or source checkout "
            f"that provides it."
        )

    return (
        f"Update {update_package_name} using the package manager, wrapper, "
        f"or source checkout that provides this installation."
    )


def get_update_instruction(package_name: str) -> str:
    """Get instructions for updating this package."""
    method = detect_install_method()
    command = _get_self_update_command_for_method(method, package_name)
    if command:
        return f"Run: {command.display}"
    return get_self_update_unavailable_instruction(package_name)


# =============================================================================
# Package Asset Paths (shipped with executable)
# =============================================================================

# Application name and config
APP_NAME = "hoocode"
APP_TITLE = "HooCode"
CONFIG_DIR_NAME = ".hoocode"
DISPATCH_DIR_NAME = "dispatch"

# Environment variables
ENV_AGENT_DIR = f"{APP_NAME.upper()}_CODING_AGENT_DIR"
ENV_SESSION_DIR = f"{APP_NAME.upper()}_CODING_AGENT_SESSION_DIR"


def get_package_dir() -> str:
    """Get the base directory for resolving package assets."""
    env_dir = os.environ.get("HOOCODE_PACKAGE_DIR")
    if env_dir:
        return expand_tilde_path(env_dir)

    # In Python, we resolve from the current file's location
    # Walk up until we find a pyproject.toml or similar marker
    current = Path(__file__).parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return str(current)
        current = current.parent

    return str(Path(__file__).parent)


def get_themes_dir() -> str:
    """Get path to built-in themes directory."""
    package_dir = get_package_dir()
    src_or_dist = "src" if (Path(package_dir) / "src").exists() else "dist"
    return str(Path(package_dir) / src_or_dist / "modes" / "interactive" / "theme")


def get_export_template_dir() -> str:
    """Get path to HTML export template directory."""
    package_dir = get_package_dir()
    src_or_dist = "src" if (Path(package_dir) / "src").exists() else "dist"
    return str(Path(package_dir) / src_or_dist / "core" / "export-html")


def get_package_json_path() -> str:
    """Get path to package.json."""
    return str(Path(get_package_dir()) / "package.json")


def get_readme_path() -> str:
    """Get path to README.md."""
    return str(Path(get_package_dir()).resolve() / "README.md")


def get_docs_path() -> str:
    """Get path to docs directory."""
    return str(Path(get_package_dir()).resolve() / "docs")


def get_examples_path() -> str:
    """Get path to examples directory."""
    return str(Path(get_package_dir()).resolve() / "examples")


def get_changelog_path() -> str:
    """Get path to CHANGELOG.md."""
    return str(Path(get_package_dir()).resolve() / "CHANGELOG.md")


def get_interactive_assets_dir() -> str:
    """Get path to built-in interactive assets directory."""
    package_dir = get_package_dir()
    src_or_dist = "src" if (Path(package_dir) / "src").exists() else "dist"
    return str(Path(package_dir) / src_or_dist / "modes" / "interactive" / "assets")


def get_bundled_interactive_asset_path(name: str) -> str:
    """Get path to a bundled interactive asset."""
    return str(Path(get_interactive_assets_dir()) / name)


def get_subagent_spawn_command() -> dict[str, str | list[str]]:
    """Get the command to spawn a subagent child process."""
    return {
        "executable": sys.executable,
        "prefix_args": sys.argv[1:] if len(sys.argv) > 1 else [],
    }


def get_templates_dir() -> str:
    """Get path to bundled init templates."""
    return str(Path(get_package_dir()) / "templates")


def expand_tilde_path(path: str) -> str:
    """Expand ~ in a path to the user's home directory."""
    if path == "~":
        return str(Path.home())
    if path.startswith("~/"):
        return str(Path.home() / path[2:])
    return path


DEFAULT_SHARE_VIEWER_URL = "https://hoocode.dev/session/"


def get_share_viewer_url(gist_id: str) -> str:
    """Get the share viewer URL for a gist ID."""
    base_url = os.environ.get("HOOCODE_SHARE_VIEWER_URL", DEFAULT_SHARE_VIEWER_URL)
    return f"{base_url}#{gist_id}"


# =============================================================================
# User Config Paths (~/.hoocode/*)
# =============================================================================


def get_agent_dir() -> str:
    """Get the agent config directory (e.g., ~/.hoocode/)."""
    env_dir = os.environ.get(ENV_AGENT_DIR)
    if env_dir:
        return expand_tilde_path(env_dir)
    return str(Path.home() / CONFIG_DIR_NAME)


def get_dispatch_root(cwd: str) -> str:
    """Root directory for subagent runtime state within a project."""
    return str(Path(cwd) / CONFIG_DIR_NAME / DISPATCH_DIR_NAME)


def get_dispatch_task_dir(cwd: str, task_id: str) -> str:
    """Per-task runtime directory for a single subagent dispatch."""
    return str(Path(get_dispatch_root(cwd)) / task_id)


def get_hoo_code_dir() -> str:
    """Get the hoocode config root directory."""
    return get_agent_dir()


def get_custom_themes_dir() -> str:
    """Get path to user's custom themes directory."""
    return str(Path(get_agent_dir()) / "themes")


def get_models_path() -> str:
    """Get path to models.json."""
    return str(Path(get_agent_dir()) / "models.json")


def get_auth_path() -> str:
    """Get path to auth.json."""
    return str(Path(get_agent_dir()) / "auth.json")


def get_settings_path() -> str:
    """Get path to settings.json."""
    return str(Path(get_agent_dir()) / "settings.json")


def get_tools_dir() -> str:
    """Get path to tools directory."""
    return str(Path(get_agent_dir()) / "tools")


def get_bin_dir() -> str:
    """Get path to managed binaries directory (fd, rg)."""
    return str(Path(get_agent_dir()) / "bin")


def get_prompts_dir() -> str:
    """Get path to prompt templates directory."""
    return str(Path(get_agent_dir()) / "prompts")


def get_sessions_dir() -> str:
    """Get path to sessions directory."""
    return str(Path(get_agent_dir()) / "sessions")


def get_debug_log_path() -> str:
    """Get path to debug log file."""
    return str(Path(get_agent_dir()) / f"{APP_NAME}-debug.log")
