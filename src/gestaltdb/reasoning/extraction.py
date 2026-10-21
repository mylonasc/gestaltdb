"""Storage-neutral bounded batched extraction contracts; no I/O or backends.

This module defines explicit extraction requests, immutable evidence batches and
backend capability records. It never touches storage, clocks or network state.
Concrete adapters bind these contracts to authenticated read views; the native
Arrow scan API proposed upstream (PyRex #31) is not assumed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from .model import ReasoningError, ReasoningLimitError, _text
from .protocols import Evidence


def _positive(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReasoningError(f"{name} must be a positive integer")
    return value


def _optional_collection(values: object, name: str) -> tuple[str, ...] | None:
    if values is None:
        return None
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ReasoningError(f"{name} must be an explicit collection or None")
    for value in values:
        _text(value, name)
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class ExtractionRequest:
    """Predicate/binding/world selection plus explicit batch/row budgets.

    ``predicates=None`` selects all predicates; an empty collection selects
    none. ``agents``/``sources`` are adapter-enforced selection hints; adapters
    must document whether they push them down or filter in Python. Budgets bound
    yielded logical rows; they are not RSS, snapshot-history or wall-time limits.
    """

    world: str
    predicates: tuple[str, ...] | None = None
    subject: str | None = None
    object: str | None = None
    agents: tuple[str, ...] | None = None
    sources: tuple[str | None, ...] | None = None
    batch_rows: int = 1024
    max_rows: int = 100_000

    def __post_init__(self) -> None:
        _text(self.world, "world")
        object.__setattr__(self, "predicates", _optional_collection(self.predicates, "predicates"))
        object.__setattr__(self, "agents", _optional_collection(self.agents, "agents"))
        if self.sources is not None:
            if not isinstance(self.sources, (tuple, list, set, frozenset)):
                raise ReasoningError("sources must be an explicit collection or None")
            for value in self.sources:
                if value is not None:
                    _text(value, "sources")
            object.__setattr__(self, "sources", tuple(sorted(set(self.sources),
                                                             key=lambda v: (v is not None, v or ""))))
        if self.subject is not None:
            _text(self.subject, "subject")
        if self.object is not None:
            _text(self.object, "object")
        object.__setattr__(self, "batch_rows", _positive(self.batch_rows, "batch_rows"))
        object.__setattr__(self, "max_rows", _positive(self.max_rows, "max_rows"))


@dataclass(frozen=True)
class FactBatch:
    """One immutable bounded batch; ``complete`` marks the final batch only."""

    evidence: tuple[Evidence, ...]
    batch_index: int
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, (tuple, list)) or not self.evidence:
            raise ReasoningError("batch evidence must be a non-empty tuple or list")
        if not all(isinstance(item, Evidence) for item in self.evidence):
            raise ReasoningError("batch evidence must contain only Evidence values")
        if isinstance(self.batch_index, bool) or not isinstance(self.batch_index, int) or self.batch_index < 0:
            raise ReasoningError("batch_index must be a non-negative integer")
        if not isinstance(self.complete, bool):
            raise ReasoningError("complete must be a bool")
        object.__setattr__(self, "evidence", tuple(self.evidence))


def batch_evidence(items: Iterable[Evidence], request: ExtractionRequest) -> Iterator[FactBatch]:
    """Split an evidence stream into bounded immutable batches.

    At most ``max_rows`` values are consumed (one extra read detects overflow).
    Source iterator errors propagate; no partial batch is yielded on failure.
    """
    if not isinstance(request, ExtractionRequest):
        raise ReasoningError("request must be an ExtractionRequest")
    try:
        iterator = iter(items)
    except TypeError as exc:
        raise ReasoningError("evidence source must be iterable") from exc
    index = 0
    yielded = 0
    stash: Evidence | None = None
    stashed = False
    while True:
        batch: list[Evidence] = []
        if stashed:
            batch.append(stash)
            stashed = False
        while len(batch) < request.batch_rows:
            try:
                value = next(iterator)
            except StopIteration:
                break
            if not isinstance(value, Evidence):
                raise ReasoningError("evidence source must yield Evidence values")
            yielded += 1
            if yielded > request.max_rows:
                raise ReasoningLimitError(f"extraction exceeded max_rows={request.max_rows}")
            batch.append(value)
        if not batch:
            return
        try:
            stash = next(iterator)
            stashed = True
        except StopIteration:
            yield FactBatch(tuple(batch), index, True)
            return
        if not isinstance(stash, Evidence):
            raise ReasoningError("evidence source must yield Evidence values")
        yielded += 1
        if yielded > request.max_rows:
            raise ReasoningLimitError(f"extraction exceeded max_rows={request.max_rows}")
        yield FactBatch(tuple(batch), index, False)
        index += 1


@dataclass(frozen=True)
class ExtractionCapability:
    """Audited backend extraction surface; probing never opens storage handles.

    ``native_arrow_scan`` stays False until an installed backend actually
    exposes a validated Arrow scan entry point. A capability record describes
    what was probed, not a performance promise.
    """

    backend: str
    snapshot_kind: str
    pinned_snapshot: bool
    verifiable_snapshot: bool
    predicate_index: bool
    multiget: bool
    native_arrow_scan: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.backend, "backend")
        _text(self.snapshot_kind, "snapshot_kind")
        for name in ("pinned_snapshot", "verifiable_snapshot", "predicate_index",
                     "multiget", "native_arrow_scan"):
            if not isinstance(getattr(self, name), bool):
                raise ReasoningError(f"{name} must be a bool")
        if not isinstance(self.notes, (tuple, list)) or not all(isinstance(n, str) and n for n in self.notes):
            raise ReasoningError("notes must be a tuple of non-empty strings")
        object.__setattr__(self, "notes", tuple(self.notes))

    @classmethod
    def probe(cls, store: object) -> ExtractionCapability:
        """Describe a store object via attributes only; never I/O or handles.

        A ``has_native_arrow_scan`` hook is preferred when present so wrapped
        handles (e.g. transactional vs plain PyRex) report their actual
        runtime. Unknown backends fail closed to an all-False record rather
        than assuming snapshot or index support.
        """
        backend = type(store).__name__ if not isinstance(store, type) else store.__name__
        if not isinstance(backend, str) or not backend:
            raise ReasoningError("store must have a named type")
        namespace = backend.lower()
        pinned = bool(getattr(store, "supports_read_snapshots", False))
        verifiable = bool(getattr(store, "supports_verifiable_read_snapshots", False))
        hook = getattr(store, "has_native_arrow_scan", None)
        if callable(hook):
            try:
                native = bool(hook())
            except Exception:
                native = False
        else:
            native = bool(hasattr(store, "scan_arrow"))
        if "lmdb" in namespace:
            kind, notes = "lmdb-read-transaction", ("read snapshots pin one LMDB read transaction",)
        elif "leveldb" in namespace or "level" in namespace:
            kind, notes = "leveldb-none", ("multi-database layout exposes no pinned read snapshot; explicit snapshot requests fail closed",)
        elif "pyrex" in namespace or "rocksdb" in namespace:
            if native:
                kind = "pyrex-native-snapshot"
                notes = ("native Snapshot/create_snapshot pins one shared horizon on plain handles; "
                         "transactional handles use transaction snapshots instead",)
            elif pinned:
                kind, notes = "pyrex-transaction-snapshot", ("transactional snapshots only; handles without native scans cannot pin reads otherwise",)
            else:
                kind, notes = "pyrex-none", ("handle on a runtime without native scans exposes no pinned read snapshot",)
        else:
            kind, notes = "unknown-none", ("unrecognized backend; no snapshot or index support assumed",)
        if "unknown" in kind or "leveldb" in kind:
            pinned, verifiable, native = False, False, False
        elif native:
            pinned, verifiable = True, True
        return cls(
            backend=backend,
            snapshot_kind=kind,
            pinned_snapshot=pinned,
            verifiable_snapshot=verifiable,
            predicate_index=bool(hasattr(store, "iter_index_prefix")),
            multiget=bool(hasattr(store, "get_nodes_bulk") and hasattr(store, "get_edges_bulk")),
            native_arrow_scan=native,
            notes=notes,
        )

    def require_pinned_snapshot(self) -> None:
        """Fail closed when the caller requests a pinned snapshot we lack."""
        if not self.pinned_snapshot:
            raise ReasoningError(
                f"{self.backend} ({self.snapshot_kind}) exposes no pinned read snapshot; "
                "use an LMDB, transactional-PyRex or native-scan-capable PyRex handle "
                "instead of silently reading without one"
            )
