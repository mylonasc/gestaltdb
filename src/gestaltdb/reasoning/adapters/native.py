"""Bounded native Arrow KV scans over PyRex handles; decoding stays caller-owned.

This adapter consumes the validated ``scan_arrow``/``Snapshot`` entry points
(pyrex-rocksdb>=0.5.0) and yields immutable raw ``(key, value)`` batches with
exact row accounting. Payload bytes are copied out of Arrow buffers, so batches
outlive scans, readers, snapshots and database handles. Nothing here decodes
GestaltDB records, maps predicates, authenticates graph visibility or publishes
derived data: those remain explicit adapter responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..model import ReasoningError, ReasoningLimitError

MIN_NATIVE_SCAN_VERSION = "0.5.0"


def _require_pyarrow():
    try:
        import pyarrow
    except ImportError as exc:
        raise ImportError("native scans require gestaltdb[arrow]") from exc
    return pyarrow


def _native_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return None
    try:
        return version("pyrex-rocksdb")
    except PackageNotFoundError:
        return None


def _at_least(version: str | None, minimum: str) -> bool:
    if not isinstance(version, str):
        return False
    parts: list[int] = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    floor = [int(chunk) for chunk in minimum.split(".")]
    width = max(len(parts), len(floor))
    parts += [0] * (width - len(parts))
    floor += [0] * (width - len(floor))
    return parts >= floor


def require_native_scan(store: object):
    """Return the native database handle or fail closed with the reason.

    The store must expose ``has_native_arrow_scan()`` (preferred) or raw
    ``scan_arrow``/``create_snapshot`` entry points, backed by
    ``pyrex-rocksdb>=0.5.0``. No scan is created here.
    """
    hook = getattr(store, "has_native_arrow_scan", None)
    supported = bool(hook() if callable(hook) else
                     (hasattr(store, "scan_arrow") and hasattr(store, "create_snapshot")))
    if not supported:
        found = _native_version()
        if found is None or not _at_least(found, MIN_NATIVE_SCAN_VERSION):
            raise ImportError(
                "native Arrow scans require pyrex-rocksdb>="
                f"{MIN_NATIVE_SCAN_VERSION} (found {found!r}); "
                "install gestaltdb[rocksdb,arrow] with a recent release"
            ) from None
        raise ReasoningError(
            "this store handle exposes no native Arrow scan entry points; "
            "transactional PyRex handles lack scan_arrow in 0.5.0; "
            "0.6.0 adds default-column-family scans, not named-family scan admission"
        )
    handle = getattr(store, "db", None)
    if handle is None and hasattr(store, "scan_arrow") and hasattr(store, "create_snapshot"):
        handle = store
    if handle is None or not (hasattr(handle, "scan_arrow") and hasattr(handle, "create_snapshot")):
        raise ReasoningError("store exposes no usable native database handle")
    return handle


@dataclass(frozen=True)
class DecodedRow:
    """One decoded native row: fact triple plus exact version/evidence identity."""

    subject: str
    predicate: str
    object: str
    version_id: str
    evidence_id: str

    def __post_init__(self) -> None:
        for field in ("subject", "predicate", "object", "version_id", "evidence_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ReasoningError(f"decoded {field} must be a non-empty string")


@dataclass(frozen=True)
class DecodedBatch:
    """Immutable decoded batch; ``visibility`` pins the authenticated horizon."""

    rows: tuple[DecodedRow, ...]
    batch_index: int
    complete: bool
    visibility: str

    def __post_init__(self) -> None:
        if not isinstance(self.rows, (tuple, list)) or not self.rows:
            raise ReasoningError("decoded batch rows must be non-empty")
        if not all(isinstance(row, DecodedRow) for row in self.rows):
            raise ReasoningError("decoded batch rows must contain only DecodedRow values")
        if isinstance(self.batch_index, bool) or not isinstance(self.batch_index, int) or self.batch_index < 0:
            raise ReasoningError("batch_index must be a non-negative integer")
        if not isinstance(self.complete, bool):
            raise ReasoningError("complete must be a bool")
        if not isinstance(self.visibility, str) or not self.visibility:
            raise ReasoningError("visibility must be a non-empty authenticated token")
        object.__setattr__(self, "rows", tuple(self.rows))


def _check_bindings(bindings) -> dict[str, str]:
    if bindings is None:
        return {}
    if not isinstance(bindings, dict):
        raise ReasoningError("bindings must be a mapping of subject/object to entities")
    cleaned = {}
    for key, value in bindings.items():
        if key not in ("subject", "object"):
            raise ReasoningError("bindings only support subject/object pushdown")
        if not isinstance(value, str) or not value:
            raise ReasoningError("binding values must be non-empty strings")
        cleaned[key] = value
    return cleaned


def iter_decoded_evidence(scan, decode_row, *, predicate: str | None = None,
                          bindings: dict[str, str] | None = None, visibility: str,
                          max_rows: int = 100_000) -> Iterator[DecodedBatch]:
    """Decode native batches into evidence without ever leaking raw rows.

    ``decode_row`` is caller-owned: it maps ``(key, value)`` bytes to a
    ``DecodedRow`` or ``None`` to skip. Corrupt rows fail closed with the
    native/decoding error chained and no raw bytes in the message.
    ``predicate``/``bindings`` push down filters before materialization;
    every batch carries the caller-authenticated ``visibility`` token.
    Version IDs must be unique per scan; duplicates fail closed. The
    ``complete`` flag mirrors the raw batch it was decoded from — it is
    provenance, not framing: end-of-scan is always generator exhaustion,
    since filtered-out tails carry no batch. Decoding
    stays caller-owned — this plumbing only bounds, filters and
    authenticates the handoff into inference.
    """
    if not callable(decode_row):
        raise ReasoningError("decode_row must be callable")
    if predicate is not None and (not isinstance(predicate, str) or not predicate):
        raise ReasoningError("predicate must be a non-empty string or None")
    wanted = _check_bindings(bindings)
    if not isinstance(visibility, str) or not visibility:
        raise ReasoningError("visibility must be a non-empty authenticated token")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ReasoningError("max_rows must be a positive integer")
    try:
        batches = iter(scan)
    except TypeError as exc:
        raise ReasoningError("scan must be iterable of RawBatch values") from exc
    seen: set[str] = set()
    total = 0
    pending: tuple[tuple[DecodedRow, ...], int] | None = None
    raw_complete = False
    for batch in batches:
        if not isinstance(batch, RawBatch):
            raise ReasoningError("scan must yield RawBatch values only")
        rows = []
        for key, value in batch.rows:
            try:
                decoded = decode_row(key, value)
            except Exception as exc:
                raise ReasoningError(f"native row decoding failed: {type(exc).__name__}: {exc}") from exc
            if decoded is None:
                continue
            if not isinstance(decoded, DecodedRow):
                raise ReasoningError("decode_row must return DecodedRow or None")
            if predicate is not None and decoded.predicate != predicate:
                continue
            if any(getattr(decoded, field) != expected for field, expected in wanted.items()):
                continue
            if decoded.version_id in seen:
                raise ReasoningError(f"duplicate version id in native scan: {decoded.version_id!r}")
            seen.add(decoded.version_id)
            rows.append(decoded)
        total += len(rows)
        if total > max_rows:
            raise ReasoningLimitError(f"decoded native scan exceeded max_rows={max_rows}")
        if pending is not None:
            yield DecodedBatch(pending[0], pending[1], False, visibility)
        pending = (tuple(rows), batch.batch_index) if rows else None
        if batch.complete:
            raw_complete = True
            break
    if pending is not None:
        yield DecodedBatch(pending[0], pending[1], raw_complete, visibility)


def iter_locator_keys(scan, *, max_rows: int = 100_000) -> Iterator[bytes]:
    """Yield native keys for caller point-materialization; values are dropped.

    Locator scans skip payload copies for the index-prefix probe, so callers
    can measure locator versus full-scan tradeoffs per backend. Snapshot
    validity stays with the underlying scan; decoding stays caller-owned.
    Column projection would need upstream support and is documented, not
    assumed.
    """
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ReasoningError("max_rows must be a positive integer")
    try:
        batches = iter(scan)
    except TypeError as exc:
        raise ReasoningError("scan must be iterable of RawBatch values") from exc
    total = 0
    for batch in batches:
        if not isinstance(batch, RawBatch):
            raise ReasoningError("scan must yield RawBatch values only")
        for key, _value in batch.rows:
            total += 1
            if total > max_rows:
                raise ReasoningLimitError(f"locator scan exceeded max_rows={max_rows}")
            yield key


@dataclass(frozen=True)
class RawBatch:
    """One immutable copied key/value batch; ``complete`` marks the last one."""

    rows: tuple[tuple[bytes, bytes], ...]
    batch_index: int
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.rows, (tuple, list)) or not self.rows:
            raise ReasoningError("raw batch rows must be non-empty")
        for row in self.rows:
            if (not isinstance(row, (tuple, list)) or len(row) != 2
                    or not all(type(item) is bytes for item in row)):
                raise ReasoningError("raw batch rows must be (key, value) byte pairs")
        if isinstance(self.batch_index, bool) or not isinstance(self.batch_index, int) or self.batch_index < 0:
            raise ReasoningError("batch_index must be a non-negative integer")
        if not isinstance(self.complete, bool):
            raise ReasoningError("complete must be a bool")
        object.__setattr__(self, "rows", tuple((key, value) for key, value in self.rows))


class NativeKeyScan:
    """Single-use bounded scan; snapshot horizon pins at construction.

    Pass ``snapshot=None`` to pin and own a fresh snapshot, or borrow a live
    snapshot owned by the caller (never released here). Closing is idempotent
    and releases only owned resources. Iterating twice raises; sources of
    native errors are wrapped in ``ReasoningError`` with the native type
    chained, so callers never import PyRex exception types.
    """

    def __init__(
        self, store: object, *, snapshot=None, prefix: bytes | None = None,
        start: bytes | None = None, stop: bytes | None = None,
        batch_rows: int = 65536, max_batch_bytes: int = 8 * 1024 * 1024,
        allow_overflow: bool = False, max_rows: int = 1_000_000,
    ) -> None:
        for name, value in (("prefix", prefix), ("start", start), ("stop", stop)):
            if value is not None and type(value) is not bytes:
                raise ReasoningError(f"{name} must be bytes or None")
        if isinstance(batch_rows, bool) or not isinstance(batch_rows, int) or batch_rows < 1:
            raise ReasoningError("batch_rows must be a positive integer")
        if isinstance(max_batch_bytes, bool) or not isinstance(max_batch_bytes, int) or max_batch_bytes < 1:
            raise ReasoningError("max_batch_bytes must be a positive integer")
        if not isinstance(allow_overflow, bool):
            raise ReasoningError("allow_overflow must be a bool")
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
            raise ReasoningError("max_rows must be a positive integer")
        handle = require_native_scan(store)
        _require_pyarrow()
        self._owned = snapshot is None
        try:
            self._snapshot = snapshot if snapshot is not None else handle.create_snapshot()
            self._scan = handle.scan_arrow(prefix=prefix, start=start, stop=stop,
                                           batch_size=batch_rows, max_batch_bytes=max_batch_bytes,
                                           snapshot=self._snapshot, allow_overflow=allow_overflow)
        except Exception as exc:
            if snapshot is None and getattr(self, "_snapshot", None) is not None:
                try:
                    self._snapshot.release()
                except Exception:
                    pass
            raise ReasoningError(f"native scan failed: {type(exc).__name__}: {exc}") from exc
        self._max_rows = max_rows
        self._consumed = False
        self._closed = False

    def _check_snapshot(self, when: str) -> None:
        try:
            valid = self._snapshot.is_valid()
        except Exception as exc:
            raise ReasoningError(f"native snapshot check failed {when}: {type(exc).__name__}: {exc}") from exc
        if not valid:
            raise ReasoningError(f"native snapshot is no longer valid {when}")

    def __iter__(self) -> Iterator[RawBatch]:
        return self.iter_batches()

    def iter_batches(self) -> Iterator[RawBatch]:
        """Copy each Arrow batch into immutable rows; verify the snapshot."""
        if self._consumed:
            raise ReasoningError("native scan is single-use; create a new scan")
        if self._closed:
            raise ReasoningError("native scan is closed")
        self._consumed = True
        pa = _require_pyarrow()
        self._check_snapshot("before iteration")
        try:
            reader = pa.RecordBatchReader.from_stream(self._scan)
        except Exception as exc:
            raise ReasoningError(f"native scan export failed: {type(exc).__name__}: {exc}") from exc
        index, total, pending = 0, 0, None
        try:
            while True:
                try:
                    record = reader.read_next_batch()
                except StopIteration:
                    break
                except Exception as exc:
                    raise ReasoningError(f"native scan read failed: {type(exc).__name__}: {exc}") from exc
                keys = record.column(0).to_pylist()
                values = record.column(1).to_pylist()
                if len(keys) != len(values) or any(item is None for item in keys + values):
                    raise ReasoningError("native scan returned null or ragged key/value columns")
                rows = tuple(zip(keys, values))
                total += len(rows)
                if total > self._max_rows:
                    raise ReasoningLimitError(f"native scan exceeded max_rows={self._max_rows}")
                self._check_snapshot("during iteration")
                if pending is not None:
                    yield pending
                pending = RawBatch(rows, index, False)
                index += 1
            if pending is not None:
                yield RawBatch(pending.rows, pending.batch_index, True)
            self._check_snapshot("after iteration")
        finally:
            try:
                self._scan.close()
            except Exception:
                pass
            if self._owned:
                try:
                    self._snapshot.release()
                except Exception:
                    pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._scan.close()
        except Exception:
            pass
        if self._owned:
            try:
                self._snapshot.release()
            except Exception:
                pass

    def __enter__(self) -> NativeKeyScan:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
