# pyright: reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportReturnType=false
"""Extension system for the Cortex CLI.

Port of ``extensions/**/*.ts`` from ``packages/coding-agent/src/core/``.

Provides a Python plugin API using importlib for loading extensions.
Extensions can:
- Subscribe to agent lifecycle events
- Register LLM-callable tools
- Register commands, keyboard shortcuts, and CLI flags
- Interact with the user via UI primitives
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# ============================================================================
# Extension Types
# ============================================================================


@dataclass
class ExtensionMetadata:
    """Metadata for an extension."""

    name: str
    version: str
    description: str
    author: str | None = None
    homepage: str | None = None
    keywords: list[str] = field(default_factory=list)


@dataclass
class ExtensionContext:
    """Context provided to extensions for interacting with the agent."""

    metadata: ExtensionMetadata
    config: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


class Extension(Protocol):
    """Protocol defining the extension interface."""

    def activate(self, context: ExtensionContext) -> None:
        """Activate the extension.

        Called when the extension is loaded.

        Args:
            context: Extension context.
        """
        ...

    def deactivate(self) -> None:
        """Deactivate the extension.

        Called when the extension is unloaded.
        """
        ...


@dataclass
class ExtensionInfo:
    """Information about a loaded extension."""

    metadata: ExtensionMetadata
    module: Any
    context: ExtensionContext
    is_active: bool = False


# ============================================================================
# Extension Loader
# ============================================================================


class ExtensionLoader:
    """Loads and manages extensions using importlib."""

    def __init__(self, extension_paths: list[str] | None = None):
        """Initialize the extension loader.

        Args:
            extension_paths: Paths to search for extensions.
        """
        self.extension_paths = extension_paths or []
        self.extensions: dict[str, ExtensionInfo] = {}
        self._event_handlers: dict[str, list[Callable[..., Any]]] = {}

    def load_extension(self, path: str) -> ExtensionInfo | None:
        """Load an extension from a path.

        Args:
            path: Path to the extension module.

        Returns:
            ExtensionInfo if loaded, None otherwise.
        """
        try:
            # Convert path to module name
            module_name = Path(path).stem
            if module_name.startswith("_"):
                return None

            # Load the module
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Extract metadata
            metadata = getattr(module, "EXTENSION_METADATA", None)
            if metadata is None:
                metadata = ExtensionMetadata(
                    name=module_name,
                    version="0.0.1",
                    description=getattr(module, "__doc__", "") or "",
                )

            # Create context
            context = ExtensionContext(metadata=metadata)

            # Store extension info
            info = ExtensionInfo(
                metadata=metadata,
                module=module,
                context=context,
            )
            self.extensions[metadata.name] = info
            return info

        except Exception:
            return None

    def activate_extension(self, name: str) -> bool:
        """Activate an extension by name.

        Args:
            name: Name of the extension.

        Returns:
            True if activated successfully.
        """
        if name not in self.extensions:
            return False

        info = self.extensions[name]
        if info.is_active:
            return True

        try:
            # Call activate if available
            activate_fn = getattr(info.module, "activate", None)
            if activate_fn is not None:
                activate_fn(info.context)

            info.is_active = True
            return True

        except Exception:
            return False

    def deactivate_extension(self, name: str) -> bool:
        """Deactivate an extension by name.

        Args:
            name: Name of the extension.

        Returns:
            True if deactivated successfully.
        """
        if name not in self.extensions:
            return False

        info = self.extensions[name]
        if not info.is_active:
            return True

        try:
            # Call deactivate if available
            deactivate_fn = getattr(info.module, "deactivate", None)
            if deactivate_fn is not None:
                deactivate_fn()

            info.is_active = False
            return True

        except Exception:
            return False

    def unload_extension(self, name: str) -> bool:
        """Unload an extension by name.

        Args:
            name: Name of the extension.

        Returns:
            True if unloaded successfully.
        """
        if name not in self.extensions:
            return False

        # Deactivate first
        self.deactivate_extension(name)

        # Remove from loaded extensions
        del self.extensions[name]
        return True

    def get_extension(self, name: str) -> ExtensionInfo | None:
        """Get an extension by name.

        Args:
            name: Name of the extension.

        Returns:
            ExtensionInfo if found, None otherwise.
        """
        return self.extensions.get(name)

    def list_extensions(self) -> list[ExtensionInfo]:
        """List all loaded extensions.

        Returns:
            List of ExtensionInfo objects.
        """
        return list(self.extensions.values())

    def get_active_extensions(self) -> list[ExtensionInfo]:
        """Get all active extensions.

        Returns:
            List of active ExtensionInfo objects.
        """
        return [ext for ext in self.extensions.values() if ext.is_active]

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        """Register an event handler.

        Args:
            event: Event name.
            handler: Event handler function.
        """
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """Emit an event to all registered handlers.

        Args:
            event: Event name.
            *args: Positional arguments.
            **kwargs: Keyword arguments.
        """
        handlers = self._event_handlers.get(event, [])
        for handler in handlers:
            try:
                handler(*args, **kwargs)
            except Exception:
                pass


# ============================================================================
# Extension Registry
# ============================================================================


class ExtensionRegistry:
    """Registry for managing extensions across the application."""

    def __init__(self) -> None:
        """Initialize the extension registry."""
        self.loaders: dict[str, ExtensionLoader] = {}
        self.global_handlers: dict[str, list[Callable[..., Any]]] = {}

    def register_loader(self, name: str, loader: ExtensionLoader) -> None:
        """Register an extension loader.

        Args:
            name: Name of the loader.
            loader: ExtensionLoader instance.
        """
        self.loaders[name] = loader

    def get_loader(self, name: str) -> ExtensionLoader | None:
        """Get a loader by name.

        Args:
            name: Name of the loader.

        Returns:
            ExtensionLoader if found, None otherwise.
        """
        return self.loaders.get(name)

    def load_all_extensions(self, paths: list[str]) -> list[ExtensionInfo]:
        """Load all extensions from the given paths.

        Args:
            paths: List of paths to search for extensions.

        Returns:
            List of loaded ExtensionInfo objects.
        """
        loaded = []
        for path in paths:
            loader = ExtensionLoader(extension_paths=[path])
            for entry in os.scandir(path):
                if entry.is_file() and entry.name.endswith(".py"):
                    info = loader.load_extension(entry.path)
                    if info is not None:
                        loaded.append(info)
        return loaded

    def activate_all(self) -> int:
        """Activate all loaded extensions.

        Returns:
            Number of extensions activated.
        """
        count = 0
        for loader in self.loaders.values():
            for ext in loader.list_extensions():
                if loader.activate_extension(ext.metadata.name):
                    count += 1
        return count

    def deactivate_all(self) -> int:
        """Deactivate all loaded extensions.

        Returns:
            Number of extensions deactivated.
        """
        count = 0
        for loader in self.loaders.values():
            for ext in loader.get_active_extensions():
                if loader.deactivate_extension(ext.metadata.name):
                    count += 1
        return count
