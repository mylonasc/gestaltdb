"""Reentrant process/file coordination independent of graph features.

Lock managers retain only weak references: callers (including waiters) keep a
lock alive while using it. Use a shared manager for cooperating handles in one
process; distinct managers do not share the in-process reentrancy contract.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from weakref import WeakValueDictionary


class ReentrantFileLock:
    """Serialize threads and cooperating processes on one lock file.

    Lock files are never unlinked: replacing them could split process identity.
    The manager, not callers constructing fresh locks, supplies shared identity.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._depth = 0
        self._handle = None

    def __enter__(self):
        self._lock.acquire()
        try:
            if self._depth == 0:
                handle = self.path.open("a+b")
                try:
                    if os.name == "posix":
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    elif os.name == "nt":  # pragma: no cover - Windows-only
                        import msvcrt

                        handle.write(b"\0")
                        handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:  # pragma: no cover - unsupported platform
                        raise RuntimeError("file locking is unsupported on this platform")
                except BaseException:
                    handle.close()
                    raise
                self._handle = handle
            self._depth += 1
            return self
        except BaseException:
            self._lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        self._depth -= 1
        try:
            if self._depth == 0:
                handle = self._handle
                self._handle = None
                try:
                    if os.name == "posix":
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    elif os.name == "nt":  # pragma: no cover - Windows-only
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                finally:
                    handle.close()
        finally:
            self._lock.release()


class LockManager:
    """Canonical live-lock identity with reclaimable registry entries.

    Example::

        manager = LockManager()
        with manager.for_path(database_path / ".writer.lock"):
            persist_batch()

    Directory creation and the protected operation remain caller-owned.
    """

    def __init__(self):
        self._guard = threading.Lock()
        self._locks = WeakValueDictionary()

    def for_path(self, path) -> ReentrantFileLock:
        """Return the shared live lock for a canonical lock-file path."""
        canonical = Path(path).resolve()
        with self._guard:
            lock = self._locks.get(canonical)
            if lock is None:
                lock = ReentrantFileLock(canonical)
                self._locks[canonical] = lock
            return lock

    @property
    def live_lock_count(self) -> int:
        """Number of lock identities currently retained by consumers."""
        with self._guard:
            return len(self._locks)


# One process default is needed to coordinate independently opened handles.
# Ownership is injectable; unlike the former path dictionaries it is bounded
# by live consumers, not by every database ever opened in the process.
DEFAULT_LOCK_MANAGER = LockManager()
