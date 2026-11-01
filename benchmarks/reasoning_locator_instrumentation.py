"""Opt-in, benchmark-only attribution for #451/HSA-01.

This module does not change production semantics. It wraps existing store and
temporal operations inside one benchmark worker so reports can distinguish
physical metadata/index reads, supersession-target fallback work, checksum
marker visits, construction phases and native scan work. Timings are
**inclusive, non-exclusive** attribution buckets: nested operations can
contribute to more than one bucket and do not sum to end-to-end time.

The wrappers are installed only while the context manager is active and cover
concrete store classes, including transaction-bound children. They are not a
public instrumentation API, an authentication mechanism or an RSS estimator.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import functools
import time
from typing import Any, Callable, Iterator

from gestaltdb._graph.temporal_keys import (
    _TEMPORAL_COMMIT_PREFIX,
    _TEMPORAL_RECORD_PREFIX,
    _TEMPORAL_SEQUENCE_KEY,
    _TEMPORAL_VISIBLE_PREFIX,
)
from gestaltdb._graph import index_maintenance as _index_maintenance
from gestaltdb._graph import temporal_indexes as _temporal_indexes
from gestaltdb._graph import temporal_reads as _temporal_reads
from gestaltdb._graph import temporal_writes as _temporal_writes
from gestaltdb.kvstores import KVStore
from gestaltdb.reasoning.adapters import native as _native
from gestaltdb.reasoning.adapters import native_claims as _native_claims
from gestaltdb.reasoning.adapters import native_locator_claims as _locator_claims


_DEFAULT_INDEX_STATE_KEY = _temporal_indexes.INDEX_STATE_KEY
_PEAK_SUFFIX = "_peak"


def _metadata_category(key: bytes) -> str:
    if key.startswith(_TEMPORAL_COMMIT_PREFIX):
        return "commit_descriptor"
    if key.startswith(_TEMPORAL_RECORD_PREFIX):
        return "record_envelope"
    if key.startswith(_TEMPORAL_VISIBLE_PREFIX):
        return "visibility_marker"
    if key == _TEMPORAL_SEQUENCE_KEY:
        return "sequence"
    if key == _DEFAULT_INDEX_STATE_KEY:
        return "index_state"
    return "other_metadata"


def _bump(mapping: dict[str, int], name: str, amount: int = 1) -> None:
    mapping[name] = mapping.get(name, 0) + amount


@dataclass
class _Counters:
    # physical store work
    metadata_reads: int = 0
    metadata_writes: int = 0
    metadata_deletes: int = 0
    metadata_reads_by_category: dict[str, int] = field(default_factory=dict)
    metadata_writes_by_category: dict[str, int] = field(default_factory=dict)
    metadata_reads_by_scope: dict[str, int] = field(default_factory=dict)
    metadata_writes_by_scope: dict[str, int] = field(default_factory=dict)
    metadata_read_s_by_category: dict[str, float] = field(default_factory=dict)
    index_scan_openings: int = 0
    index_rows: int = 0
    index_scan_s: float = 0.0
    index_scan_openings_by_scope: dict[str, int] = field(default_factory=dict)

    # native transfer and extraction
    native_scan_openings: int = 0
    native_batches: int = 0
    native_rows: int = 0
    native_scan_s: float = 0.0
    native_prepare_calls: int = 0
    native_prepare_s: float = 0.0
    native_selected_records_peak: int = 0

    # historical authentication and target resolution
    commit_auth_calls: int = 0
    commit_auth_s: float = 0.0
    decode_calls: int = 0
    decode_s: float = 0.0
    target_lookup_calls: int = 0
    target_lookup_horizon_sum: int = 0
    target_lookup_commit_visits: int = 0
    target_lookup_s: float = 0.0
    distinct_target_version_ids: int = 0
    validation_target_calls: int = 0
    validation_target_s: float = 0.0
    retraction_validation_calls: int = 0
    retraction_validation_s: float = 0.0
    version_id_exists_calls: int = 0
    version_id_exists_s: float = 0.0
    checksum_calls: int = 0
    checksum_marker_visits: int = 0
    checksum_s: float = 0.0
    canonical_conformance_calls: int = 0
    canonical_conformance_s: float = 0.0

    # construction phases
    commit_calls: int = 0
    commit_s: float = 0.0
    sequence_reservations: int = 0
    sequence_reserve_s: float = 0.0
    index_write_calls: int = 0
    index_write_s: float = 0.0
    index_entries_written: int = 0
    publication_calls: int = 0
    publication_s: float = 0.0
    stale_mark_calls: int = 0
    stale_mark_s: float = 0.0

    # retained state
    commit_cache_entries_peak: int = 0

    _target_version_ids: set[str] = field(default_factory=set)
    _target_lookup_depth: int = 0

    def snapshot(self) -> dict[str, Any]:
        values = {
            name: (dict(value) if isinstance(value, dict) else value)
            for name, value in vars(self).items()
            if not name.startswith("_")
        }
        values["timing_boundary"] = (
            "inclusive non-exclusive attribution buckets; nested work contributes "
            "to multiple buckets and does not sum to end-to-end time"
        )
        values["validation_boundary"] = (
            "validation_target_* covers supersession get_version calls and "
            "version_id_exists; other commit validation/encoding is only in commit_s"
        )
        return values


def _delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """Return a stage delta for cumulative counters and nested mappings.

    Peak counters are process-wide maxima, so a stage delta reports the
    observed maximum rather than a meaningless subtraction.
    """
    result: dict[str, Any] = {}
    for key, value in after.items():
        if key in ("timing_boundary", "validation_boundary"):
            continue
        if key.endswith(_PEAK_SUFFIX):
            result[key] = value
        elif isinstance(value, dict):
            previous = before.get(key) or {}
            if all(isinstance(item, float) for item in value.values()):
                result[key] = {name: value.get(name, 0.0) - previous.get(name, 0.0)
                               for name in set(value) | set(previous)}
            else:
                result[key] = {name: value.get(name, 0) - previous.get(name, 0)
                               for name in set(value) | set(previous)}
        elif isinstance(value, (int, float)):
            result[key] = value - before.get(key, 0)
    result["timing_boundary"] = after["timing_boundary"]
    result["validation_boundary"] = after["validation_boundary"]
    return result


EventHook = Callable[[str, dict[str, Any]], None]


class LocatorInstrumentation:
    """Install benchmark counters around existing classes for one process scope."""

    def __init__(self, events: EventHook | None = None) -> None:
        self._counters = _Counters()
        self._restore: list[tuple[Any, str, Any]] = []
        self._events = events

    def _event(self, name: str, **details: Any) -> None:
        if self._events is not None:
            self._events(name, details)

    def _patch(self, target: Any, name: str, replacement: Any) -> None:
        self._restore.append((target, name, getattr(target, name)))
        setattr(target, name, replacement)

    def _timed(self, bucket: str, function: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            begin = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                setattr(self._counters, bucket + "_s", getattr(self._counters, bucket + "_s") + (
                    time.perf_counter() - begin))
        return wrapped

    def _instrument_store_class(self, store_type: type[KVStore]) -> None:
        counters = self._counters
        original_get = getattr(store_type, "get_metadata", None)
        if original_get is not None and "get_metadata" in store_type.__dict__:

            @functools.wraps(original_get)
            def get_metadata(store: KVStore, key: bytes) -> bytes | None:
                scope = type(store).__name__
                category = _metadata_category(key)
                begin = time.perf_counter()
                try:
                    return original_get(store, key)
                finally:
                    counters.metadata_reads += 1
                    _bump(counters.metadata_reads_by_category, category)
                    _bump(counters.metadata_reads_by_scope, scope)
                    _bump(counters.metadata_read_s_by_category, category,
                          time.perf_counter() - begin)

            self._patch(store_type, "get_metadata", get_metadata)

        original_put = getattr(store_type, "put_metadata", None)
        if original_put is not None and "put_metadata" in store_type.__dict__:

            @functools.wraps(original_put)
            def put_metadata(store: KVStore, key: bytes, value: bytes) -> None:
                scope = type(store).__name__
                category = _metadata_category(key)
                try:
                    return original_put(store, key, value)
                finally:
                    counters.metadata_writes += 1
                    _bump(counters.metadata_writes_by_category, category)
                    _bump(counters.metadata_writes_by_scope, scope)

            self._patch(store_type, "put_metadata", put_metadata)

        original_delete = getattr(store_type, "delete_metadata", None)
        if original_delete is not None and "delete_metadata" in store_type.__dict__:

            @functools.wraps(original_delete)
            def delete_metadata(store: KVStore, key: bytes) -> None:
                try:
                    return original_delete(store, key)
                finally:
                    counters.metadata_deletes += 1

            self._patch(store_type, "delete_metadata", delete_metadata)

        for method_name in ("iter_index_prefix", "iter_range_index"):
            original = getattr(store_type, method_name, None)
            if original is None or method_name not in store_type.__dict__:
                continue

            def iter_index(store: KVStore, *args: Any, _original=original, **kwargs: Any) -> Iterator[bytes]:
                counters.index_scan_openings += 1
                _bump(counters.index_scan_openings_by_scope, type(store).__name__)
                iterator = iter(_original(store, *args, **kwargs))
                while True:
                    begin = time.perf_counter()
                    try:
                        value = next(iterator)
                    except StopIteration:
                        return
                    finally:
                        counters.index_scan_s += time.perf_counter() - begin
                    counters.index_rows += 1
                    yield value

            self._patch(store_type, method_name, iter_index)

    @contextmanager
    def _active(self) -> Iterator[LocatorInstrumentation]:
        self._install()
        try:
            yield self
        finally:
            self._uninstall()

    def _install(self) -> None:
        from gestaltdb import kvstores as stores

        store_types = {
            value for value in vars(stores).values()
            if isinstance(value, type) and issubclass(value, KVStore) and value is not KVStore
        }
        for store_type in sorted(store_types, key=lambda item: (len(item.__mro__), item.__name__)):
            self._instrument_store_class(store_type)

        counters = self._counters
        reader = _temporal_reads.TemporalReader
        original_commit = reader.get_commit_cached

        @functools.wraps(original_commit)
        def get_commit_cached(reader_self: Any, commit_id: int, *args: Any, **kwargs: Any) -> Any:
            counters.commit_auth_calls += 1
            if counters._target_lookup_depth:
                counters.target_lookup_commit_visits += 1
            begin = time.perf_counter()
            try:
                return original_commit(reader_self, commit_id, *args, **kwargs)
            finally:
                counters.commit_auth_s += time.perf_counter() - begin
                counters.commit_cache_entries_peak = max(
                    counters.commit_cache_entries_peak, len(reader_self.commit_cache))

        self._patch(reader, "get_commit_cached", get_commit_cached)

        original_decode = reader.decode_version

        @functools.wraps(original_decode)
        def decode_version(reader_self: Any, envelope: bytes) -> Any:
            counters.decode_calls += 1
            begin = time.perf_counter()
            try:
                return original_decode(reader_self, envelope)
            finally:
                counters.decode_s += time.perf_counter() - begin

        self._patch(reader, "decode_version", decode_version)

        original_get_version = reader.get_version

        @functools.wraps(original_get_version)
        def get_version(reader_self: Any, version_id: str) -> Any:
            counters.validation_target_calls += 1
            begin = time.perf_counter()
            try:
                return original_get_version(reader_self, version_id)
            finally:
                counters.validation_target_s += time.perf_counter() - begin

        self._patch(reader, "get_version", get_version)

        original_through = reader.get_version_through

        @functools.wraps(original_through)
        def get_version_through(reader_self: Any, version_id: str, through_commit: int) -> Any:
            counters.target_lookup_calls += 1
            counters.target_lookup_horizon_sum += max(0, int(through_commit))
            if version_id not in counters._target_version_ids:
                counters._target_version_ids.add(version_id)
                counters.distinct_target_version_ids += 1
            counters._target_lookup_depth += 1
            begin = time.perf_counter()
            try:
                return original_through(reader_self, version_id, through_commit)
            finally:
                counters.target_lookup_s += time.perf_counter() - begin
                counters._target_lookup_depth -= 1

        self._patch(reader, "get_version_through", get_version_through)

        committer = _temporal_writes.TemporalCommitter
        original_exists = committer.version_id_exists

        @functools.wraps(original_exists)
        def version_id_exists(committer_self: Any, version_id: str) -> bool:
            counters.version_id_exists_calls += 1
            begin = time.perf_counter()
            try:
                return original_exists(committer_self, version_id)
            finally:
                counters.version_id_exists_s += time.perf_counter() - begin

        self._patch(committer, "version_id_exists", version_id_exists)

        original_commit_direct = committer.commit_direct

        @functools.wraps(original_commit_direct)
        def commit_direct(committer_self: Any, writes: Any, metadata: Any, index_mode: Any) -> Any:
            counters.commit_calls += 1
            self._event("commit", versions=len(writes), index_mode=str(index_mode))
            begin = time.perf_counter()
            try:
                return original_commit_direct(committer_self, writes, metadata, index_mode)
            finally:
                counters.commit_s += time.perf_counter() - begin

        self._patch(committer, "commit_direct", commit_direct)

        original_retraction = _temporal_writes.require_retraction_identities

        @functools.wraps(original_retraction)
        def require_retraction_identities(*args: Any, **kwargs: Any) -> Any:
            counters.retraction_validation_calls += 1
            begin = time.perf_counter()
            try:
                return original_retraction(*args, **kwargs)
            finally:
                counters.retraction_validation_s += time.perf_counter() - begin

        self._patch(_temporal_writes, "require_retraction_identities",
                    require_retraction_identities)

        original_reserve = _temporal_writes.reserve_temporal_sequence

        @functools.wraps(original_reserve)
        def reserve_temporal_sequence(*args: Any, **kwargs: Any) -> Any:
            counters.sequence_reservations += 1
            self._event("sequence-reservation")
            begin = time.perf_counter()
            try:
                result = original_reserve(*args, **kwargs)
            finally:
                counters.sequence_reserve_s += time.perf_counter() - begin
            if isinstance(result, tuple) and result and isinstance(result[0], int):
                self._event("sequence-reserved", commit_id=result[0])
            return result

        self._patch(_temporal_writes, "reserve_temporal_sequence", reserve_temporal_sequence)

        original_write_indexes = _temporal_indexes.write_temporal_indexes

        @functools.wraps(original_write_indexes)
        def write_temporal_indexes(*args: Any, **kwargs: Any) -> Any:
            counters.index_write_calls += 1
            commit_id = kwargs.get("commit_id", args[2] if len(args) > 2 else None)
            self._event("index-write", commit_id=commit_id)
            begin = time.perf_counter()
            try:
                result = original_write_indexes(*args, **kwargs)
            finally:
                counters.index_write_s += time.perf_counter() - begin
            if isinstance(result, dict):
                counters.index_entries_written += sum(
                    value for value in result.values() if isinstance(value, int))
            return result

        self._patch(_temporal_indexes, "write_temporal_indexes", write_temporal_indexes)

        original_publish = _temporal_indexes.persist_index_state

        @functools.wraps(original_publish)
        def persist_index_state(*args: Any, **kwargs: Any) -> Any:
            counters.publication_calls += 1
            self._event("index-publication")
            begin = time.perf_counter()
            try:
                return original_publish(*args, **kwargs)
            finally:
                counters.publication_s += time.perf_counter() - begin

        self._patch(_temporal_indexes, "persist_index_state", persist_index_state)

        original_mark_stale = _index_maintenance.mark_indexes_stale

        @functools.wraps(original_mark_stale)
        def mark_indexes_stale(*args: Any, **kwargs: Any) -> Any:
            counters.stale_mark_calls += 1
            begin = time.perf_counter()
            try:
                return original_mark_stale(*args, **kwargs)
            finally:
                counters.stale_mark_s += time.perf_counter() - begin

        self._patch(_index_maintenance, "mark_indexes_stale", mark_indexes_stale)

        original_checksum = _temporal_indexes.visibility_checksum

        @functools.wraps(original_checksum)
        def visibility_checksum(store: Any, through_commit: int, **kwargs: Any) -> str:
            counters.checksum_calls += 1
            counters.checksum_marker_visits += max(0, int(through_commit))
            begin = time.perf_counter()
            try:
                return original_checksum(store, through_commit, **kwargs)
            finally:
                counters.checksum_s += time.perf_counter() - begin

        self._patch(_temporal_indexes, "visibility_checksum", visibility_checksum)

        original_scan_init = _native.NativeKeyScan.__init__
        original_scan_iter = _native.NativeKeyScan.iter_batches

        @functools.wraps(original_scan_init)
        def scan_init(scan_self: Any, *args: Any, **kwargs: Any) -> None:
            counters.native_scan_openings += 1
            return original_scan_init(scan_self, *args, **kwargs)

        @functools.wraps(original_scan_iter)
        def scan_iter(scan_self: Any) -> Iterator[Any]:
            iterator = iter(original_scan_iter(scan_self))
            while True:
                begin = time.perf_counter()
                try:
                    batch = next(iterator)
                except StopIteration:
                    return
                finally:
                    counters.native_scan_s += time.perf_counter() - begin
                counters.native_batches += 1
                counters.native_rows += len(batch.rows)
                yield batch

        self._patch(_native.NativeKeyScan, "__init__", scan_init)
        self._patch(_native.NativeKeyScan, "iter_batches", scan_iter)

        def wrap_prepare(source_type: type[Any]) -> None:
            original = source_type.__dict__.get("_read_winners")
            if original is None:
                return

            @functools.wraps(original)
            def read_winners(source_self: Any, cancel: Any) -> Any:
                counters.native_prepare_calls += 1
                begin = time.perf_counter()
                try:
                    result = original(source_self, cancel)
                finally:
                    counters.native_prepare_s += time.perf_counter() - begin
                if isinstance(result, tuple) and result:
                    try:
                        counters.native_selected_records_peak = max(
                            counters.native_selected_records_peak, len(result[0]))
                    except TypeError:
                        pass
                return result

            self._patch(source_type, "_read_winners", read_winners)

        wrap_prepare(_native_claims.PinnedNativeClaimSource)
        wrap_prepare(_locator_claims.PinnedNativeLocatorClaimSource)

        from gestaltdb._graph import temporal_selection as selection

        original_iter_claims = selection.iter_claims_as_of

        @functools.wraps(original_iter_claims)
        def iter_claims_as_of(*args: Any, **kwargs: Any) -> Iterator[Any]:
            counters.canonical_conformance_calls += 1
            iterator = iter(original_iter_claims(*args, **kwargs))
            while True:
                begin = time.perf_counter()
                try:
                    value = next(iterator)
                except StopIteration:
                    return
                finally:
                    counters.canonical_conformance_s += time.perf_counter() - begin
                yield value

        self._patch(selection, "iter_claims_as_of", iter_claims_as_of)

    def _uninstall(self) -> None:
        for target, name, original in reversed(self._restore):
            setattr(target, name, original)
        self._restore.clear()

    def snapshot(self) -> dict[str, Any]:
        return self._counters.snapshot()


@contextmanager
def instrument(events: EventHook | None = None) -> Iterator[LocatorInstrumentation]:
    """Activate benchmark attribution for one process scope.

    ``events`` receives coarse phase names before/after construction work so a
    timed-out worker still exposes its last durable phase.
    """
    recorder = LocatorInstrumentation(events=events)
    with recorder._active():
        yield recorder


def stage_delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """Public helper for a stage-level counter delta."""
    return _delta(after, before)
