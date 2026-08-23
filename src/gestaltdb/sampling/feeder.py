"""Async prefetch utilities for sampler batches."""

from __future__ import annotations

from queue import Full, Queue
from threading import Event, Thread
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


class AsyncBatchFeeder(Generic[T]):
    """Background producer that keeps a bounded queue of sampled batches."""

    _STOP = object()

    def __init__(self, producer: Callable[[], T], *, max_prefetch: int = 2):
        if max_prefetch < 1:
            raise ValueError("max_prefetch must be at least 1")
        self.producer = producer
        self.queue: Queue[object] = Queue(max_prefetch)
        self.stop_event = Event()
        self.thread: Thread | None = None

    def start(self) -> "AsyncBatchFeeder[T]":
        """Start the background producer thread."""
        if self.thread is not None and self.thread.is_alive():
            return self
        self.stop_event.clear()
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        """Signal the producer to stop and unblock waiting consumers."""
        self.stop_event.set()
        try:
            self.queue.put_nowait(self._STOP)
        except Exception:
            pass
        if self.thread is not None:
            self.thread.join(timeout=5)

    def get(self, timeout: float | None = None) -> T:
        """Return the next prefetched batch, raising producer errors inline."""
        item = self.queue.get(timeout=timeout)
        if item is self._STOP:
            raise RuntimeError("async batch feeder stopped")
        if isinstance(item, BaseException):
            raise item
        return item

    def __enter__(self) -> "AsyncBatchFeeder[T]":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                item = self.producer()
            except BaseException as exc:  # propagate producer failures to consumer
                self._put_until_stopped(exc)
                self.stop_event.set()
                return
            self._put_until_stopped(item)

    def _put_until_stopped(self, item: object) -> None:
        while not self.stop_event.is_set():
            try:
                self.queue.put(item, timeout=0.1)
                return
            except Full:
                continue
