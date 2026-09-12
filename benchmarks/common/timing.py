"""Timing and execution measurement utilities for benchmarks."""

from __future__ import annotations

from contextlib import contextmanager
import os
import random
import time
from typing import Any, Callable, Generator, Iterable, TypeVar

T = TypeVar("T")


def seconds(func: Callable[[], T]) -> tuple[T, float]:
    """Execute ``func()`` and return ``(result, elapsed_seconds)``."""
    started = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - started
    return result, elapsed


def timed(label: str, func: Callable[[], T]) -> tuple[T, float]:
    """Execute ``func()``, print elapsed time, and return ``(result, elapsed_seconds)``."""
    result, elapsed = seconds(func)
    print(f"{label}: {elapsed:.4f}s")
    return result, elapsed


def reservoir_count(records: Iterable[Any], sample_size: int, rng: random.Random) -> int:
    """Return reservoir sample size from streaming iterable without buffering all items."""
    sample: list[Any] = []
    seen = 0
    for record in records:
        seen += 1
        if len(sample) < sample_size:
            sample.append(record)
            continue
        replacement_idx = rng.randrange(seen)
        if replacement_idx < sample_size:
            sample[replacement_idx] = record
    return len(sample)


@contextmanager
def cpu_affinity(cores: int) -> Generator[None, None, None]:
    """Pin execution to a subset of CPU cores if OS supports affinity."""
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        yield
        return
    try:
        original = os.sched_getaffinity(0)
        selected = set(sorted(original)[:cores])
        os.sched_setaffinity(0, selected)
        try:
            yield
        finally:
            os.sched_setaffinity(0, original)
    except Exception:
        yield


def set_thread_env(cores: int) -> None:
    """Set standard multithreading environment variables to restrict thread pools."""
    cores_str = str(cores)
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "POLARS_MAX_THREADS",
        "RAYON_NUM_THREADS",
    ):
        os.environ[var] = cores_str
