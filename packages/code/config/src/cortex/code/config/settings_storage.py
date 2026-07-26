# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportPrivateUsage=false, reportUnknownParameterType=false, reportUnknownLambdaType=false, reportGeneralTypeIssues=false
"""Settings storage backends for SettingsManager.

A SettingsStorage abstracts read-modify-write of the raw settings JSON for a
scope under an exclusive lock. FileSettingsStorage persists to the global and
project settings.json with cross-process file locking; InMemorySettingsStorage
keeps them in memory for tests.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from cortex.code.config import CONFIG_DIR_NAME

SettingsScope = Literal["global", "project"]


class SettingsError:
    """An error that occurred during settings load or save."""

    def __init__(self, scope: SettingsScope, error: Exception) -> None:
        self.scope = scope
        self.error = error


class SettingsStorage(ABC):
    """Abstract base class for settings storage backends."""

    @abstractmethod
    def with_lock(self, scope: SettingsScope, fn: lambda current: str | None) -> None:
        """Execute fn under an exclusive lock for the given scope.

        fn receives the current content (or None if no file exists) and should
        return the new content to write, or None to skip writing.
        """
        ...


class FileSettingsStorage(SettingsStorage):
    """File-based settings storage with cross-process locking."""

    def __init__(self, cwd: str, agent_dir: str) -> None:
        self.global_settings_path = Path(agent_dir) / "settings.json"
        self.project_settings_path = Path(cwd) / CONFIG_DIR_NAME / "settings.json"

    def _acquire_lock_with_retry(self, path: Path) -> callable:
        """Acquire a file lock with retry logic."""
        max_attempts = 10
        delay_ms = 20
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                # Use a simple lock file approach
                lock_path = path.with_suffix(".lock")
                lock_path.parent.mkdir(parents=True, exist_ok=True)

                # Try to create the lock file exclusively
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, str(os.getpid()).encode())
                    os.close(fd)

                    def release(lp: str = lock_path) -> None:
                        try:
                            os.unlink(str(lp))
                        except OSError:
                            pass

                    return release
                except FileExistsError:
                    # Check if the lock is stale
                    try:
                        lock_mtime = os.path.getmtime(str(lock_path))
                        if time.time() - lock_mtime > 5:  # 5 second stale lock threshold
                            os.unlink(str(lock_path))
                            continue
                    except OSError:
                        pass

                    if attempt < max_attempts:
                        time.sleep(delay_ms / 1000)
                        continue
                    raise OSError(f"Failed to acquire lock on {path}") from None

            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    time.sleep(delay_ms / 1000)
                    continue
                raise

        raise last_error or OSError(f"Failed to acquire lock on {path}")

    def with_lock(self, scope: SettingsScope, fn: lambda current: str | None) -> None:
        path = self.global_settings_path if scope == "global" else self.project_settings_path
        dir_path = path.parent

        release: callable | None = None
        try:
            # Only create directory and lock if file exists or we need to write
            file_exists = path.exists()
            if file_exists:
                release = self._acquire_lock_with_retry(path)
            current = path.read_text(encoding="utf-8") if file_exists else None
            next_content = fn(current)
            if next_content is not None:
                # Only create directory when we actually need to write
                dir_path.mkdir(parents=True, exist_ok=True)
                if release is None:
                    release = self._acquire_lock_with_retry(path)
                path.write_text(next_content, encoding="utf-8")
        finally:
            if release is not None:
                release()


class InMemorySettingsStorage(SettingsStorage):
    """In-memory settings storage for tests."""

    def __init__(self) -> None:
        self._global: str | None = None
        self._project: str | None = None

    def with_lock(self, scope: SettingsScope, fn: lambda current: str | None) -> None:
        current = self._global if scope == "global" else self._project
        next_content = fn(current)
        if next_content is not None:
            if scope == "global":
                self._global = next_content
            else:
                self._project = next_content
