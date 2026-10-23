"""Benchmark-gated scale decisions: operator API pins and spill/worker gates.

No new runtime lives here. Advanced joins (bitmap/CSR, transitive-specialized,
multiway), differential/signed updates, B/F maintenance and out-of-core
execution are adopted only on measured need against the deployed
semi-naive/DRed/rebuild path — see the frozen benchmark evidence and the
decision record. This module pins the common operator API all engines must
share and fails closed on spill/worker execution without explicit
ownership, cancellation and disk budgets. No universal constant-memory or
zero-copy promises are made.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ReasoningError, _text

OPERATOR_API_VERSION = "v1"


def check_operator_api() -> str:
    """Pin the common operator API every engine must expose and honor.

    Returns the version string on success; any missing entry point fails
    closed so operator drift is caught, not silently absorbed.
    """
    from .typed import GenericEngine

    for entry in ("evaluate", "evaluate_stratified"):
        if not callable(getattr(GenericEngine, entry, None)):
            raise ReasoningError(f"operator API {OPERATOR_API_VERSION} missing entry point: {entry}")
    return OPERATOR_API_VERSION


@dataclass(frozen=True)
class SpillBudget:
    """Explicit disk budget for out-of-core execution; no implicit spilling."""

    directory: str
    max_bytes: int

    def __post_init__(self) -> None:
        _text(self.directory, "directory")
        if isinstance(self.max_bytes, bool) or not isinstance(self.max_bytes, int) or self.max_bytes < 1:
            raise ReasoningError("max_bytes must be a positive integer")

    def check(self, needed_bytes: int) -> None:
        """Fail closed without an explicit budget or when it is exceeded."""
        if isinstance(needed_bytes, bool) or not isinstance(needed_bytes, int) or needed_bytes < 0:
            raise ReasoningError("needed_bytes must be a non-negative integer")
        if needed_bytes > self.max_bytes:
            raise ReasoningError(
                f"spill of {needed_bytes} bytes exceeds budget of {self.max_bytes} bytes")


@dataclass(frozen=True)
class WorkerPolicy:
    """Explicit worker execution policy: fixed count plus cancellation."""

    workers: int
    cancel_supported: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) or self.workers < 1:
            raise ReasoningError("workers must be a positive integer")
        if not isinstance(self.cancel_supported, bool):
            raise ReasoningError("cancel_supported must be a bool")

    def check(self) -> None:
        """Fail closed without cooperative cancellation support."""
        if not self.cancel_supported:
            raise ReasoningError("worker execution requires cooperative cancellation")


def backend_scale_notes() -> tuple[str, ...]:
    """Non-promises every scale proposal must respect."""
    return ("no universal constant-memory guarantee",
            "no zero-copy promise across snapshot boundaries",
            "spill requires caller-owned directories with explicit byte budgets",
            "worker execution requires fixed counts and cooperative cancellation",
            "RSS, thread and I/O measurements accompany any scale claim")
