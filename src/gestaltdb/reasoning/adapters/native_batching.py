"""Explicit bounded native range unions and required-record point fetches.

These raw readers do not authenticate a source. NativePointScan rejects missing
values; it is for required records, not a general nullable multi-get interface.
Both borrow/own snapshots exactly like NativeKeyScan, including early close.
"""

from collections.abc import Sequence
from typing import Any, Protocol, cast

from ..model import ReasoningError
from .native import NativeKeyScan, require_native_scan


MAX_NATIVE_BATCH_ITEMS = 65536
NativeRange = tuple[bytes, bytes]


class NativeBatchHandle(Protocol):
    """Lazy native extension seam; Arrow/snapshot objects stay backend-owned."""

    def scan_arrow_ranges(self, *, ranges: Sequence[NativeRange], **options: Any) -> Any: ...
    def multi_get_arrow(self, *, keys: Sequence[bytes], **options: Any) -> Any: ...


def require_native_batching(store: object) -> NativeBatchHandle:
    """Fail explicit batching requests closed; never fall back to scalar I/O."""
    handle = require_native_scan(store)
    if not all(callable(getattr(handle, name, None))
               for name in ("scan_arrow_ranges", "multi_get_arrow")):
        raise ReasoningError("native history batching requires scan_arrow_ranges and multi_get_arrow")
    return cast(NativeBatchHandle, handle)


class NativeRangeScan(NativeKeyScan):
    """Read a bounded union of half-open byte ranges in native key order."""

    def __init__(self, store: object, *, ranges: Sequence[NativeRange], **options: Any) -> None:
        if any(name in options for name in ("prefix", "start", "stop")):
            raise ReasoningError("range unions accept ranges, not prefix/start/stop")
        if not isinstance(ranges, Sequence) or not 1 <= len(ranges) <= MAX_NATIVE_BATCH_ITEMS:
            raise ReasoningError("ranges must contain 1..65536 byte-bound pairs")
        if any(not isinstance(pair, (tuple, list)) or len(pair) != 2
               or any(type(bound) is not bytes for bound in pair) or pair[0] > pair[1]
               for pair in ranges):
            raise ReasoningError("ranges must contain ordered byte-bound pairs")
        require_native_batching(store)
        self.ranges = tuple((start, stop) for start, stop in ranges)
        super().__init__(store, **options)

    def _open_scan(self, handle: NativeBatchHandle, **options: Any) -> Any:
        for name in ("prefix", "start", "stop"):
            options.pop(name)
        return handle.scan_arrow_ranges(ranges=self.ranges, **options)


class NativePointScan(NativeKeyScan):
    """Fetch required raw keys in input order, preserving input duplicates."""

    def __init__(self, store: object, *, keys: Sequence[bytes], **options: Any) -> None:
        if any(name in options for name in ("prefix", "start", "stop")):
            raise ReasoningError("point fetches accept keys, not prefix/start/stop")
        if (not isinstance(keys, Sequence) or not 1 <= len(keys) <= MAX_NATIVE_BATCH_ITEMS
                or any(type(key) is not bytes for key in keys)):
            raise ReasoningError("keys must contain 1..65536 byte keys")
        require_native_batching(store)
        self.keys = tuple(keys)
        super().__init__(store, **options)

    def _open_scan(self, handle: NativeBatchHandle, **options: Any) -> Any:
        for name in ("prefix", "start", "stop"):
            options.pop(name)
        return handle.multi_get_arrow(keys=self.keys, **options)
