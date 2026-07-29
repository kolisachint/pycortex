"""Watching a path for changes, and surviving the day the OS says no.

Port of ``utils/fs-watch.ts``.

**Node watches; Python polls.** ``fs.watch`` is inotify (or its per-platform
equivalent) behind an event emitter, and the standard library has no equivalent —
so :class:`PathWatcher` samples the path on a background thread and reports what
moved. The contract the TS callers rely on is preserved exactly: a listener
called with ``(event_type, filename)`` for a change under a watched directory,
an error hook when the watch itself dies, and a ``close`` that never raises. What
is *not* preserved is latency — a change is seen within one poll interval rather
than immediately, which is why the caller's debounce window (500 ms in
:class:`~cortex.code.interactive.footer_data_provider.FooterDataProvider`) is
comfortably longer than the interval.

The TS watches a *directory* rather than a file wherever git is involved, because
git writes atomically (write temp, rename over the target) and an inode-bound
watch stops firing after the rename. Polling has no such problem, but the callers
are ported as written: watching the directory is also what makes a *created* file
visible, which an inode watch could never do.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

__all__ = [
    "FS_WATCH_RETRY_DELAY_MS",
    "PathWatcher",
    "close_watcher",
    "watch_with_error_handler",
]

#: How long a caller waits before rebuilding watchers that died (`EMFILE`, a
#: deleted directory). The TS's, to the millisecond.
FS_WATCH_RETRY_DELAY_MS = 5000

#: How often a watcher samples its path. Node's `fs.watch` is event-driven and
#: needs no such number; its `watchFile` polls at 250 ms too.
POLL_INTERVAL_MS = 250

WatchListener = Callable[[str, str | None], None]

#: What a single directory entry (or the watched file itself) looked like last
#: time round: modification time, size, and inode, so an atomic rename registers.
_Stamp = tuple[int, int, int]


class PathWatcher:
    """A polling stand-in for ``fs.watch``, watching a file or a directory."""

    def __init__(
        self,
        path: str,
        listener: WatchListener,
        on_error: Callable[[], None],
        interval_ms: int = POLL_INTERVAL_MS,
    ) -> None:
        self._path = path
        self._listener = listener
        self._on_error = on_error
        self._interval = interval_ms / 1000
        self._stop = threading.Event()
        # Sampled on the calling thread: a path that cannot be read at all is an
        # immediate failure, which `watch_with_error_handler` turns into `None`
        # exactly as `fs.watch` throwing does.
        self._previous = self._snapshot()
        self._thread = threading.Thread(
            target=self._run, name=f"fs-watch:{os.path.basename(path)}", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        """Stop watching. Idempotent, and never raises."""
        self._stop.set()

    @property
    def closed(self) -> bool:
        return self._stop.is_set()

    def _snapshot(self) -> dict[str, _Stamp]:
        """Current state of the watched path, keyed by entry name.

        A watched *file* is a single entry under its own basename, which is what
        makes the listener's ``filename`` argument match the TS for both shapes.
        """
        if os.path.isdir(self._path):
            entries: dict[str, _Stamp] = {}
            for name in os.listdir(self._path):
                stamp = self._stamp(os.path.join(self._path, name))
                if stamp is not None:
                    entries[name] = stamp
            return entries
        stamp = self._stamp(self._path)
        if stamp is None:
            raise FileNotFoundError(self._path)
        return {os.path.basename(self._path): stamp}

    @staticmethod
    def _stamp(path: str) -> _Stamp | None:
        try:
            info = os.stat(path)
        except OSError:
            # A file that vanished between `listdir` and `stat` is a change like
            # any other; the next poll reports it by its absence.
            return None
        return (info.st_mtime_ns, info.st_size, info.st_ino)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                current = self._snapshot()
            except OSError:
                # The directory was removed, or the descriptor limit was hit:
                # `fs.watch`'s `error` event, and the caller rebuilds us.
                if not self._stop.is_set():
                    self._on_error()
                return
            changed = [
                name
                for name in set(current) | set(self._previous)
                if current.get(name) != self._previous.get(name)
            ]
            self._previous = current
            for name in sorted(changed):
                if self._stop.is_set():
                    return
                self._listener("change", name)


def close_watcher(watcher: PathWatcher | None) -> None:
    if watcher is None:
        return
    try:
        watcher.close()
    except Exception:
        # Ignore watcher close errors.
        pass


def watch_with_error_handler(
    path: str,
    listener: WatchListener,
    on_error: Callable[[], None],
    interval_ms: int = POLL_INTERVAL_MS,
) -> PathWatcher | None:
    """Watch ``path``, or report failure through ``on_error`` and return ``None``."""
    try:
        return PathWatcher(path, listener, on_error, interval_ms)
    except OSError:
        on_error()
        return None
