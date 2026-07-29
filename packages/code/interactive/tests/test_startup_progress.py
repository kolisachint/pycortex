"""Tests for the transient startup-progress store.

Port of the store half of `test/startup-progress.test.ts`; the footer half lives
in `test_footer.py`, where the rest of the rendering is. The other half of the
TS file drives `reportEmbsearchProgress`, whose module is not ported.
"""

from __future__ import annotations

import pytest
from cortex.code.interactive.startup_progress import (
    DownloadProgress,
    ErrorProgress,
    StartupProgressStore,
    WorkProgress,
    startup_progress,
)


@pytest.fixture
def store() -> StartupProgressStore:
    return StartupProgressStore()


def _download(key: str, received: int = 1, total: int | None = 10) -> DownloadProgress:
    return DownloadProgress(key=key, label=key, received_bytes=received, total_bytes=total)


class TestStore:
    def test_entries_stay_in_insertion_order(self, store: StartupProgressStore):
        store.set(_download("fd"))
        store.set(_download("rg"))
        assert [entry.key for entry in store.list()] == ["fd", "rg"]

    def test_updating_a_key_replaces_in_place_without_reordering(self, store: StartupProgressStore):
        store.set(_download("fd"))
        store.set(_download("rg"))
        store.set(_download("fd", received=9))

        entries = store.list()
        assert [entry.key for entry in entries] == ["fd", "rg"]
        assert entries[0] == _download("fd", received=9)

    def test_remove_drops_one_key_and_clear_wipes_all(self, store: StartupProgressStore):
        store.set(_download("fd"))
        store.set(_download("rg"))
        store.remove("fd")
        assert [entry.key for entry in store.list()] == ["rg"]
        store.clear()
        assert store.list() == []

    def test_the_returned_list_is_a_copy(self, store: StartupProgressStore):
        store.set(_download("fd"))
        store.list().clear()
        assert len(store.list()) == 1

    def test_entries_of_every_kind_live_together(self, store: StartupProgressStore):
        store.set(_download("fd"))
        store.set(WorkProgress(key="index", label="index", done=1, total=2, unit="files"))
        store.set(ErrorProgress(key="rg", label="ripgrep", message="failed"))
        assert [entry.key for entry in store.list()] == ["fd", "index", "rg"]


class TestSubscription:
    def test_set_remove_and_clear_notify(self, store: StartupProgressStore):
        seen: list[int] = []
        store.subscribe(lambda: seen.append(1))

        store.set(_download("fd"))
        store.remove("fd")
        assert len(seen) == 2

    def test_a_no_op_does_not_notify(self, store: StartupProgressStore):
        seen: list[int] = []
        store.subscribe(lambda: seen.append(1))

        # Nothing to remove, and nothing to clear: the footer has no reason to
        # repaint, and a download reporting bytes it already reported is the
        # common case.
        store.remove("absent")
        store.clear()
        assert seen == []

    def test_unsubscribe_stops_the_notifications(self, store: StartupProgressStore):
        seen: list[int] = []
        unsubscribe = store.subscribe(lambda: seen.append(1))
        store.set(_download("fd"))
        unsubscribe()
        store.set(_download("rg"))
        assert len(seen) == 1


def test_the_shared_store_is_a_store():
    assert isinstance(startup_progress, StartupProgressStore)
