"""Real thread/process file coordination and bounded registry ownership."""

from concurrent.futures import ThreadPoolExecutor
import gc
import multiprocessing
from pathlib import Path
import threading
import weakref

import pytest

from gestaltdb.concurrency import LockManager


def _process_lock(path, ready, acquired):
    manager = LockManager()
    ready.set()
    with manager.for_path(path):
        acquired.set()


def test_canonical_identity_and_nested_exception_cleanup(tmp_path):
    manager = LockManager()
    lock = manager.for_path(tmp_path / "writer.lock")
    assert manager.for_path(tmp_path / "." / "writer.lock") is lock
    with pytest.raises(ValueError, match="abort"):
        with lock:
            handle = lock._handle
            with manager.for_path(tmp_path / "writer.lock"):
                assert lock._handle is handle
                assert lock._depth == 2
                raise ValueError("abort")
    assert handle.closed
    assert lock._handle is None
    assert lock._depth == 0
    with lock:
        assert lock._depth == 1


def test_registry_reclaims_unused_paths_but_keeps_live_identity(tmp_path):
    manager = LockManager()
    live = manager.for_path(tmp_path / "live")
    for index in range(100):
        unused = manager.for_path(tmp_path / str(index))
    reference = weakref.ref(unused)
    del unused
    gc.collect()
    assert reference() is None
    assert manager.live_lock_count == 1
    assert manager.for_path(tmp_path / "live") is live


def test_failed_open_releases_thread_lock(tmp_path):
    manager = LockManager()
    lock = manager.for_path(tmp_path / "missing" / "writer.lock")
    with pytest.raises(FileNotFoundError):
        with lock:
            pytest.fail("unreachable")
    Path(tmp_path / "missing").mkdir()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_enter_once, lock)
        assert future.result(timeout=5)


def _enter_once(lock):
    with lock:
        return True


def test_waiter_keeps_same_lock_until_holder_releases(tmp_path):
    manager = LockManager()
    attempted, acquired = threading.Event(), threading.Event()
    path = tmp_path / "writer.lock"

    def wait():
        lock = manager.for_path(path)
        attempted.set()
        with lock:
            acquired.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager.for_path(path):
            pending = pool.submit(wait)
            assert attempted.wait(5)
            assert not acquired.wait(0.1)
            gc.collect()
            assert manager.live_lock_count == 1
        pending.result(timeout=5)
    assert acquired.is_set()


def test_unrelated_paths_do_not_share_a_global_operation_mutex(tmp_path):
    manager = LockManager()
    barrier = threading.Barrier(2)

    def enter(name):
        with manager.for_path(tmp_path / name):
            barrier.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(enter, "first")
        second = pool.submit(enter, "second")
        first.result(timeout=10)
        second.result(timeout=10)


def test_cross_process_exclusion(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, acquired = context.Event(), context.Event()
    manager = LockManager()
    path = tmp_path / "writer.lock"
    process = context.Process(target=_process_lock, args=(path, ready, acquired))
    try:
        with manager.for_path(path):
            process.start()
            assert ready.wait(10)
            assert not acquired.wait(0.2)
        assert acquired.wait(10)
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
