"""Scope-bound temporal subsystem: bitemporal history, writes and recovery.

``TemporalEngine`` owns every temporal capability of a database handle:
immutable version history and point-in-time selection, guarded commit
publication, temporal index maintenance and orphan recovery, and versioned
entity/claim writes. ``GraphDB`` constructs one engine per handle and keeps
thin compatibility wrappers; reasoning adapters and future services consume
the engine (or its reader/history) instead of the full graph facade.

The engine borrows its store lifetime from the owning handle, like the other
scope-bound collaborators. Transaction and snapshot children rebind through
: meth:`rebound` and :meth:`spawn_transaction_scope`, never retaining the
parent's live store. Guarded publication calls back into the owner's writer
coordination and identity; unguarded use of the raw committer is not the
replacement API.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ContextManager, Iterable, Iterator, Literal, Mapping, Sequence, cast, overload

if TYPE_CHECKING:
    from .kvstores import KVStore
    from .readview import GraphReadView

from .ingestion import IndexMaintenanceMode
from .versioning import ClaimVersion, EdgeVersion, NodeVersion, TemporalCommit, TemporalOrphanReclaimResult, TemporalOrphanReport, TemporalVersion, TemporalVersionError, canonical_json_bytes
from .temporal import TemporalInput, TemporalInstant, TemporalIntervalInput
from ._graph.entities import Edge, GraphEntityDictSerializer, Node
from ._graph.temporal_types import Clock, CommitCache, ExactEntry, Hydrator, IndexState, IndexWriter, LiveStore, PointInput, RangeEntry, RecordCatalog, RetractionIdentity, TemporalReadSource, VersionWrite
from ._graph import temporal_indexes as _temporal_indexes
from ._graph import temporal_orphans as _temporal_orphans
from ._graph import temporal_reads as _temporal_reads
from ._graph import temporal_selection as _temporal_selection
from ._graph import temporal_writes as _temporal_writes
from ._graph import temporal_sequence as _temporal_sequence
from ._graph.temporal_keys import (
    _TEMPORAL_SEQUENCE_FILENAME,
    _TEMPORAL_SEQUENCE_KEY,
    decode_temporal_locator,
    temporal_commit_key,
    temporal_locator,
    temporal_record_key,
    temporal_visible_key,
)
from ._graph import temporal_entity_writes as _temporal_entity_writes
from ._graph.temporal_selection import _UNSET
from .concurrency import DEFAULT_LOCK_MANAGER, LockManager
from .history import TemporalHistory

_STALE_INDEXES_METADATA_KEY = b"schema:indexes:stale"
_TEMPORAL_WRITER_LOCK_FILENAME = ".gestaltdb_temporal_writer.lock"


class TemporalEngine:
    """Own the temporal state and operations bound to one store scope."""

    def __init__(
        self,
        store: KVStore,
        entity_serializer: GraphEntityDictSerializer,
        *,
        clock: Clock,
        store_path: str | Path | None,
        lock_manager: LockManager | None = None,
        write_lock: ContextManager[object] | None = None,
        transaction_bound: bool = False,
        commit_cache: CommitCache | None = None,
        state_key: bytes | None = None,
        stale_key: bytes | None = None,
        sequence_key: bytes | None = None,
        sequence_filename: str | None = None,
        writer_lock_filename: str | None = None,
    ) -> None:
        self._store = store
        self._entity_serializer = entity_serializer
        self._clock = clock
        self._store_path = None if store_path is None else Path(store_path)
        self._lock_manager = DEFAULT_LOCK_MANAGER if lock_manager is None else lock_manager
        self._write_lock = threading.RLock() if write_lock is None else write_lock
        self._transaction_bound = transaction_bound
        self._commit_cache: CommitCache = {} if commit_cache is None else commit_cache
        self._state_key = _temporal_indexes.INDEX_STATE_KEY if state_key is None else state_key
        self._stale_key = _STALE_INDEXES_METADATA_KEY if stale_key is None else stale_key
        self._sequence_key = _TEMPORAL_SEQUENCE_KEY if sequence_key is None else sequence_key
        self._sequence_filename = _TEMPORAL_SEQUENCE_FILENAME if sequence_filename is None else sequence_filename
        self._writer_lock_filename = _TEMPORAL_WRITER_LOCK_FILENAME if writer_lock_filename is None else writer_lock_filename

    # -- scope binding ----------------------------------------------------

    def rebound(self, store: KVStore, *, store_path: str | Path | None = None) -> TemporalEngine:
        """Bind a sibling engine to a new store, sharing cache and coordination."""
        return TemporalEngine(
            store,
            self._entity_serializer,
            clock=self._clock,
            store_path=self._store_path if store_path is None else store_path,
            lock_manager=self._lock_manager,
            write_lock=self._write_lock,
            transaction_bound=self._transaction_bound,
            commit_cache=self._commit_cache,
            state_key=self._state_key,
            stale_key=self._stale_key,
            sequence_key=self._sequence_key,
            sequence_filename=self._sequence_filename,
            writer_lock_filename=self._writer_lock_filename,
        )

    def spawn_transaction_scope(self, store: KVStore) -> TemporalEngine:
        """Bind a transaction child engine with a fresh commit cache."""
        child = self.rebound(store)
        child._commit_cache = {}
        child._transaction_bound = True
        return child

    @property
    def store(self) -> KVStore:
        """The bound backend store; scope-owned, borrowed for this handle's lifetime."""
        return self._store

    @property
    def is_transaction_bound(self) -> bool:
        """Whether this scope runs inside a caller-owned backend transaction."""
        return self._transaction_bound

    def reader(self) -> _temporal_reads.TemporalReader:
        """Return the store-bound historical read capability for this scope."""
        return _temporal_reads.TemporalReader(
            store=self._store,
            entity_serializer=self._entity_serializer,
            commit_cache=self._commit_cache,
            sequence_key=self._sequence_key,
            store_path=self._store_path,
            sequence_filename=self._sequence_filename,
            state_key=self._state_key,
            stale_key=self._stale_key,
        )

    def history(self, *, hydrate: Hydrator | None = None, live_store: object = _UNSET) -> TemporalHistory:
        """Return read-only history bound to this scope's store.

        Hydration defaults to the engine's version seam so scoped
        instrumentation keeps working; pass an explicit callable to bypass it.
        """
        reader = self.reader()
        return TemporalHistory(
            reader,
            hydrate=hydrate or self.get_version_at,
            live_store=(lambda: self._store) if live_store is _UNSET else cast(LiveStore | None, live_store),
        )

    # -- writer coordination ----------------------------------------------

    @contextmanager
    def write_guard(self) -> Iterator[None]:
        """Serialize temporal publication for one database directory."""
        if self._store_path is None:
            with self._write_lock:
                yield
            return
        with self._lock_manager.for_path(self._store_path / self._writer_lock_filename):
            yield

    @contextmanager
    def _transacted_store(self) -> Iterator[KVStore]:
        tx_store = self._store.transaction()
        try:
            yield tx_store
        except Exception:
            tx_store.rollback()
            raise
        else:
            tx_store.commit()

    # -- commit sequence ---------------------------------------------------

    @staticmethod
    def decode_sequence(payload: bytes) -> tuple[int, TemporalInstant]:
        """Decode a sequence payload into ``(commit_id, system_time)``."""
        return _temporal_sequence.decode_temporal_sequence(payload)

    @staticmethod
    def commit_key(commit_id: int) -> bytes:
        """Return the metadata key for a commit descriptor."""
        return temporal_commit_key(commit_id)

    @staticmethod
    def visible_key(commit_id: int) -> bytes:
        """Return the metadata key for a commit visibility marker."""
        return temporal_visible_key(commit_id)

    @staticmethod
    def record_key(commit_id: int, ordinal: int) -> bytes:
        """Return the metadata key for one version record in a commit."""
        return temporal_record_key(commit_id, ordinal)

    @staticmethod
    def locator(commit_id: int, ordinal: int) -> bytes:
        """Return the opaque index locator for a version record."""
        return temporal_locator(commit_id, ordinal)

    @staticmethod
    def decode_locator(locator: bytes) -> tuple[int, int]:
        """Decode an opaque index locator into ``(commit_id, ordinal)``."""
        return decode_temporal_locator(locator)

    def sequence(self) -> tuple[int, TemporalInstant | None]:
        """Return the latest durable ``(commit_id, system_time)`` pair."""
        return _temporal_sequence.read_temporal_sequence(
            self._store, self._sequence_key, self._store_path, self._sequence_filename)

    def reserve_sequence(self) -> tuple[int, TemporalInstant]:
        """Reserve the next commit ID and persist the sequence durably."""
        return _temporal_sequence.reserve_temporal_sequence(
            self._store, self._sequence_key, self._store_path,
            self._sequence_filename, self._clock)

    def next_system_time(self, previous: TemporalInstant | None) -> TemporalInstant:
        """Return a system time strictly after ``previous`` if given."""
        return _temporal_sequence.next_temporal_system_time(self._clock, previous)

    # -- commit reads ------------------------------------------------------

    def get_temporal_commit(self, commit_id: int, *, _validate_supersession: bool = True) -> TemporalCommit | None:
        """Return a validated visible commit with detached mutable payloads."""
        return self.reader().get_commit(commit_id, _validate_supersession=_validate_supersession)

    def get_commit_cached(self, commit_id: int, *, _validate_supersession: bool = True) -> TemporalCommit | None:
        """Return a cached validated commit, rechecking store markers."""
        return self.reader().get_commit_cached(commit_id, _validate_supersession=_validate_supersession)

    def decode_version(self, envelope: bytes) -> TemporalVersion:
        """Decode one version envelope, verifying payload identity."""
        return self.reader().decode_version(envelope)

    def iter_temporal_commits(self, *, through_commit: int | None = None) -> Iterator[TemporalCommit]:
        """Iterate visible commits in commit order, skipping incomplete commits."""
        return self.history().iter_temporal_commits(through_commit=through_commit)

    def get_version(self, version_id: str) -> TemporalVersion | None:
        """Return the latest visible version with an ID, scanning all commits."""
        return self.reader().get_version(version_id)

    def get_version_at(self, locator: bytes) -> TemporalVersion | None:
        """Return the version addressed by an opaque index locator."""
        return self.reader().get_version_at(locator)

    def get_version_through(self, version_id: str, through_commit: int) -> TemporalVersion | None:
        """Return the latest version with an ID up to a commit horizon."""
        return self.reader().get_version_through(version_id, through_commit)

    def get_versions_through_batch(
        self, version_ids: object, through_commit: int
    ) -> dict[str, TemporalVersion | None]:
        """Resolve distinct version IDs once against one captured horizon."""
        return self.reader().get_versions_through_batch(version_ids, through_commit)

    def system_horizon(self, *, system_time: PointInput | None = None, through_commit: int | None = None) -> int:
        """Resolve a system-time/through-commit selection to a commit horizon."""
        return self.reader().system_horizon(system_time=system_time, through_commit=through_commit)

    def get_node_version(self, version_id: str, *, system_time: PointInput | None = None, through_commit: int | None = None) -> NodeVersion | None:
        """Return a visible node version by UUID using the temporal index."""
        return self.history().get_node_version(version_id, system_time=system_time, through_commit=through_commit)

    def get_edge_version(self, version_id: str, *, system_time: PointInput | None = None, through_commit: int | None = None) -> EdgeVersion | None:
        """Return a visible edge version by UUID using the temporal index."""
        return self.history().get_edge_version(version_id, system_time=system_time, through_commit=through_commit)

    def iter_node_versions(self, logical_id: str | None = None, *, system_time: PointInput | None = None, through_commit: int | None = None) -> Iterator[NodeVersion]:
        """Iterate node versions, using the logical-history index when filtered."""
        return self.history().iter_node_versions(logical_id, system_time=system_time, through_commit=through_commit)

    def iter_edge_versions(self, logical_id: str | None = None, *, system_time: PointInput | None = None, through_commit: int | None = None) -> Iterator[EdgeVersion]:
        """Iterate edge versions, using the logical-history index when filtered."""
        return self.history().iter_edge_versions(logical_id, system_time=system_time, through_commit=through_commit)

    # -- point-in-time selection -------------------------------------------

    def interval_locators(self, index_name: str, parts: list[bytes], instant: TemporalInstant) -> tuple[bytes, ...]:
        """Return index locators started but not yet ended at an instant."""
        return _temporal_selection.interval_locators(self.reader(), index_name, parts, instant)

    @overload
    def entity_as_of(self, kind: Literal["node"], logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> NodeVersion | None: ...
    @overload
    def entity_as_of(self, kind: Literal["edge"], logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> EdgeVersion | None: ...
    @overload
    def entity_as_of(self, kind: Literal["claim"], logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> ClaimVersion | None: ...
    @overload
    def entity_as_of(self, kind: str, logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> TemporalVersion | None: ...
    def entity_as_of(self, kind: str, logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> TemporalVersion | None:
        """Resolve one logical node/edge/claim version at a valid time."""
        return self.history(hydrate=hydrate).entity_as_of(
            kind, logical_id, valid_time=valid_time, system_time=system_time, through_commit=through_commit)

    def get_node_as_of(self, logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> NodeVersion | None:
        """Resolve a logical node at valid time and an optional system horizon."""
        return self.history(hydrate=hydrate).get_node_as_of(
            logical_id, valid_time=valid_time, system_time=system_time, through_commit=through_commit)

    def get_edge_as_of(self, logical_id: str, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> EdgeVersion | None:
        """Resolve a logical edge at valid time and an optional system horizon."""
        return self.history(hydrate=hydrate).get_edge_as_of(
            logical_id, valid_time=valid_time, system_time=system_time, through_commit=through_commit)

    def iter_nodes_as_of(self, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None, hydrate: Hydrator | None = None) -> Iterator[NodeVersion]:
        """Iterate node versions visible at one valid and system-time point."""
        return self.history(hydrate=hydrate).iter_nodes_as_of(
            valid_time=valid_time, system_time=system_time, through_commit=through_commit)

    def iter_edges_as_of(self, *, valid_time: PointInput, system_time: PointInput | None = None, through_commit: int | None = None,
                         edge_type: str | None = None, direction: str = "out", source: str | None = None, target: str | None = None, hydrate: Hydrator | None = None) -> Iterator[EdgeVersion]:
        """Iterate endpoint edges visible at valid time and a system horizon."""
        return self.history(hydrate=hydrate).iter_edges_as_of(
            valid_time=valid_time, system_time=system_time, through_commit=through_commit,
            edge_type=edge_type, direction=direction, source=source, target=target)

    # -- commit writes ------------------------------------------------------

    def committer(self) -> _temporal_writes.TemporalCommitter:
        """Bind the temporal write capability to this scope's store."""
        return _temporal_writes.TemporalCommitter(
            store=self._store,
            entity_serializer=self._entity_serializer,
            reader=self.reader(),
            clock=self._clock,
            require_retraction_fn=self.require_retraction_identities,
        )

    def commit_direct(self, writes: Sequence[VersionWrite], metadata: Mapping[str, object], index_mode: str) -> TemporalCommit:
        """Validate, sequence, and publish one commit on this scope's store."""
        return self.committer().commit_direct(writes, metadata, index_mode)

    def commit_versions(self, writes: Iterable[VersionWrite], *, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> TemporalCommit:
        """Append one logical commit containing immutable node/edge versions."""
        writes = tuple(writes)
        if not writes:
            raise TemporalVersionError("temporal commit must contain at least one version")
        mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        allowed_modes = {item.value for item in IndexMaintenanceMode}
        if mode not in allowed_modes:
            raise ValueError(f"index_mode must be one of: {', '.join(sorted(allowed_modes))}")
        rebuild_after = mode == IndexMaintenanceMode.DEFER_REBUILD.value
        write_mode = IndexMaintenanceMode.DEFER.value if rebuild_after else mode
        normalized_metadata = json.loads(canonical_json_bytes(dict(metadata or {})).decode("utf-8"))
        with self.write_guard():
            if getattr(self._store, "supports_transactions", False) and not self._transaction_bound:
                with self._transacted_store() as tx_store:
                    result = self.spawn_transaction_scope(tx_store).commit_direct(
                        writes, normalized_metadata, write_mode)
            else:
                result = self.commit_direct(writes, normalized_metadata, write_mode)
        if rebuild_after:
            self.rebuild_temporal_indexes()
        return result

    def require_retraction_identities(self, identities: Iterable[RetractionIdentity]) -> None:
        """Validate retractions against published history, not deferred indexes."""
        _temporal_writes.require_retraction_identities(self.reader(), identities)

    def version_id_exists(self, version_id: str) -> bool:
        """Return whether a version ID is already visible."""
        return self.committer().version_id_exists(version_id)

    # -- versioned entity and claim writes -----------------------------------

    def put_node_version(self, node: Node, *, valid: TemporalIntervalInput, version_id: str | None = None, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> NodeVersion:
        """Append an immutable node assertion without changing the current graph."""
        return _temporal_entity_writes.put_node_version(
            self.commit_versions, node, valid=valid, version_id=version_id,
            metadata=metadata, index_mode=index_mode)

    def put_edge_version(self, edge: Edge, *, valid: TemporalIntervalInput, version_id: str | None = None, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> EdgeVersion:
        """Append an immutable edge assertion without changing the current graph."""
        return _temporal_entity_writes.put_edge_version(
            self.commit_versions, edge, valid=valid, version_id=version_id,
            metadata=metadata, index_mode=index_mode)

    def correct_node_version(self, node: Node, *, supersedes_version_id: str, valid: TemporalIntervalInput | None = None,
                             version_id: str | None = None, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> NodeVersion:
        """Append a corrected node payload while preserving the superseded version."""
        return _temporal_entity_writes.correct_node_version(
            self.commit_versions, node, supersedes_version_id=supersedes_version_id,
            valid=valid, version_id=version_id, metadata=metadata, index_mode=index_mode)

    def correct_edge_version(self, edge: Edge, *, supersedes_version_id: str, valid: TemporalIntervalInput | None = None,
                             version_id: str | None = None, metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> EdgeVersion:
        """Append a corrected edge payload while preserving the superseded version."""
        return _temporal_entity_writes.correct_edge_version(
            self.commit_versions, edge, supersedes_version_id=supersedes_version_id,
            valid=valid, version_id=version_id, metadata=metadata, index_mode=index_mode)

    def retract_node_version(self, logical_id: str, *, valid: TemporalIntervalInput | None = None, valid_from: TemporalInput | None = None,
                             supersedes_version_id: str | None = None, reason: str | None = None, version_id: str | None = None,
                             metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> NodeVersion:
        """Append a node retraction over an explicit or inherited interval."""
        return _temporal_entity_writes.retract_node_version(
            self.commit_versions, logical_id, valid=valid, valid_from=valid_from,
            supersedes_version_id=supersedes_version_id, reason=reason,
            version_id=version_id, metadata=metadata, index_mode=index_mode)

    def retract_edge_version(self, logical_id: str, *, valid: TemporalIntervalInput | None = None, valid_from: TemporalInput | None = None,
                             supersedes_version_id: str | None = None, reason: str | None = None, version_id: str | None = None,
                             metadata: Mapping[str, object] | None = None, index_mode: str | IndexMaintenanceMode = "maintain") -> EdgeVersion:
        """Append an edge retraction over an explicit or inherited interval."""
        return _temporal_entity_writes.retract_edge_version(
            self.commit_versions, logical_id, valid=valid, valid_from=valid_from,
            supersedes_version_id=supersedes_version_id, reason=reason,
            version_id=version_id, metadata=metadata, index_mode=index_mode)

    def migrate_time_indexed_edges(self, *, delete_legacy: bool = False, metadata: Mapping[str, object] | None = None,
                                   index_mode: str | IndexMaintenanceMode = "maintain", delete_current_edge: Callable[[bytes], None] | None = None) -> tuple[EdgeVersion, ...]:
        """Append legacy ``TimeIndexedEdge`` records as temporal edge history.

        ``delete_current_edge`` is an explicit integration hook supplied by the
        current-graph owner; ordinary temporal operation never depends on it.
        """
        if delete_legacy and delete_current_edge is None:
            raise ValueError("migrate_time_indexed_edges requires an explicit current-edge delete")
        return _temporal_entity_writes.migrate_time_indexed_edges(
            self._store, self._entity_serializer, self.reader(),
            self.commit_versions, delete_current_edge, metadata=metadata,
            index_mode=index_mode, delete_legacy=delete_legacy)

    # -- orphans and recovery -------------------------------------------------

    def orphan_details(self, commit_id: int, descriptor_payload: bytes) -> tuple[RecordCatalog, TemporalInstant, tuple[TemporalVersion, ...]]:
        """Decode an unpublished commit descriptor and its present records."""
        return _temporal_orphans.orphan_details(
            self._store, self.reader().decode_version, commit_id, descriptor_payload)

    def list_orphans_locked(self, through_commit: int | None = None) -> TemporalOrphanReport:
        """Audit allocation gaps and incomplete commits up to a horizon."""
        return _temporal_orphans.list_orphans_locked(self._store, self.reader(), through_commit)

    def list_temporal_orphans(self, *, through_commit: int | None = None) -> TemporalOrphanReport:
        """List durable allocation gaps and incomplete unpublished commits."""
        with self.write_guard():
            return self.list_orphans_locked(through_commit)

    def _reclaim_locked(self, through_commit: int) -> TemporalOrphanReclaimResult:
        return _temporal_orphans.reclaim_orphans_locked(
            self._store, self.reader(), self._stale_key,
            self.rebuild_temporal_indexes, through_commit)

    def reclaim_temporal_orphans(self, *, through_commit: int) -> TemporalOrphanReclaimResult:
        """Delete validated unpublished artifacts through an explicit horizon."""
        with self.write_guard():
            if getattr(self._store, "supports_transactions", False) and not self._transaction_bound:
                with self._transacted_store() as tx_store:
                    return self.spawn_transaction_scope(tx_store)._reclaim_locked(through_commit)
            return self._reclaim_locked(through_commit)

    # -- temporal indexes -----------------------------------------------------

    def load_index_state(self) -> IndexState | None:
        """Load and validate the temporal index state record."""
        return _temporal_indexes.load_index_state(self._store, self._state_key)

    def index_horizon(self) -> int | None:
        """Return the indexed-through commit, or ``None`` when indexes are absent."""
        return _temporal_indexes.index_state_horizon(self._store, self._state_key)

    def index_generation(self) -> int:
        """Return the active index generation (1 when no state is published)."""
        return _temporal_indexes.index_state_generation(self._store, self._state_key)

    @staticmethod
    def index_name(index_name: str, generation: int) -> str:
        """Return the generation-qualified physical index name."""
        return _temporal_indexes.index_name(index_name, generation)

    def visibility_checksum(self, through_commit: int, *, pending_marker: tuple[int, bytes] | None = None) -> str:
        """Digest commit visibility markers over ``1..through_commit``."""
        return _temporal_indexes.visibility_checksum(
            self._store, through_commit, pending_marker=pending_marker)

    def persist_index_state(self, commit_id: int, generation: int, *, pending_marker: tuple[int, bytes] | None = None) -> None:
        """Publish the temporal index state record."""
        _temporal_indexes.persist_index_state(
            self._store, self._state_key, commit_id, generation, pending_marker=pending_marker)

    def ensure_indexes(self, horizon: int) -> None:
        """Fail closed when temporal indexes are stale or checksums diverge."""
        _temporal_indexes.ensure_temporal_indexes(
            self._store, self._state_key, self._stale_key, horizon)

    def has_visible_history(self) -> bool:
        """Return whether any visible commit marker exists."""
        return _temporal_indexes.has_visible_temporal_history(
            self._store, self._sequence_key, self._store_path, self._sequence_filename)

    def index_entries(self, version: TemporalVersion, *, generation: int | None = None) -> tuple[list[ExactEntry], list[RangeEntry]]:
        """Encode the exact and range index entries for one version record."""
        resolved = self.index_generation() if generation is None else generation
        return _temporal_indexes.temporal_index_entries(version, generation=resolved)

    def write_temporal_indexes(self, versions: Sequence[TemporalVersion], commit_id: int, *, generation: int | None = None) -> dict[str, int]:
        """Write temporal exact/range index entries for committed versions."""
        resolved = self.index_generation() if generation is None else generation
        return _temporal_indexes.write_temporal_indexes(
            self._store, versions, commit_id, generation=resolved)

    def rebuild_temporal_indexes(self, *, through_commit: int | None = None, write_indexes_fn: IndexWriter | None = None) -> dict[str, int]:
        """Rebuild all derived temporal indexes from visible canonical history."""
        hook = write_indexes_fn or self._write_temporal_indexes
        with self.write_guard():
            return _temporal_indexes.rebuild_temporal_indexes(
                self._store, self.reader(), self._state_key, self._stale_key,
                write_indexes_fn=hook, through_commit=through_commit)

    def _write_temporal_indexes(self, versions: Sequence[TemporalVersion], commit_id: int, *, generation: int | None = None) -> dict[str, int]:
        resolved = self.index_generation() if generation is None else generation
        return _temporal_indexes.write_temporal_indexes(
            self._store, versions, commit_id, generation=resolved)

    def delete_index_generation(self, generation: int, through_commit: int) -> None:
        """Delete one superseded temporal index generation."""
        _temporal_indexes.delete_temporal_index_generation(
            self._store, self.reader(), generation, through_commit)

    # -- read views ------------------------------------------------------------

    @contextmanager
    def open_read_view(self, graph: TemporalReadSource, *, manifest: Mapping[str, Any], database_id: str, valid_time: PointInput,
                       system_time: PointInput | None = None, through_commit: int | None = None) -> Iterator[GraphReadView]:
        """Yield a temporal read view pinned to one visible commit horizon.

        ``graph`` is the read source handle, ``manifest`` and ``database_id``
        authenticate it, ``valid_time`` selects domain time, and
        ``through_commit``/``system_time`` select a recorded history prefix.

        Examples:
            Inspect an assertion before and after retraction (requires ``lmdb``)::

                >>> from tempfile import TemporaryDirectory
                >>> from gestaltdb.graphdb import GraphDB
                >>> with TemporaryDirectory() as root:
                ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
                ...     try:
                ...         first = graph.epistemic.assert_claim(subject="alice", predicate="member",
                ...             object="team", agent="registry", polarity="positive", valid=(0, 100))
                ...         _ = graph.epistemic.retract_claim(first.logical_id, valid=(0, 100))
                ...         with graph.temporal.open_read_view(graph, manifest=graph.manifest,
                ...                 database_id=graph.database_id, valid_time=50,
                ...                 through_commit=first.commit_id) as old:
                ...             assert len(tuple(old.iter_claims_as_of(predicate="member"))) == 1
                ...         with graph.temporal.open_read_view(graph, manifest=graph.manifest,
                ...                 database_id=graph.database_id, valid_time=50) as current:
                ...             assert not tuple(current.iter_claims_as_of(predicate="member"))
                ...     finally:
                ...         graph.close()

        Keep the context open while its consumers run.
        """
        yield from _temporal_reads.open_temporal_view(
            graph, self.reader(), manifest=manifest, database_id=database_id,
            valid_time=valid_time, system_time=system_time, through_commit=through_commit)
