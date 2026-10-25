
from __future__ import annotations

import pickle
import json
import hashlib
import copy
import os
import random
import shutil
import threading
import base64
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional, Union

if TYPE_CHECKING:
    from .kvstores import KVStore
    from .serializers import Serializer

import datetime
import struct

from .ingestion import ColumnarIngestionMode, EdgeList, IndexMaintenanceMode, NodeList
from .epistemic import Claim, ClaimObjectKind, ClaimPolarity, ClaimStatus, claim_statement_id
from .modal import ACCESSIBILITY_PREDICATES, AccessibilityKind, ModalExpression, ModalOperator, evaluate_modal
from .rules import (
    ClaimExplanation,
    ExplanationEdge,
    ExplanationNode,
    RuleAbsence,
    RuleError,
    RuleEvaluationLimitError,
    RuleJustification,
    RuleRunResult,
    RuleVersion,
    TruthMaintenanceResult,
    absence_node_id,
    normalize_rule_definition,
    stratify_rules,
)
from .sampling import RelationHierarchy, SamplingPattern, as_sampling_pattern
from .serializers import JSONSerializer, temporal_tagged_value
from .temporal import (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalInterval,
    TemporalLocalDateTime,
    TemporalLocalTime,
    TemporalTime,
)
from .versioning import (
    ClaimVersion,
    ClaimVersionWrite,
    EdgeVersion,
    EdgeVersionWrite,
    NodeVersion,
    NodeVersionWrite,
    TemporalCommit,
    TemporalCorruptionError,
    TemporalOrphanArtifact,
    TemporalOrphanReclaimResult,
    TemporalOrphanReport,
    TemporalVersionError,
    VersionOperation,
    canonical_json_bytes,
    commit_descriptor_bytes,
    decode_version_envelope,
    encode_version_envelope,
    immutable_metadata,
    normalize_logical_id,
    normalize_temporal_instant,
    normalize_temporal_interval,
    normalize_version_id,
)
from ._graph import adjacency as _adjacency
from ._graph import index_maintenance as _index_maintenance
from ._graph import edge_records as _edge_records
from ._graph import node_records as _node_records
from ._graph import ingest_edges as _ingest_edges
from ._graph import ingest_nodes as _ingest_nodes
from ._graph import ingest_support as _ingest_support
from ._graph import sampling as _sampling
from ._graph import index_lookup as _index_lookup
from ._graph import index_rebuild as _index_rebuild
from ._graph import temporal_indexes as _temporal_indexes
from ._graph import temporal_orphans as _temporal_orphans
from ._graph.temporal_indexes import (
    CLAIM_CATALOG_INDEX as _TEMPORAL_CLAIM_CATALOG_INDEX,
    CLAIM_DIMENSION_INDEX as _TEMPORAL_CLAIM_DIMENSION_INDEX,
    CLAIM_STATEMENT_INDEX as _TEMPORAL_CLAIM_STATEMENT_INDEX,
    EDGE_IN_CATALOG_INDEX as _TEMPORAL_EDGE_IN_CATALOG_INDEX,
    EDGE_IN_INDEX as _TEMPORAL_EDGE_IN_INDEX,
    EDGE_OUT_CATALOG_INDEX as _TEMPORAL_EDGE_OUT_CATALOG_INDEX,
    EDGE_OUT_INDEX as _TEMPORAL_EDGE_OUT_INDEX,
    INDEX_STATE_KEY as _TEMPORAL_INDEX_STATE_KEY,
    LOGICAL_COMMIT_INDEX as _TEMPORAL_LOGICAL_COMMIT_INDEX,
    LOGICAL_VALID_INDEX as _TEMPORAL_LOGICAL_VALID_INDEX,
    NODE_CATALOG_INDEX as _TEMPORAL_NODE_CATALOG_INDEX,
    OPEN_END as _TEMPORAL_OPEN_END,
    SYSTEM_INDEX as _TEMPORAL_SYSTEM_INDEX,
    VALID_END_INDEX as _TEMPORAL_VALID_END_INDEX,
    VERSION_INDEX as _TEMPORAL_VERSION_INDEX,
)
from ._graph import temporal_keys as _temporal_keys
from ._graph import lifecycle as _lifecycle
from ._graph import stored_rules as _stored_rules
from ._graph import truth_maintenance as _truth_maintenance
from ._graph.stored_rules import RULE_CATALOG_KEY as _RULE_CATALOG_KEY
from ._graph.stored_rules import RULE_ENGINE_AGENT as _RULE_ENGINE_AGENT
from ._graph.lifecycle import MANIFEST_FILENAME, MANIFEST_FORMAT_VERSION
from ._graph.truth_maintenance import TRUTH_MAINTENANCE_STATE_KEY as _TRUTH_MAINTENANCE_STATE_KEY
from ._graph import temporal_entity_writes as _temporal_entity_writes
from ._graph import temporal_reads as _temporal_reads
from ._graph.temporal_reads import _UNSET
from ._graph import temporal_writes as _temporal_writes
from ._graph import temporal_sequence as _temporal_sequence
from ._graph.temporal_keys import (
    _TEMPORAL_COMMIT_PREFIX,
    _TEMPORAL_RECORD_PREFIX,
    _TEMPORAL_SEQUENCE_FILENAME,
    _TEMPORAL_SEQUENCE_KEY,
    _TEMPORAL_VISIBLE_PREFIX,
)
from ._graph.registry import (
    backend_registry as _backend_registry,
    gestaltdb_version as _gestaltdb_version,
    registry_name_for_instance as _registry_name_for_instance,
    serializer_registry as _serializer_registry,
    utc_now_iso as _utc_now_iso,
)
from ._graph.entities import (
    Edge,
    GraphEntityDictSerializer,
    Node,
    TimeIndexedEdge,
    _normalize_labels,
    _property_value_to_index_bytes,
    _property_value_to_range_index_bytes,
    bytes_to_datetime,
    datetime_to_bytes,
    edge_key_to_bytes as _edge_key_to_bytes,
    key_to_string as _key_to_string,
    node_key_to_bytes as _node_key_to_bytes,
)
from ._graph.scopes import configure_snapshot_child, configure_transaction_child



_NODE_PROPERTY_INDEXES_METADATA_KEY = b"schema:indexes:node_properties"
_EDGE_PROPERTY_INDEXES_METADATA_KEY = b"schema:indexes:edge_properties"
_NODE_CONSTRAINTS_METADATA_KEY = b"schema:constraints:nodes"
_STALE_INDEXES_METADATA_KEY = b"schema:indexes:stale"
_MANIFEST_METADATA_KEY = b"schema:manifest"
_DATABASE_ID_METADATA_KEY = b"schema:database_identity:v1"
_VALID_INDEX_MODES = {IndexMaintenanceMode.MAINTAIN.value, IndexMaintenanceMode.DEFER.value}
_INDEX_REBUILD_BATCH_SIZE = 100_000
_TEMPORAL_WRITER_LOCK_FILENAME = ".gestaltdb_temporal_writer.lock"
_DATABASE_ID_LOCK = threading.Lock()
_TEMPORAL_WRITER_LOCKS_LOCK = threading.Lock()
_TEMPORAL_WRITER_LOCKS = {}


class _TemporalWriterLock:
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
                        raise RuntimeError("temporal writer locking is unsupported on this platform")
                except Exception:
                    handle.close()
                    raise
                self._handle = handle
            self._depth += 1
            return self
        except Exception:
            self._lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        self._depth -= 1
        try:
            if self._depth == 0:
                handle = self._handle
                self._handle = None
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif os.name == "nt":  # pragma: no cover - Windows-only
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                handle.close()
        finally:
            self._lock.release()


def _temporal_writer_lock(path: Path) -> _TemporalWriterLock:
    canonical = path.resolve()
    key = str(canonical)
    with _TEMPORAL_WRITER_LOCKS_LOCK:
        lock = _TEMPORAL_WRITER_LOCKS.get(key)
        if lock is None:
            lock = _TemporalWriterLock(canonical / _TEMPORAL_WRITER_LOCK_FILENAME)
            _TEMPORAL_WRITER_LOCKS[key] = lock
        return lock


# Entity/value helpers live in `gestaltdb._graph.entities`; re-exported above.
_SimpleProgress = _ingest_support.SimpleProgress


class GraphDB:
    """Persistent graph records, indexes, queries, and explicit temporal history.

    Choose an installed backend and serializer with :meth:`create`; :meth:`open`
    restores those choices from the database manifest. Always close the handle.
    Ordinary node/edge writes and temporal claim/version writes are separate.

    Examples:
        Create, query, and reopen a disposable database (requires ``lmdb``)::

            >>> from tempfile import TemporaryDirectory
            >>> from pathlib import Path
            >>> from gestaltdb.graphdb import GraphDB, Node
            >>> with TemporaryDirectory() as root:
            ...     path = Path(root) / "graph"
            ...     graph = GraphDB.create(path, backend="lmdb", serializer="json")
            ...     try:
            ...         _ = graph.create_node_property_index("name")
            ...         graph.put_node(Node("alice", labels=["Person"], properties={"name": "Alice"}))
            ...         result = graph.query('MATCH (n:Person {name: "Alice"}) RETURN n.name AS name')
            ...         assert result.records == [{"name": "Alice"}]
            ...     finally:
            ...         graph.close()
            ...     reopened = GraphDB.open(path)
            ...     try:
            ...         assert reopened.get_node(b"alice").properties["name"] == "Alice"
            ...     finally:
            ...         reopened.close()

    See the architecture and temporal-versioning guides for component handoffs
    and historical reads; use ``ingest_arrow`` for a columnar loading example.
    """
    def __init__(
            self, 
            store: KVStore, 
            serializer: Serializer,
            indexed_node_properties: Optional[list[str]] = None,
            indexed_edge_properties: Optional[list[str]] = None,
        ):
        """Initialize a graph database wrapper.

        Args:
            store: ``KVStore`` instance such as ``LMDBStore``, ``LevelDBStore``,
                or ``PyRexStore``.
            serializer: Serializer for node, edge, and adjacency payloads.
            indexed_node_properties: Optional exact-match node property indexes
                to maintain for future writes.
            indexed_edge_properties: Optional exact-match edge property indexes
                to maintain for future writes.

        Examples:
            >>> from gestaltdb.kvstores import LMDBStore
            >>> from gestaltdb.serializers import PickleSerializer
            >>> graph = GraphDB(LMDBStore(path="/tmp/example"), PickleSerializer(), indexed_node_properties=["name"])  # doctest: +SKIP
        """
        self.store = store
        self.serializer = serializer
        store_path = getattr(store, "path", None)
        self._store_path: Path | None = None if store_path is None else Path(store_path)
        self._backend_name: str | None = _registry_name_for_instance(store, _backend_registry())
        self._serializer_name: str | None = _registry_name_for_instance(serializer, _serializer_registry())
        self._manifest: dict | None = None
        self._database_id: str | None = None
        self._temporal_write_lock = threading.RLock()
        self._temporal_transaction_bound = False
        self._temporal_clock = lambda: datetime.datetime.now(datetime.timezone.utc)
        self.entity_serializer = GraphEntityDictSerializer(
            self.serializer
        )
        self._typed_adjacency_count_cache: dict[tuple[str, bytes, str], int] = {}
        self._verified_current_provenance: dict[str, str] = {}
        # Cache private decoded values, scoped to the exact store object.
        # Only pinned read snapshots can skip storage revalidation. Mutable
        # entity payloads are copied at the public read boundary.
        self._temporal_commit_cache: dict = {}
        persisted_node_indexes = self._load_property_index_metadata(_NODE_PROPERTY_INDEXES_METADATA_KEY)
        persisted_edge_indexes = self._load_property_index_metadata(_EDGE_PROPERTY_INDEXES_METADATA_KEY)
        self.indexed_node_properties = set(persisted_node_indexes).union(indexed_node_properties or [])
        self.indexed_edge_properties = set(persisted_edge_indexes).union(indexed_edge_properties or [])
        self.node_constraints = self._load_node_constraint_metadata()
        if indexed_node_properties is not None:
            self._persist_property_index_metadata(_NODE_PROPERTY_INDEXES_METADATA_KEY, self.indexed_node_properties)
        if indexed_edge_properties is not None:
            self._persist_property_index_metadata(_EDGE_PROPERTY_INDEXES_METADATA_KEY, self.indexed_edge_properties)

    @classmethod
    def create(
        cls,
        path,
        *,
        backend="pyrex",
        serializer="json",
        backend_options=None,
        indexed_node_properties=None,
        indexed_edge_properties=None,
        overwrite=False,
    ) -> "GraphDB":
        """Create a self-describing graph store and return an open handle."""
        path = Path(path)
        backend_cls = cls._backend_class(backend)
        serializer_cls = cls._serializer_class(serializer)
        backend_options = dict(backend_options or {})

        backend_cls.ensure_available()
        ensure_serializer = getattr(serializer_cls, "ensure_available", None)
        if ensure_serializer is not None:
            ensure_serializer()

        if path.exists() and any(path.iterdir()):
            if not overwrite:
                raise ValueError(f"store directory is not empty: {path}. Pass overwrite=True to replace it.")
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)

        store = backend_cls(path=str(path), **backend_options)
        graph = cls(
            store,
            serializer_cls(),
            indexed_node_properties=indexed_node_properties,
            indexed_edge_properties=indexed_edge_properties,
        )
        graph._store_path = path
        graph._backend_name = backend
        graph._serializer_name = serializer
        graph.save_manifest(path)
        return graph

    @classmethod
    def open(cls, path, *, backend_options=None, validate_manifest=True) -> "GraphDB":
        """Open a self-describing graph store from a directory."""
        path = Path(path)
        manifest_path = path / MANIFEST_FILENAME
        if not manifest_path.exists():
            raise ValueError(f"missing GestaltDB manifest: {manifest_path}. Migrate an existing DB with graph.save_manifest().")
        manifest = cls._read_manifest(manifest_path)
        backend_name = manifest.get("backend", {}).get("name")
        serializer_name = manifest.get("serializer", {}).get("name")
        backend_cls = cls._backend_class(backend_name)
        serializer_cls = cls._serializer_class(serializer_name)
        backend_cls.ensure_available()
        ensure_serializer = getattr(serializer_cls, "ensure_available", None)
        if ensure_serializer is not None:
            ensure_serializer()
        options = dict(manifest.get("backend", {}).get("options") or {})
        options.update(backend_options or {})

        graph_metadata = manifest.get("graph", {})
        store = backend_cls(path=str(path), **options)
        try:
            graph = cls(
                store,
                serializer_cls(),
            )
            graph.indexed_node_properties.update(graph_metadata.get("indexed_node_properties") or [])
            graph.indexed_edge_properties.update(graph_metadata.get("indexed_edge_properties") or [])
        except Exception:
            store.close()
            raise
        graph._store_path = path
        graph._backend_name = backend_name
        graph._serializer_name = serializer_name
        try:
            graph._initialize_database_id(manifest.get("database_id"))
            graph._manifest = graph._manifest_from_current(created_at=manifest.get("created_at"))
            if validate_manifest:
                graph._validate_backend_manifest(manifest)
            backend_manifest_payload = graph.store.get_metadata(_MANIFEST_METADATA_KEY)
            backend_manifest = json.loads(backend_manifest_payload.decode("utf-8")) if backend_manifest_payload else {}
            if manifest.get("database_id") is None or backend_manifest.get("database_id") is None:
                graph.save_manifest(path)
            return graph
        except Exception:
            graph.close()
            raise

    @property
    def database_id(self) -> str:
        """Return the stable UUID persisted with this database."""
        if self._database_id is not None:
            return self._database_id
        return self._initialize_database_id(None)

    def _initialize_database_id(self, expected: str | None) -> str:
        """Persist or reconcile the database UUID under a local filesystem lock."""
        value = _lifecycle.initialize_database_id(
            self.store, self._store_path, _DATABASE_ID_LOCK,
            _DATABASE_ID_METADATA_KEY, expected,
        )
        self._database_id = value
        return value

    def _initialize_database_id_locked(self, expected: str | None) -> str:
        value = _lifecycle.initialize_database_id_locked(
            self.store, self._store_path, _DATABASE_ID_METADATA_KEY, expected)
        self._database_id = value
        return value

    @property
    def manifest(self) -> dict:
        """Return the loaded or generated manifest for this graph."""
        self._manifest = self._manifest_from_current(
            created_at=(self._manifest or {}).get("created_at") if self._manifest else None
        )
        return dict(self._manifest)

    def save_manifest(self, path=None) -> dict:
        """Write or update the root manifest for this graph."""
        if path is not None:
            self._store_path = Path(path)
        if self._store_path is None:
            raise ValueError("cannot save manifest without a store path; pass graph.save_manifest(path=...)")
        self._store_path.mkdir(parents=True, exist_ok=True)
        self._initialize_database_id((self._manifest or {}).get("database_id"))
        if self._backend_name is None:
            self._backend_name = _registry_name_for_instance(self.store, _backend_registry())
        if self._serializer_name is None:
            self._serializer_name = _registry_name_for_instance(self.serializer, _serializer_registry())
        if self._backend_name is None:
            raise ValueError("cannot infer backend name for manifest; use GraphDB.create/open for managed stores")
        if self._serializer_name is None:
            raise ValueError("cannot infer serializer name for manifest; use an allowlisted serializer")

        manifest = self._manifest_from_current(created_at=(self._manifest or {}).get("created_at") if self._manifest else None)
        with (self._store_path / MANIFEST_FILENAME).open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
        try:
            self.store.put_metadata(_MANIFEST_METADATA_KEY, json.dumps(manifest, sort_keys=True).encode("utf-8"))
        except NotImplementedError:
            pass
        self._manifest = manifest
        return dict(manifest)

    @staticmethod
    def _backend_class(name: str):
        return _lifecycle.backend_class(name)

    @staticmethod
    def _serializer_class(name: str):
        return _lifecycle.serializer_class(name)

    @staticmethod
    def _read_manifest(path: Path) -> dict:
        return _lifecycle.read_manifest_file(path)

    def _manifest_from_current(self, *, created_at: str | None = None) -> dict:
        backend_name = self._backend_name or _registry_name_for_instance(self.store, _backend_registry())
        serializer_name = self._serializer_name or _registry_name_for_instance(self.serializer, _serializer_registry())
        if backend_name is None or serializer_name is None:
            raise ValueError("cannot build manifest for unknown backend or serializer")
        backend_cls = self._backend_class(backend_name)
        serializer_cls = self._serializer_class(serializer_name)
        backend_options = {}
        if backend_name == "pyrex" and getattr(self.store, "transactional", False):
            backend_options["transactional"] = True
        return {
            "format_version": MANIFEST_FORMAT_VERSION,
            "database_id": self.database_id,
            "gestaltdb_version": _gestaltdb_version(),
            "backend": {
                "name": backend_name,
                "class": f"{backend_cls.__module__}.{backend_cls.__name__}",
                "layout_version": 1,
                "options": backend_options,
            },
            "serializer": {
                "name": serializer_name,
                "class": f"{serializer_cls.__module__}.{serializer_cls.__name__}",
                "format_version": 1,
            },
            "graph": {
                "node_count": None,
                "edge_count": None,
                "indexed_node_properties": list(sorted(self.indexed_node_properties)),
                "indexed_edge_properties": list(sorted(self.indexed_edge_properties)),
                "temporal_versions_format": 1,
            },
            "created_at": created_at or _utc_now_iso(),
        }

    def _validate_backend_manifest(self, root_manifest: dict) -> None:
        try:
            payload = self.store.get_metadata(_MANIFEST_METADATA_KEY)
        except NotImplementedError:
            return
        if not payload:
            return
        backend_manifest = json.loads(payload.decode("utf-8"))
        if backend_manifest.get("backend", {}).get("name") != root_manifest.get("backend", {}).get("name"):
            raise ValueError("GestaltDB manifest/backend metadata mismatch for backend name")
        if backend_manifest.get("serializer", {}).get("name") != root_manifest.get("serializer", {}).get("name"):
            raise ValueError("GestaltDB manifest/backend metadata mismatch for serializer name")
        root_database_id = root_manifest.get("database_id")
        backend_database_id = backend_manifest.get("database_id")
        if root_database_id is not None and backend_database_id is not None and root_database_id != backend_database_id:
            raise ValueError("GestaltDB manifest/backend metadata mismatch for database identity")
        actual_database_id = self.database_id
        if root_database_id is not None and root_database_id != actual_database_id:
            raise ValueError("root manifest does not match persisted database identity")
        if backend_database_id is not None and backend_database_id != actual_database_id:
            raise ValueError("backend manifest does not match persisted database identity")

    def _load_stale_indexes(self) -> set[str]:
        """Load index families known to be stale after deferred bulk ingestion."""
        return _index_maintenance.load_stale_indexes(self.store, _STALE_INDEXES_METADATA_KEY)

    def _persist_stale_indexes(self, stale_indexes: set[str]) -> None:
        """Persist stale index metadata."""
        _index_maintenance.persist_stale_indexes(self.store, _STALE_INDEXES_METADATA_KEY, stale_indexes)

    def _mark_indexes_stale(self, *index_names: str) -> None:
        """Mark index families stale after a deferred bulk load."""
        _index_maintenance.mark_indexes_stale(self.store, _STALE_INDEXES_METADATA_KEY, *index_names)

    def _clear_stale_indexes(self, *index_names: str) -> None:
        """Clear stale markers after rebuilding index families."""
        _index_maintenance.clear_stale_indexes(self.store, _STALE_INDEXES_METADATA_KEY, *index_names)

    def stale_indexes(self) -> tuple[str, ...]:
        """Return index families requiring rebuild after deferred ingestion."""
        stale = self._load_stale_indexes()
        if self._temporal_index_state() is None and self._has_visible_temporal_history():
            stale.add("temporal")
        return tuple(sorted(stale))

    def _ensure_indexes_current(self, *index_names: str) -> None:
        """Prevent stale secondary indexes from silently returning wrong results."""
        _index_maintenance.ensure_indexes_current(self.store, _STALE_INDEXES_METADATA_KEY, *index_names)

    def _validate_index_mode(self, index_mode: str) -> None:
        """Validate the columnar ingestion index mode."""
        _ingest_support.validate_index_mode(index_mode)

    def _low_level_index_mode(self, index_mode: str | IndexMaintenanceMode) -> str:
        """Return the low-level index mode used by primitive ingestion calls."""
        return _ingest_support.low_level_index_mode(index_mode)

    def _coerce_columnar_ingestion_mode(self, ingestion_mode: str | ColumnarIngestionMode) -> str:
        """Normalize a high-level columnar ingestion mode."""
        return _ingest_support.coerce_columnar_ingestion_mode(ingestion_mode)

    def _should_rebuild_after_ingest(self, index_mode: str | IndexMaintenanceMode) -> bool:
        """Return whether high-level ingestion should rebuild deferred indexes."""
        return _ingest_support.should_rebuild_after_ingest(index_mode)

    @contextmanager
    def _ingestion_progress(self, enabled: bool, *, total=None, desc: str):
        """Return an optional progress reporter for chunked ingestion."""
        with _ingest_support.ingestion_progress(enabled, total=total, desc=desc) as progress:
            yield progress

    def _load_property_index_metadata(self, key: bytes) -> set[str]:
        """Load persisted property index definitions from backend metadata."""
        return _index_maintenance.load_property_index_metadata(self.store, key)

    def _persist_property_index_metadata(self, key: bytes, property_names: set[str]) -> None:
        """Persist property index definitions to backend metadata."""
        _index_maintenance.persist_property_index_metadata(self.store, key, property_names)

    def _load_node_constraint_metadata(self) -> list[dict[str, str]]:
        """Load persisted node constraint definitions from backend metadata."""
        return _index_maintenance.load_node_constraint_metadata(self.store, _NODE_CONSTRAINTS_METADATA_KEY)

    def set_node_constraints(self, constraints: list[dict[str, str]]) -> None:
        """Replace and persist the node constraint catalog."""
        payload = json.dumps(
            {"version": 1, "constraints": constraints},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.store.put_metadata(_NODE_CONSTRAINTS_METADATA_KEY, payload)
        self.node_constraints = [dict(constraint) for constraint in constraints]

    # -----------
    # Node Methods
    # -----------
    def put_node(self, node: Node):
        """Store a node.

        Args:
            node: Node to serialize and write.

        Examples:
            >>> graph_db.put_node(Node(node_id="drug-1"))  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.put_node(node)
        _node_records.put_node(self.store, self.entity_serializer, self.indexed_node_properties, node)

    def get_node(self, node_id) -> Node:
        """Return a node by byte key.

        Args:
            node_id: Node ID bytes as stored in the backend.

        Returns:
            The decoded node, or ``None`` when absent.

        Examples:
            >>> graph_db.get_node(b"drug-1")  # doctest: +SKIP
        """
        return _node_records.get_node(self.store, self.entity_serializer, node_id)

    def delete_node(self, node_id):
        """Delete a node by byte key.

        Args:
            node_id: Node ID bytes.

        Examples:
            >>> graph_db.delete_node(b"drug-1")  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.delete_node(node_id)
        _node_records.delete_node(self.store, self.entity_serializer, self.serializer, self.indexed_node_properties, self.indexed_edge_properties, self._typed_adjacency_count_cache, node_id)

    def _put_node_indexes(self, node: Node):
        """Maintain label and configured property indexes for a node."""
        _index_maintenance.put_node_indexes(self.store, self.indexed_node_properties, node)

    def _delete_node_indexes(self, node: Node):
        """Remove label and configured property indexes for a node."""
        _index_maintenance.delete_node_indexes(self.store, self.indexed_node_properties, node)

    def _flush_index_rebuild_batches(self, entries: list, range_entries: list) -> None:
        """Write accumulated rebuild entries and clear the buffers."""
        _index_maintenance.flush_index_rebuild_batches(self.store, entries, range_entries)

    def rebuild_node_indexes(
        self,
        *,
        labels: bool = True,
        properties: list[str] | tuple[str, ...] | set[str] | None = None,
        batch_size: int = _INDEX_REBUILD_BATCH_SIZE,
    ) -> dict[str, int]:
        """Rebuild requested node secondary indexes in one node scan."""
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.rebuild_node_indexes(
                    labels=labels, properties=properties, batch_size=batch_size
                )
        return _index_rebuild.rebuild_node_indexes(
            self.store, self.entity_serializer, self.indexed_node_properties,
            _STALE_INDEXES_METADATA_KEY, labels=labels, properties=properties,
            batch_size=batch_size,
        )

    def create_node_property_index(self, property_name: str):
        """Register and rebuild an exact-match node property index.

        Args:
            property_name: Node property to index for exact-match lookup.

        Returns:
            Number of existing nodes added to the index.

        Examples:
            >>> graph_db.create_node_property_index("kind")  # doctest: +SKIP
            10
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                rebuilt = tx.create_node_property_index(property_name)
            self.indexed_node_properties.add(property_name)
            return rebuilt
        self.indexed_node_properties.add(property_name)
        rebuilt = self.rebuild_node_property_index(property_name)
        self._persist_property_index_metadata(_NODE_PROPERTY_INDEXES_METADATA_KEY, self.indexed_node_properties)
        return rebuilt

    def rebuild_node_property_index(self, property_name: str):
        """Rebuild an exact-match node property index from stored nodes.

        Args:
            property_name: Node property to index.

        Returns:
            Number of indexed node records.

        Examples:
            >>> graph_db.rebuild_node_property_index("name")  # doctest: +SKIP
            3
        """
        rebuilt = self.rebuild_node_indexes(labels=False, properties={property_name})
        return rebuilt[f"node_property:{property_name}"]

    def rebuild_label_index(self):
        """Rebuild the node label index from stored nodes.

        Returns:
            Number of label index entries written.

        Examples:
            >>> graph_db.rebuild_label_index()  # doctest: +SKIP
            12
        """
        rebuilt = self.rebuild_node_indexes(labels=True, properties=set())
        return rebuilt["node_label"]

    def iter_node_ids_by_label(self, label: str):
        """Yield node IDs from the label index.

        Args:
            label: Node label to scan.

        Yields:
            Node ID bytes with the requested label.

        Examples:
            >>> list(graph_db.iter_node_ids_by_label("Drug"))  # doctest: +SKIP
            [b'drug-1']
        """
        return _index_lookup.iter_node_ids_by_label(self.store, self._ensure_indexes_current, label)

    def nodes_by_label(self, label: str):
        """Return nodes with a label using the label index.

        Args:
            label: Node label to scan.

        Returns:
            List of decoded ``Node`` objects.

        Examples:
            >>> graph_db.nodes_by_label("Drug")  # doctest: +SKIP
        """
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_label(label)]

    def count_nodes_by_label(self, label: str) -> int:
        """Return the number of nodes currently indexed for a label."""
        return sum(1 for _ in self.iter_node_ids_by_label(label))

    def iter_node_ids_by_property(self, property_name: str, value):
        """Yield node IDs from an exact-match property index.

        Args:
            property_name: Indexed node property name.
            value: Exact property value to match.

        Yields:
            Node ID bytes matching the property value.

        Examples:
            >>> list(graph_db.iter_node_ids_by_property("kind", "drug"))  # doctest: +SKIP
            [b'drug-1']
        """
        return _index_lookup.iter_node_ids_by_property(self.store, self._ensure_indexes_current, property_name, value)

    def nodes_by_property(self, property_name: str, value):
        """Return nodes using an exact-match property index.

        Args:
            property_name: Indexed node property name.
            value: Exact property value to match.

        Returns:
            List of decoded ``Node`` objects.

        Examples:
            >>> graph_db.nodes_by_property("kind", "drug")  # doctest: +SKIP
        """
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_property(property_name, value)]

    def count_nodes_by_property(self, property_name: str, value) -> int:
        """Return the number of nodes currently indexed for an exact property value."""
        return sum(1 for _ in self.iter_node_ids_by_property(property_name, value))

    def iter_node_ids_by_label_property(self, label: str, property_name: str, value):
        """Yield node IDs from the composite label/property exact-match index."""
        return _index_lookup.iter_node_ids_by_label_property(self.store, self._ensure_indexes_current, label, property_name, value)

    def nodes_by_label_property(self, label: str, property_name: str, value):
        """Return nodes using the composite label/property exact-match index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_label_property(label, property_name, value)]

    def count_nodes_by_label_property(self, label: str, property_name: str, value) -> int:
        """Return the number of nodes indexed for a label and exact property value."""
        return sum(1 for _ in self.iter_node_ids_by_label_property(label, property_name, value))

    def iter_node_ids_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield node IDs from a scalar property range index."""
        return _index_lookup.iter_node_ids_by_property_range(self.store, self._ensure_indexes_current, property_name, start_value, end_value, include_start, include_end)

    def nodes_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return nodes using a scalar property range index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)]

    def count_nodes_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of nodes indexed in a scalar property range."""
        return sum(1 for _ in self.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end))

    def iter_node_ids_by_label_property_range(self, label: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield node IDs from a composite label/property range index."""
        return _index_lookup.iter_node_ids_by_label_property_range(self.store, self._ensure_indexes_current, label, property_name, start_value, end_value, include_start, include_end)

    def nodes_by_label_property_range(self, label: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return nodes using a composite label/property range index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_label_property_range(label, property_name, start_value, end_value, include_start, include_end)]

    def count_nodes_by_label_property_range(self, label: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of nodes indexed for a label/property range."""
        return sum(1 for _ in self.iter_node_ids_by_label_property_range(label, property_name, start_value, end_value, include_start, include_end))

    def node_key_to_bytes(self, node_key):
        """Normalize a node key to bytes.

        Args:
            node_key: String or bytes node key.

        Returns:
            UTF-8 encoded bytes.

        Examples:
            >>> GraphDB.node_key_to_bytes(None, "drug-1")
            b'drug-1'
        """
        return _node_key_to_bytes(node_key)

    def edge_key_to_bytes(self, edge_key):
        """Normalize an edge key to bytes.

        Args:
            edge_key: String or bytes edge key.

        Returns:
            UTF-8 encoded bytes.

        Examples:
            >>> GraphDB.edge_key_to_bytes(None, "d1-p1")
            b'd1-p1'
        """
        return _edge_key_to_bytes(edge_key)

    def key_to_string(self, key):
        """Normalize a key to a string.

        Args:
            key: String or UTF-8 bytes key.

        Returns:
            String key.

        Examples:
            >>> GraphDB.key_to_string(None, b"drug-1")
            'drug-1'
        """
        return _key_to_string(key)

    def edge_type(self, edge: Edge):
        """Return the type used by typed traversal for an edge.

        Args:
            edge: Edge to inspect.

        Returns:
            Edge type string, or ``None``.

        Examples:
            >>> GraphDB.edge_type(None, Edge(properties={"type": "drug-to-protein"}))
            'drug-to-protein'
        """
        return edge.get_type

    # -----------
    # Edge Methods
    # -----------
    def put_edge(self, edge: Edge, update_adjacency = True):
        """Store an edge and update adjacency indexes.

        Args:
            edge: Edge to serialize and write.
            update_adjacency: Whether to update the legacy untyped adjacency list.

        Examples:
            >>> graph_db.put_edge(Edge(source="drug-1", target="protein-1"))  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.put_edge(edge, update_adjacency=update_adjacency)
        _edge_records.put_edge(self.store, self.entity_serializer, self.serializer, self.indexed_edge_properties, self._typed_adjacency_count_cache, edge, update_adjacency=update_adjacency)

    def _put_typed_adjacency_for_edge(self, edge: Edge):
        """Write typed adjacency index records for an edge.

        Args:
            edge: Edge whose ``properties["type"]`` determines the typed index.
        """
        _adjacency.put_typed_adjacency_for_edge(self.store, self._typed_adjacency_count_cache, edge)

    def _delete_typed_adjacency_for_edge(self, edge: Edge):
        """Remove typed adjacency index records for an edge.

        Args:
            edge: Edge whose current source, target, type, and ID identify records.
        """
        _adjacency.delete_typed_adjacency_for_edge(self.store, self._typed_adjacency_count_cache, edge)

    def _update_typed_adjacency_count_cache(self, records, delta: int) -> None:
        """Update cached typed adjacency counts for writes made by this graph handle."""
        _adjacency.update_count_cache(self._typed_adjacency_count_cache, records, delta)

    def _put_edge_indexes(self, edge: Edge):
        """Maintain relationship type and configured property indexes."""
        _index_maintenance.put_edge_indexes(self.store, self.indexed_edge_properties, edge)

    def _delete_edge_indexes(self, edge: Edge):
        """Remove relationship type and configured property indexes."""
        _index_maintenance.delete_edge_indexes(self.store, self.indexed_edge_properties, edge)

    def rebuild_edge_indexes(
        self,
        *,
        edge_types: bool = True,
        properties: list[str] | tuple[str, ...] | set[str] | None = None,
        batch_size: int = _INDEX_REBUILD_BATCH_SIZE,
    ) -> dict[str, int]:
        """Rebuild requested edge secondary indexes in one edge scan."""
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.rebuild_edge_indexes(
                    edge_types=edge_types, properties=properties, batch_size=batch_size
                )
        return _index_rebuild.rebuild_edge_indexes(
            self.store, self.entity_serializer, self.indexed_edge_properties,
            _STALE_INDEXES_METADATA_KEY, edge_types=edge_types, properties=properties,
            batch_size=batch_size,
        )

    def rebuild_relationship_type_index(self):
        """Rebuild the relationship type catalog from stored edges.

        Returns:
            Number of typed edge records indexed.

        Examples:
            >>> graph_db.rebuild_relationship_type_index()  # doctest: +SKIP
            20
        """
        rebuilt = self.rebuild_edge_indexes(edge_types=True, properties=set())
        return rebuilt["edge_type"]

    def create_edge_property_index(self, property_name: str):
        """Register and rebuild an exact-match edge property index.

        Args:
            property_name: Edge property to index for exact-match lookup.

        Returns:
            Number of existing edges added to the index.

        Examples:
            >>> graph_db.create_edge_property_index("score")  # doctest: +SKIP
            7
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                rebuilt = tx.create_edge_property_index(property_name)
            self.indexed_edge_properties.add(property_name)
            return rebuilt
        self.indexed_edge_properties.add(property_name)
        rebuilt = self.rebuild_edge_property_index(property_name)
        self._persist_property_index_metadata(_EDGE_PROPERTY_INDEXES_METADATA_KEY, self.indexed_edge_properties)
        return rebuilt

    def rebuild_edge_property_index(self, property_name: str):
        """Rebuild an exact-match edge property index from stored edges.

        Args:
            property_name: Edge property to index.

        Returns:
            Number of indexed edge records.

        Examples:
            >>> graph_db.rebuild_edge_property_index("score")  # doctest: +SKIP
            7
        """
        rebuilt = self.rebuild_edge_indexes(edge_types=False, properties={property_name})
        return rebuilt[f"edge_property:{property_name}"]

    def iter_edge_ids_by_type(self, edge_type: str):
        """Yield edge IDs from the relationship type catalog.

        Args:
            edge_type: Relationship type stored in ``edge.properties["type"]``.

        Yields:
            Edge ID bytes with the requested relationship type.

        Examples:
            >>> list(graph_db.iter_edge_ids_by_type("drug-to-protein"))  # doctest: +SKIP
            [b'd1-p1']
        """
        return _index_lookup.iter_edge_ids_by_type(self.store, self._ensure_indexes_current, edge_type)

    def iter_edge_ids(self, num_edges=None, key_offset=None):
        """Yield all canonical edge IDs from the backing store.

        Args:
            num_edges: Optional maximum number of edge IDs to yield.
            key_offset: Optional starting key.

        Yields:
            Edge ID bytes in backend key order.

        Examples:
            >>> list(graph_db.iter_edge_ids())  # doctest: +SKIP
            [b'd1-p1']
        """
        return _index_lookup.iter_edge_ids(self.store, num_edges, key_offset)

    def edges_by_type(self, edge_type: str):
        """Return edges using the relationship type catalog.

        Args:
            edge_type: Relationship type stored in ``edge.properties["type"]``.

        Returns:
            List of decoded ``Edge`` objects.

        Examples:
            >>> graph_db.edges_by_type("drug-to-protein")  # doctest: +SKIP
        """
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_type(edge_type)]

    def count_edges_by_type(self, edge_type: str) -> int:
        """Return the number of edges currently indexed for a relationship type."""
        return sum(1 for _ in self.iter_edge_ids_by_type(edge_type))

    def iter_edge_ids_by_property(self, property_name: str, value):
        """Yield edge IDs from an exact-match edge property index.

        Args:
            property_name: Indexed edge property name.
            value: Exact property value to match.

        Yields:
            Edge ID bytes matching the property value.

        Examples:
            >>> list(graph_db.iter_edge_ids_by_property("score", 1))  # doctest: +SKIP
            [b'e1']
        """
        return _index_lookup.iter_edge_ids_by_property(self.store, self._ensure_indexes_current, property_name, value)

    def edges_by_property(self, property_name: str, value):
        """Return edges using an exact-match property index.

        Args:
            property_name: Indexed edge property name.
            value: Exact property value to match.

        Returns:
            List of decoded ``Edge`` objects.

        Examples:
            >>> graph_db.edges_by_property("score", 1)  # doctest: +SKIP
        """
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_property(property_name, value)]

    def count_edges_by_property(self, property_name: str, value) -> int:
        """Return the number of edges currently indexed for an exact property value."""
        return sum(1 for _ in self.iter_edge_ids_by_property(property_name, value))

    def iter_edge_ids_by_type_property(self, edge_type: str, property_name: str, value):
        """Yield edge IDs from the composite type/property exact-match index."""
        return _index_lookup.iter_edge_ids_by_type_property(self.store, self._ensure_indexes_current, edge_type, property_name, value)

    def edges_by_type_property(self, edge_type: str, property_name: str, value):
        """Return edges using the composite type/property exact-match index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_type_property(edge_type, property_name, value)]

    def count_edges_by_type_property(self, edge_type: str, property_name: str, value) -> int:
        """Return the number of edges indexed for a type and exact property value."""
        return sum(1 for _ in self.iter_edge_ids_by_type_property(edge_type, property_name, value))

    def iter_edge_ids_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield edge IDs from a scalar property range index."""
        return _index_lookup.iter_edge_ids_by_property_range(self.store, self._ensure_indexes_current, property_name, start_value, end_value, include_start, include_end)

    def edges_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return edges using a scalar property range index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)]

    def count_edges_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of edges indexed in a scalar property range."""
        return sum(1 for _ in self.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end))

    def iter_edge_ids_by_type_property_range(self, edge_type: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield edge IDs from a composite type/property range index."""
        return _index_lookup.iter_edge_ids_by_type_property_range(self.store, self._ensure_indexes_current, edge_type, property_name, start_value, end_value, include_start, include_end)

    def edges_by_type_property_range(self, edge_type: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return edges using a composite type/property range index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_type_property_range(edge_type, property_name, start_value, end_value, include_start, include_end)]

    def count_edges_by_type_property_range(self, edge_type: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of edges indexed for a type/property range."""
        return sum(1 for _ in self.iter_edge_ids_by_type_property_range(edge_type, property_name, start_value, end_value, include_start, include_end))

    def index_statistics(self) -> dict[str, object]:
        """Return persisted index definitions visible to the query planner."""
        return {
            "indexed_node_properties": tuple(sorted(self.indexed_node_properties)),
            "indexed_edge_properties": tuple(sorted(self.indexed_edge_properties)),
        }

    def rebuild_deferred_indexes(self) -> dict[str, int]:
        """Rebuild secondary indexes marked stale by deferred bulk ingestion.

        Returns:
            Mapping from rebuilt index family/property to entries written.
        """
        stale = set(self.stale_indexes())
        rebuilt: dict[str, int] = {}
        if stale.intersection({"node_label", "node_property"}):
            rebuilt.update(
                self.rebuild_node_indexes(
                    labels="node_label" in stale,
                    properties=self.indexed_node_properties if "node_property" in stale else set(),
                )
            )
        if stale.intersection({"edge_type", "edge_property"}):
            rebuilt.update(
                self.rebuild_edge_indexes(
                    edge_types="edge_type" in stale,
                    properties=self.indexed_edge_properties if "edge_property" in stale else set(),
                )
            )
        if "temporal" in stale:
            rebuilt.update(self.rebuild_temporal_indexes())
        return rebuilt

    def build_sampler_snapshot(
        self,
        output_path,
        *,
        temporal: bool = False,
        system_time=None,
        time_bucket: str | None = None,
        source_db_reference: bool = True,
        source_db_path=None,
        source_db_path_mode: str = "relative",
        read_snapshot: bool | None = None,
        **kwargs,
    ):
        """Build a read-optimized array sampler snapshot from this graph.

        Args:
            output_path: Directory where snapshot arrays and metadata are written.
            temporal: Build temporal-history arrays from one pinned read view.
            system_time: Optional system-time horizon for a temporal build.
            time_bucket: Temporal candidate-index bucket policy: ``None``,
                ``"none"``, ``"hour"``, or ``"day"``.
            read_snapshot: Require or disable a verifiable mutable-state backend
                snapshot. The default uses one automatically when supported.
            **kwargs: Options forwarded to ``SamplerSnapshot.build``.

        Returns:
            A loaded ``SamplerSnapshot`` instance.
        """
        from .sampling import SamplerSnapshot

        if source_db_reference and "source_db" not in kwargs:
            resolved_source_path = Path(source_db_path) if source_db_path is not None else self._store_path
            if resolved_source_path is not None and (resolved_source_path / MANIFEST_FILENAME).exists():
                if source_db_path_mode not in {"relative", "absolute", "relative_to_snapshot", "relative_to_project"}:
                    raise ValueError("source_db_path_mode must be 'relative', 'absolute', 'relative_to_snapshot', or 'relative_to_project'")
                path_type = "relative_to_snapshot" if source_db_path_mode == "relative" else source_db_path_mode
                kwargs["source_db"] = {
                    "path": str(resolved_source_path),
                    "path_type": path_type,
                    "backend": self._backend_name,
                    "serializer": self._serializer_name,
                }
        if temporal:
            with self.read_view(valid_time=0, system_time=system_time) as view:
                return view.build_sampler_snapshot(
                    output_path, temporal=True, time_bucket=time_bucket,
                    **kwargs,
                )
        if system_time is not None or time_bucket is not None:
            raise ValueError("system_time and time_bucket require temporal=True")
        use_snapshot = (
            bool(getattr(self.store, "supports_read_snapshots", False))
            if read_snapshot is None
            else read_snapshot
        )
        if use_snapshot:
            if "source_provenance" in kwargs:
                raise ValueError("source_provenance is captured by read_snapshot")
            stack = ExitStack()
            try:
                view = stack.enter_context(self.current_read_view(require_verifiable=True))
            except NotImplementedError:
                stack.close()
                if read_snapshot is True:
                    raise
            else:
                with stack:
                    kwargs["source_provenance"] = view.provenance.to_dict()
                    return SamplerSnapshot.build(view, output_path, **kwargs)
        return SamplerSnapshot.build(self, output_path, **kwargs)

    @contextmanager
    def current_read_view(
        self,
        *,
        require_verifiable: bool = False,
        capture_provenance: bool = True,
    ):
        """Yield current graph reads pinned to one backend snapshot."""
        from .readview import CurrentGraphReadView, CurrentReadProvenance

        database_id = self.database_id
        manifest = self.manifest
        with self.store.read_snapshot() as snapshot_store:
            identity = snapshot_store.snapshot_identity
            provenance = None
            if capture_provenance and identity.verifiable and identity.sequence is not None:
                provenance = CurrentReadProvenance(
                    database_id=database_id,
                    snapshot_sequence=identity.sequence,
                    backend_name=manifest["backend"]["name"],
                    backend_layout_version=manifest["backend"]["layout_version"],
                    serializer_name=manifest["serializer"]["name"],
                    serializer_format_version=manifest["serializer"]["format_version"],
                    backend_snapshot_kind=identity.kind,
                    state_sha256=snapshot_store.state_sha256(),
                )
            if require_verifiable and provenance is None:
                raise NotImplementedError(
                    "the configured backend can pin reads but cannot expose a verifiable snapshot identity"
                )
            snapshot_graph = copy.copy(self)
            snapshot_graph.store = snapshot_store
            configure_snapshot_child(self, snapshot_graph)
            yield CurrentGraphReadView(snapshot_graph, provenance)

    @contextmanager
    def read_view(self, *, valid_time, system_time=None, through_commit=None):
        """Yield a temporal read view pinned to one visible commit horizon.

        ``valid_time`` selects domain time; ``through_commit`` selects a recorded
        history prefix. Omit the latter for the latest visible prefix, or use
        ``system_time`` for selection by recorded time instead.

        Examples:
            Inspect an assertion before and after retraction (requires ``lmdb``)::

                >>> from tempfile import TemporaryDirectory
                >>> from gestaltdb.graphdb import GraphDB
                >>> with TemporaryDirectory() as root:
                ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
                ...     try:
                ...         first = graph.assert_claim(subject="alice", predicate="member", object="team",
                ...             agent="registry", polarity="positive", valid=(0, 100))
                ...         _ = graph.retract_claim(first.logical_id, valid=(0, 100))
                ...         with graph.read_view(valid_time=50, through_commit=first.commit_id) as old:
                ...             assert len(tuple(old.iter_claims_as_of(predicate="member"))) == 1
                ...         with graph.read_view(valid_time=50) as current:
                ...             assert not tuple(current.iter_claims_as_of(predicate="member"))
                ...     finally:
                ...         graph.close()

        Keep the context open while its consumers run. For mutable node/edge
        snapshots use ``current_read_view``; the temporal-versioning guide
        explains valid time, system time, and historical Cypher qualifiers.
        """
        from .readview import GraphReadView, ReadViewProvenance

        instant = normalize_temporal_instant(valid_time)
        if system_time is not None and through_commit is not None:
            raise ValueError("provide either system_time or through_commit, not both")
        allocated, _ = self._temporal_sequence()
        latest = 0
        markers = []
        for commit_id in range(1, allocated + 1):
            commit_candidate = self.get_temporal_commit(commit_id, _validate_supersession=False)
            if commit_candidate is None:
                if self.store.get_metadata(self._temporal_commit_key(commit_id)) is None:
                    continue
                break
            marker_candidate = self.store.get_metadata(self._temporal_visible_key(commit_id))
            markers.append(commit_id.to_bytes(8, "big") + marker_candidate)
            latest = commit_id
        if system_time is not None:
            through_commit = min(
                latest, self._temporal_system_horizon(system_time=system_time)
            )
        if through_commit is None:
            horizon = latest
        else:
            if isinstance(through_commit, bool) or not isinstance(through_commit, int) or through_commit < 0:
                raise ValueError("through_commit must be a non-negative integer")
            if through_commit > latest:
                raise ValueError("through_commit is newer than the latest visible commit")
            horizon = through_commit
        commit = self.get_temporal_commit(horizon) if horizon else None
        if horizon and commit is None:
            raise ValueError("through_commit does not identify a visible commit")
        marker = self.store.get_metadata(self._temporal_visible_key(horizon)) if horizon else None
        visibility_digest = hashlib.sha256(b"".join(
            value for value in markers if int.from_bytes(value[:8], "big") <= horizon
        )).hexdigest()
        manifest = self.manifest
        provenance = ReadViewProvenance(
            database_id=self.database_id,
            commit_horizon=horizon,
            commit_system_time_us=None if commit is None else commit.system_time.epoch_microseconds,
            commit_marker_sha256=None if marker is None else marker.hex(),
            visibility_sha256=visibility_digest,
            valid_time_us=instant.epoch_microseconds,
            backend_name=manifest["backend"]["name"],
            backend_layout_version=manifest["backend"]["layout_version"],
            serializer_name=manifest["serializer"]["name"],
            serializer_format_version=manifest["serializer"]["format_version"],
        )
        yield GraphReadView(self, provenance)

    def get_typed_adjacency(self, node_id, edge_type: str, direction: str = 'out'):
        """Return typed adjacency records with clean direction semantics.

        `out` means source -> target, `in` means target -> source, and `any`
        returns the union of both directions.
        """
        return _adjacency.get_typed_adjacency(
            self.store, _node_key_to_bytes(node_id), edge_type, direction)

    def count_typed_adjacency(self, node_id, edge_type: str, direction: str = 'out') -> int:
        """Count typed adjacency records without materializing traversal records.

        Args:
            node_id: Node ID as string or bytes.
            edge_type: Edge type to traverse.
            direction: ``"out"``, ``"in"``, or ``"any"``.

        Returns:
            Number of typed adjacency records matching the node, edge type, and
            direction.
        """
        return _adjacency.count_typed_adjacency(
            self.store, self._typed_adjacency_count_cache,
            _node_key_to_bytes(node_id), edge_type, direction)

    def iter_typed_adjacency(self, node_id, edge_type: str, direction: str = 'out'):
        """Yield typed adjacency records with clean direction semantics.

        Args:
            node_id: Node ID as string or bytes.
            edge_type: Edge type to traverse.
            direction: ``"out"``, ``"in"``, or ``"any"``.

        Yields:
            Typed adjacency records containing edge, neighbor, source, target,
            edge type, and concrete direction fields.

        Examples:
            >>> graph_db.iter_typed_adjacency("drug-1", "drug-to-protein")  # doctest: +SKIP
        """
        return _adjacency.iter_typed_adjacency(
            self.store, _node_key_to_bytes(node_id), edge_type, direction)

    def neighbors_by_edge_type(self, node_id, edge_type: str, direction: str = 'out'):
        """Return neighbor IDs connected by a specific edge type.

        Args:
            node_id: Node ID as string or bytes.
            edge_type: Edge type to traverse.
            direction: ``"out"``, ``"in"``, or ``"any"``.

        Returns:
            List of neighbor ID bytes.

        Examples:
            >>> graph_db.neighbors_by_edge_type("drug-1", "drug-to-protein")  # doctest: +SKIP
        """
        return _adjacency.neighbors_by_edge_type(
            self.store, _node_key_to_bytes(node_id), edge_type, direction)

    def edges_by_edge_type(self, node_id, edge_type: str, direction: str = 'out'):
        """Return edge IDs connected by a specific edge type.

        Args:
            node_id: Node ID as string or bytes.
            edge_type: Edge type to traverse.
            direction: ``"out"``, ``"in"``, or ``"any"``.

        Returns:
            List of edge ID bytes.

        Examples:
            >>> graph_db.edges_by_edge_type("drug-1", "drug-to-protein")  # doctest: +SKIP
        """
        return _adjacency.edges_by_edge_type(
            self.store, _node_key_to_bytes(node_id), edge_type, direction)

    def sample_nodes(
        self,
        sample_size: int,
        *,
        label: str | None = None,
        node_filter: Callable[[Node], bool] | None = None,
        rng=None,
    ) -> list[str]:
        """Sample live node IDs using reservoir sampling.

        Memory is bounded by ``sample_size`` rather than by graph size, but
        eligible keys are still scanned. ``label`` restricts the scan to one
        native-label index; otherwise all live node keys are visited. No
        property-index pushdown is performed: ``node_filter`` receives each
        decoded ``Node`` and runs during the scan. Use snapshot
        ``SamplerEngine.sample_nodes`` for array-native, index-free sampling.

        Args:
            sample_size: Maximum number of node IDs to return.
            label: Optional native label restricting the candidate set.
            node_filter: Optional predicate over decoded ``Node`` objects.
            rng: Optional random number generator with ``randrange``.

        Returns:
            Sampled node IDs as strings, in reservoir order.

        Raises:
            ValueError: If ``sample_size`` is not a non-negative integer.
            TypeError: If ``node_filter`` is not callable.

        Examples:
            >>> graph_db.sample_nodes(5, label="Drug", rng=random.Random(7))  # doctest: +SKIP
            ['drug-3', 'drug-1', 'drug-7', 'drug-2', 'drug-5']
        """
        return _sampling.sample_nodes(
            self.store, self.entity_serializer, self._ensure_indexes_current,
            sample_size, label=label, node_filter=node_filter, rng=rng,
        )

    def sample_neighbors(self, node_id, edge_type: str, direction: str = 'out', sample_size: int = 10, rng=None, *, relation_hierarchy=None):
        """Sample typed neighbors using reservoir sampling.

        Args:
            node_id: Node ID as string or bytes.
            edge_type: Edge type to traverse. With ``relation_hierarchy``, this
                is the requested parent type.
            direction: ``"out"``, ``"in"``, or ``"any"``.
            sample_size: Maximum number of records to return.
            rng: Optional random number generator with ``randrange``.
            relation_hierarchy: Optional ``RelationHierarchy``. When supplied,
                ``edge_type`` expands to itself plus all transitive
                sub-properties and the reservoir is drawn from their union, so
                ``sample_size`` remains the total fanout. Default traversal
                stays literal.

        Returns:
            List of typed adjacency records.

        Examples:
            >>> graph_db.sample_neighbors("drug-1", "drug-to-protein", sample_size=2)  # doctest: +SKIP
        """
        return _sampling.sample_neighbors(
            self.store, node_id, edge_type, direction=direction,
            sample_size=sample_size, rng=rng, relation_hierarchy=relation_hierarchy,
        )

    def sample_typed_paths(self, seed_ids, pattern: Union[SamplingPattern, list[dict]], rng=None, *, relation_hierarchy=None):
        """Sample paths that follow an ordered typed edge pattern.

        Args:
            seed_ids: Starting node IDs as strings or bytes.
            pattern: ``SamplingPattern`` or list of dictionaries such as
                ``{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}``.
            rng: Optional random number generator with ``randrange``.
            relation_hierarchy: Optional ``RelationHierarchy`` applied to every
                hop. Each hop's ``edge_type`` expands to its transitive
                sub-properties, with ``sample_size`` shared across the union.

        Returns:
            List of dictionaries with ``seed`` and sampled ``path`` records.

        Examples:
            >>> from gestaltdb.sampling import SamplingHop, SamplingPattern
            >>> pattern = SamplingPattern([SamplingHop("drug-to-protein", sample_size=2)])
            >>> graph_db.sample_typed_paths(["drug-1"], pattern)  # doctest: +SKIP
        """
        return _sampling.sample_typed_paths(
            self.store, seed_ids, pattern, rng=rng,
            relation_hierarchy=relation_hierarchy,
        )

    def sample_typed_subgraph(self, seed_ids, pattern: Union[SamplingPattern, list[dict]], rng=None, *, relation_hierarchy=None):
        """Sample and materialize a typed subgraph around seed nodes.

        Args:
            seed_ids: Starting node IDs as strings or bytes.
            pattern: ``SamplingPattern`` or list of dictionary hop configs.
            rng: Optional random number generator with ``randrange``.
            relation_hierarchy: Optional ``RelationHierarchy`` applied to every
                hop before sampling.

        Returns:
            Dictionary with ``nodes``, ``edges``, and ``paths`` entries.

        Examples:
            >>> pattern = [{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}]
            >>> graph_db.sample_typed_subgraph(["drug-1"], pattern)  # doctest: +SKIP
        """
        return _sampling.sample_typed_subgraph(
            self.store, self.entity_serializer, seed_ids, pattern, rng=rng,
            relation_hierarchy=relation_hierarchy,
        )

    def rebuild_typed_adjacency(self):
        """Rebuild typed adjacency indexes from stored edge records.

        Returns:
            Number of typed edges indexed.

        Examples:
            >>> graph_db.rebuild_typed_adjacency()  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.rebuild_typed_adjacency()
        rebuilt = 0
        self._typed_adjacency_count_cache.clear()
        for edge_id in self.store.get_edge_keys_generator():
            edge = self.get_edge(edge_id)
            if edge is None or self.edge_type(edge) is None:
                continue
            self._put_typed_adjacency_for_edge(edge)
            rebuilt += 1
        return rebuilt

    def get_edge(self, edge_id) -> Edge:
        """Return an edge by byte key.

        Args:
            edge_id: Edge ID bytes as stored in the backend.

        Returns:
            The decoded edge, or ``None`` when absent.

        Examples:
            >>> graph_db.get_edge(b"d1-p1")  # doctest: +SKIP
        """
        return _edge_records.get_edge(self.store, self.entity_serializer, edge_id)

    # -----------
    # Example Range Query
    #  (Implementation depends on how you store indexes in the KVStore)
    # -----------
    def range_query_nodes(self, property_name: str, start_val, end_val):
        """Example stub: You might rely on the underlying store to handle indexing for nodes."""
        start_key = f"IDX:N:{property_name}:{start_val}:".encode('utf-8')
        end_key = f"IDX:N:{property_name}:{end_val}:\xff".encode('utf-8')
        for k, v in self.store.range_iter(start_key, end_key):
            # parse node id from k, retrieve node
            parts = k.decode('utf-8').split(':')
            node_id = parts[-1]
            yield self.get_node(node_id)

    # --------------------------------
    # Merge-based update (single node)
    # --------------------------------
    def update_node(self, node_id: str, new_data: dict, merge_func) -> Node:
        """
        Fetch existing node (if any). If none found, treat as new or handle gracefully.
        merge_func(old_node_dict, new_data_dict) -> merged_properties (dict)
        """
        old_node = self.get_node(node_id)
        if old_node is None:
            # Decide if we create a new node or return None
            old_node = Node(node_id=node_id, properties={})
        # merged_properties is a dict that results from the user’s custom logic
        merged_props = merge_func(old_node.properties, new_data)
        old_node.properties = merged_props
        self.put_node(old_node)
        return old_node

    def update_edge(self, edge_id: str, new_data: dict, merge_func) -> Edge:
        """
        Similar approach for edges. The new_data might include new properties,
        or you might also allow changing source/target if that makes sense.
        """
        old_edge = self.get_edge(edge_id)
        if old_edge is None:
            # treat as new or handle differently
            old_edge = Edge(edge_id=edge_id, source=None, target=None, properties={})
        merged_props = merge_func(old_edge.properties, new_data)
        old_edge.properties = merged_props
        self.put_edge(old_edge)
        return old_edge

    # --------------------------------
    # Bulk put of nodes
    # --------------------------------
    def put_nodes(self, nodes: list[Node]):
        """Store multiple nodes and maintain label/property indexes.

        Args:
            nodes: Nodes to serialize and write.

        Examples:
            >>> graph_db.put_nodes([Node(node_id="drug-1", labels=["Drug"])])  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.put_nodes(nodes)
        to_store = {}
        index_entries = []
        range_entries = []
        for n in nodes:
            old_node = self.get_node(n.get_id_bytes)
            if old_node is not None:
                self._delete_node_indexes(old_node)
            to_store[n.get_id_bytes] = self.entity_serializer.serialize(n, 'Node')
            for label in n.labels:
                index_entries.append(("node_label", [label.encode("utf-8")], n.get_id_bytes))
            for property_name in self.indexed_node_properties:
                if property_name in n.properties:
                    property_value = _property_value_to_index_bytes(n.properties[property_name])
                    index_entries.append((
                        "node_property",
                        [property_name.encode("utf-8"), property_value],
                        n.get_id_bytes,
                    ))
                    for label in n.labels:
                        index_entries.append((
                            "node_label_property",
                            [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                            n.get_id_bytes,
                        ))
                    range_value = _property_value_to_range_index_bytes(n.properties[property_name])
                    if range_value is not None:
                        range_entries.append((
                            "node_property",
                            [property_name.encode("utf-8")],
                            range_value,
                            n.get_id_bytes,
                        ))
                        for label in n.labels:
                            range_entries.append((
                                "node_label_property",
                                [label.encode("utf-8"), property_name.encode("utf-8")],
                                range_value,
                                n.get_id_bytes,
                            ))
        self.store.put_nodes_bulk(to_store)
        if index_entries:
            self.store.put_index_entries_bulk(index_entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)

    def ingest_polars(
        self,
        node_df,
        edge_df,
        *,
        ingestion_mode: str | ColumnarIngestionMode = ColumnarIngestionMode.ENTITY_COLUMNS,
        index_mode: str | IndexMaintenanceMode = IndexMaintenanceMode.DEFER_REBUILD,
        node_id: str = "node_id",
        node_value: str = "node_value",
        labels: str | list[str] | tuple[str, ...] | None = "labels",
        node_property_columns: list[str] | tuple[str, ...] | None = None,
        edge_id: str = "edge_id",
        source: str = "source",
        target: str = "target",
        edge_type: str = "edge_type",
        edge_value: str = "edge_value",
        edge_property_columns: list[str] | tuple[str, ...] | None = None,
        native: bool = True,
        chunk_size: int = 100_000,
        progress: bool = False,
    ) -> dict[str, object]:
        """Ingest a graph from Polars DataFrames with bulk-load defaults.

        By default this uses structured entity columns, defers secondary index
        maintenance during ingestion, and immediately performs a one-pass rebuild
        before returning. Typed adjacency is maintained during ingestion, so
        traversal remains correct throughout.

        Example:
            >>> graph.ingest_polars(nodes, edges, node_property_columns=["kind"], edge_property_columns=["score"])  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.ingest_polars(
                    node_df, edge_df,
                    ingestion_mode=ingestion_mode, index_mode=index_mode,
                    node_id=node_id, node_value=node_value, labels=labels,
                    node_property_columns=node_property_columns,
                    edge_id=edge_id, source=source, target=target,
                    edge_type=edge_type, edge_value=edge_value,
                    edge_property_columns=edge_property_columns,
                    native=native, chunk_size=chunk_size, progress=progress,
                )
        mode = self._coerce_columnar_ingestion_mode(ingestion_mode)
        low_level_index_mode = self._low_level_index_mode(index_mode)
        if mode == ColumnarIngestionMode.ENTITY_COLUMNS.value:
            node_count = self.ingest_nodes_polars_entities(
                node_df,
                node_id=node_id,
                labels=labels,
                property_columns=node_property_columns,
                native=native,
                chunk_size=chunk_size,
                append_only=True,
                index_mode=low_level_index_mode,
                progress=progress,
            )
            edge_count = self.ingest_edges_polars_entities(
                edge_df,
                edge_id=edge_id,
                source=source,
                target=target,
                edge_type=edge_type,
                property_columns=edge_property_columns,
                append_only=True,
                native=native,
                chunk_size=chunk_size,
                index_mode=low_level_index_mode,
                progress=progress,
            )
        else:
            node_count = self.ingest_nodes_polars(
                node_df,
                node_id=node_id,
                node_value=node_value,
                native=native,
                chunk_size=chunk_size,
                append_only=True,
                index_mode=low_level_index_mode,
                progress=progress,
            )
            edge_count = self.ingest_edges_polars(
                edge_df,
                edge_id=edge_id,
                source=source,
                target=target,
                edge_type=edge_type,
                edge_value=edge_value,
                append_only=True,
                native=native,
                chunk_size=chunk_size,
                index_mode=low_level_index_mode,
                progress=progress,
            )
        rebuilt = self.rebuild_deferred_indexes() if self._should_rebuild_after_ingest(index_mode) else {}
        return {"nodes": node_count, "edges": edge_count, "rebuilt_indexes": rebuilt, "stale_indexes": self.stale_indexes()}

    def ingest_arrow(
        self,
        node_ids,
        edge_ids,
        sources,
        targets,
        edge_types,
        *,
        ingestion_mode: str | ColumnarIngestionMode = ColumnarIngestionMode.ENTITY_COLUMNS,
        index_mode: str | IndexMaintenanceMode = IndexMaintenanceMode.DEFER_REBUILD,
        node_values=None,
        edge_values=None,
        labels=None,
        node_properties: dict[str, object] | None = None,
        edge_properties: dict[str, object] | None = None,
        native: bool = True,
        chunk_size: int = 100_000,
        progress: bool = False,
    ) -> dict[str, object]:
        """Ingest a graph from Arrow-like columns with bulk-load defaults.

        ``ENTITY_COLUMNS`` builds payloads from structured node/edge columns.
        ``SERIALIZED_PAYLOADS`` expects ``node_values`` and ``edge_values`` to
        contain serializer-compatible payload bytes.

        Examples:
            Load two entities and a typed edge (requires ``lmdb`` and ``pyarrow``)::

                >>> from tempfile import TemporaryDirectory
                >>> import pyarrow as pa
                >>> from gestaltdb.graphdb import GraphDB
                >>> with TemporaryDirectory() as root:
                ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
                ...     try:
                ...         _ = graph.ingest_arrow(
                ...             pa.array(["a", "b"]), pa.array(["ab"]),
                ...             pa.array(["a"]), pa.array(["b"]), pa.array(["knows"]),
                ...             node_properties={"name": pa.array(["Alice", "Bob"])})
                ...         assert graph.get_edge(b"ab").properties["type"] == "knows"
                ...         assert graph.get_node(b"a").properties["name"] == "Alice"
                ...     finally:
                ...         graph.close()

        The default rebuilds deferred indexes before returning. Native columnar
        writes are available on plain PyRex; transactional PyRex uses a different
        write path. See the operations/performance guides for backend trade-offs.
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.ingest_arrow(
                    node_ids, edge_ids, sources, targets, edge_types,
                    ingestion_mode=ingestion_mode, index_mode=index_mode,
                    node_values=node_values, edge_values=edge_values, labels=labels,
                    node_properties=node_properties, edge_properties=edge_properties,
                    native=native, chunk_size=chunk_size, progress=progress,
                )
        mode = self._coerce_columnar_ingestion_mode(ingestion_mode)
        low_level_index_mode = self._low_level_index_mode(index_mode)
        if mode == ColumnarIngestionMode.ENTITY_COLUMNS.value:
            node_count = self.ingest_nodes_arrow_entities(
                node_ids,
                labels=labels,
                properties=node_properties,
                native=native,
                chunk_size=chunk_size,
                append_only=True,
                index_mode=low_level_index_mode,
                progress=progress,
            )
            edge_count = self.ingest_edges_arrow_entities(
                edge_ids,
                sources,
                targets,
                edge_types,
                properties=edge_properties,
                append_only=True,
                native=native,
                chunk_size=chunk_size,
                index_mode=low_level_index_mode,
                progress=progress,
            )
        else:
            if node_values is None or edge_values is None:
                raise ValueError("node_values and edge_values are required for serialized payload ingestion")
            node_count = self.ingest_nodes_arrow(
                node_ids,
                node_values,
                native=native,
                chunk_size=chunk_size,
                append_only=True,
                index_mode=low_level_index_mode,
                progress=progress,
            )
            edge_count = self.ingest_edges_arrow(
                edge_ids,
                sources,
                targets,
                edge_types,
                edge_values,
                append_only=True,
                native=native,
                chunk_size=chunk_size,
                index_mode=low_level_index_mode,
                progress=progress,
            )
        rebuilt = self.rebuild_deferred_indexes() if self._should_rebuild_after_ingest(index_mode) else {}
        return {"nodes": node_count, "edges": edge_count, "rebuilt_indexes": rebuilt, "stale_indexes": self.stale_indexes()}

    def ingest_nodes_arrow(
        self,
        node_ids,
        node_values,
        *,
        native: bool = True,
        chunk_size: int = 100_000,
        append_only: bool = False,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest attributed nodes from Arrow-like columns.

        ``node_values`` is required and must contain serialized node payloads
        compatible with the current ``GraphDB`` serializer.

        Args:
            node_ids: Arrow-like or Python column of node IDs.
            node_values: Arrow-like or Python column of serialized node bytes.
            native: Use native backend columnar ingestion when available.
            chunk_size: Maximum rows per backend write.
            append_only: Skip existing-node index deletion for known-new nodes.
            index_mode: ``"maintain"`` updates secondary indexes immediately;
                ``"defer"`` writes node records and marks indexes stale.

        Returns:
            Number of ingested nodes.
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.ingest_nodes_arrow(
                    node_ids, node_values, native=native, chunk_size=chunk_size,
                    append_only=append_only, index_mode=index_mode, progress=progress,
                )
        return _ingest_nodes.ingest_nodes_arrow(
            self.store, self.entity_serializer, self.indexed_node_properties,
            _STALE_INDEXES_METADATA_KEY, node_ids, node_values, native=native,
            chunk_size=chunk_size, append_only=append_only, index_mode=index_mode,
            progress=progress,
        )

    def ingest_nodes_polars(
        self,
        df,
        *,
        node_id: str = "node_id",
        node_value: str = "node_value",
        native: bool = True,
        chunk_size: int = 100_000,
        append_only: bool = False,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest attributed nodes from a Polars DataFrame.

        The ``node_value`` column is required and must contain serialized node
        payload bytes compatible with the current ``GraphDB`` serializer.
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.ingest_nodes_polars(
                    df, node_id=node_id, node_value=node_value, native=native,
                    chunk_size=chunk_size, append_only=append_only,
                    index_mode=index_mode, progress=progress,
                )
        return _ingest_nodes.ingest_nodes_polars(
            self.store, self.entity_serializer, self.indexed_node_properties,
            _STALE_INDEXES_METADATA_KEY, df, node_id=node_id, node_value=node_value,
            native=native, chunk_size=chunk_size, append_only=append_only,
            index_mode=index_mode, progress=progress,
        )

    def ingest_nodes_polars_entities(
        self,
        df,
        *,
        node_id: str = "node_id",
        labels: str | list[str] | tuple[str, ...] | None = "labels",
        property_columns: list[str] | tuple[str, ...] | None = None,
        native: bool = True,
        chunk_size: int = 100_000,
        append_only: bool = False,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest node entity columns from a Polars DataFrame.

        With ``JSONSerializer``, node payloads are built with Polars struct JSON
        encoding. Other serializers fall back to Python ``Node`` serialization.
        """
        return _ingest_nodes.ingest_nodes_polars_entities(
            self.store, self.entity_serializer, self.serializer,
            self.ingest_nodes_arrow, df, node_id=node_id, labels=labels,
            property_columns=property_columns, native=native, chunk_size=chunk_size,
            append_only=append_only, index_mode=index_mode, progress=progress,
        )

    def ingest_nodes_arrow_entities(
        self,
        node_ids,
        *,
        labels=None,
        properties: dict[str, object] | None = None,
        native: bool = True,
        chunk_size: int = 100_000,
        append_only: bool = False,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest node entity columns from Arrow-like columns."""
        return _ingest_nodes.ingest_nodes_arrow_entities(
            self.store, self.entity_serializer, self.serializer,
            self.ingest_nodes_arrow, self.ingest_nodes_polars_entities,
            node_ids, labels=labels, properties=properties, native=native,
            chunk_size=chunk_size, append_only=append_only, index_mode=index_mode,
            progress=progress,
        )

    def _polars_labels_expr(self, pl, df, labels):
        """Return a Polars expression for node labels."""
        return _ingest_support.polars_labels_expr(pl, df, labels)

    def _polars_properties_expr(self, pl, property_columns):
        """Return a Polars expression for an entity properties object."""
        return _ingest_support.polars_properties_expr(pl, property_columns)

    def _validate_polars_frame(self, pl, df, feature_name: str) -> None:
        """Validate that input is a Polars eager or lazy frame."""
        _ingest_support.validate_polars_frame(pl, df, feature_name)

    def _polars_columns(self, pl, df) -> list[str]:
        """Return column names without collecting LazyFrame data."""
        return _ingest_support.polars_columns(pl, df)

    def _polars_row_count(self, pl, df):
        """Return an eager row count, or None for lazy inputs."""
        return _ingest_support.polars_row_count(pl, df)

    def _iter_polars_chunks(self, pl, df, chunk_size: int):
        """Yield eager DataFrame chunks from eager or lazy Polars input."""
        return _ingest_support.iter_polars_chunks(pl, df, chunk_size)

    def _delete_existing_node_indexes_for_columnar_chunk(self, chunk: NodeList) -> None:
        """Remove stale indexes before opaque columnar node upserts."""
        _ingest_support.delete_existing_node_indexes_for_columnar_chunk(
            self.store, self.entity_serializer, self.indexed_node_properties, chunk)

    def _put_node_indexes_for_columnar_chunk(self, chunk: NodeList) -> None:
        """Maintain label and configured property indexes for columnar nodes."""
        _ingest_support.put_node_indexes_for_columnar_chunk(
            self.store, self.entity_serializer, self.indexed_node_properties, chunk)

    def serialize_node_value(self, node: Node) -> bytes:
        """Serialize a node for use with columnar node ingestion."""
        return self.entity_serializer.serialize(node, "Node")

    def serialize_edge_value(self, edge: Edge) -> bytes:
        """Serialize an edge for use with columnar edge ingestion."""
        return self.entity_serializer.serialize(edge, "Edge")

    def get_nodes(self, node_ids: list[str]) -> list[Node]:
        """
        Use store.get_nodes_bulk(...) and deserialize each one.
        Return a list of Node (in the same order as node_ids, or possibly just all found).
        """
        return _node_records.get_nodes(self.store, self.serializer, node_ids)
    def get_node_keys_generator(self, num_nodes = None, key_offset = None):
        """Yield node keys from the backing store.

        Args:
            num_nodes: Optional maximum number of keys to yield.
            key_offset: Optional starting key.

        Returns:
            Generator of node key bytes.

        Examples:
            >>> list(graph_db.get_node_keys_generator(num_nodes=10))  # doctest: +SKIP
        """
        return _node_records.get_node_keys_generator(self.store, num_nodes, key_offset)

    # -----------------------
    # Adjacency Management
    # -----------------------
    
    # def _append_edge_to_adjacency(self, node_id: str, edge_id: str):
    #     """
    #     Internal helper: read the adjacency list for node_id,
    #     append edge_id if not present, and write it back.
    #     """
    #     edges_list = self.get_adjacency_list(node_id)
    #     if edge_id not in edges_list:
    #         edges_list.append(edge_id)
    #         self.put_adjacency_list(node_id, edges_list)

    def get_adjacency_list(self, node_id: bytes,direction = 'forward', return_raw = False) -> list[str]:
        """
        Returns the list of edge IDs connected to node_id.
        If none found, returns an empty list.
        
        Args:
          node_id: a string representing the node_id
          direction : 'forward', 'backward' or 'any' -> controls whether the source, target, or un-directed adjacency of the node will be returned. 
          return_raw : if this flag is true it will return the data as they are stored (e.g., a dictionary of 'source' and 'target' lists. )
        """
        
        return _edge_records.get_adjacency_list(self.store, self.serializer, node_id, direction, return_raw)
        
    def put_adjacency_list(self, node_id: str, edges_list: list[str]):
        """Stores the adjacency list for node_id."""
        _edge_records.put_adjacency_list(self.store, self.serializer, node_id, edges_list)

    # -----------------------
    # Deletion (Optional)
    # -----------------------
    def delete_edge(self, edge_id: str, edge_key_serializer= lambda x:  x.encode('utf-8')):
        """
        Removes the edge from the edge store, and from adjacency
        of both source and target nodes. If either node doesn't exist,
        we skip gracefully.
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.delete_edge(edge_id, edge_key_serializer=edge_key_serializer)
        _edge_records.delete_edge(self.store, self.entity_serializer, self.serializer, self.indexed_edge_properties, self._typed_adjacency_count_cache, edge_id)

    def _edge_id_variants(self, edge_id):
        """Return string/byte forms used by legacy adjacency records."""
        return _edge_records.edge_id_variants(edge_id)

    def _remove_edge_from_endpoint_adjacencies(self, edge: Edge):
        """Remove an edge from the legacy adjacency blobs for its endpoints."""
        _edge_records.remove_edge_from_endpoint_adjacencies(self.store, self.serializer, edge)

    def _remove_edge_from_adjacency(self, node_id: str, edge_id: str):
        """Remove an edge ID from a node's stored adjacency lists.

        Args:
            node_id: Node key whose adjacency list should be updated.
            edge_id: Edge key to remove.
        """

        
        _edge_records.remove_edge_from_adjacency(self.store, self.serializer, node_id, edge_id)

    # -----------------------
    # BFS Example
    # -----------------------
    def bfs(self, start_node_id: bytes, direction = 'any', edge_key_serializer = lambda x : x.encode('utf-8'), node_key_serializer = lambda x : x.encode('utf-8')) -> list[str]:
        """
        Returns a list of node_ids in BFS order starting from `start_node_id`.
        Demonstrates how adjacency is used for graph traversal.
        """
        visited = set()
        queue = [start_node_id]
        result = []
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            result.append(current)

            # 1) get adjacency list for current
            edges_list = self.get_adjacency_list(current, direction = direction)
            
            # 2) for each edge in adjacency, find the other node
            for e_id in edges_list:
                _ser_edge_key = edge_key_serializer(e_id)
                edge_obj = self.get_edge(_ser_edge_key)
                if not edge_obj:
                    continue
                _source_node = node_key_serializer(edge_obj.source)
                neighbor = (
                    node_key_serializer(edge_obj.target) if _source_node == current else _source_node
                )
                if neighbor not in visited:
                    queue.append(neighbor)

        return result

    def put_edges_bulk(self, edges: List[Edge], check_existing: bool = True):
        """Store multiple edges and update adjacency indexes in bulk.

        Args:
            edges: Edges to write.
            check_existing: When true, read existing edges first and remove stale
                typed adjacency and sorted index records before replacement. Set
                to false for append-only ingestion with new edge IDs.

        Examples:
            >>> graph_db.put_edges_bulk([Edge(source="drug-1", target="protein-1")], check_existing=False)  # doctest: +SKIP
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.put_edges_bulk(edges, check_existing=check_existing)
        _ingest_edges.put_edges_bulk(
            self.store, self.entity_serializer, self.serializer,
            self.indexed_edge_properties, self._typed_adjacency_count_cache,
            edges, check_existing=check_existing,
        )

    def ingest_edges_arrow(
        self,
        edge_ids,
        sources,
        targets,
        edge_types,
        edge_values,
        *,
        append_only: bool = True,
        native: bool = True,
        chunk_size: int = 100_000,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest typed edges from Arrow-like columns.

        ``edge_values`` is required and must contain serialized edge payloads
        compatible with the current ``GraphDB`` serializer. This ingestion path
        writes edge records and typed adjacency records only; it intentionally
        skips legacy adjacency blobs for append-friendly bulk loading.

        Args:
            edge_ids: Arrow-like or Python column of edge IDs.
            sources: Arrow-like or Python column of source node IDs.
            targets: Arrow-like or Python column of target node IDs.
            edge_types: Arrow-like or Python column of typed traversal labels.
            edge_values: Arrow-like or Python column of serialized edge bytes.
            append_only: Columnar ingestion currently requires ``True``.
            native: Use native backend columnar ingestion when available.
            chunk_size: Maximum rows per backend write.
            index_mode: ``"maintain"`` updates secondary indexes immediately;
                ``"defer"`` writes edge records and typed adjacency and marks
                secondary indexes stale.

        Returns:
            Number of ingested edges.
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.ingest_edges_arrow(
                    edge_ids, sources, targets, edge_types, edge_values,
                    append_only=append_only, native=native, chunk_size=chunk_size,
                    index_mode=index_mode, progress=progress,
                )
        return _ingest_edges.ingest_edges_arrow(
            self.store, self.entity_serializer, self.indexed_edge_properties,
            _STALE_INDEXES_METADATA_KEY, self._typed_adjacency_count_cache,
            edge_ids, sources, targets, edge_types, edge_values,
            append_only=append_only, native=native, chunk_size=chunk_size,
            index_mode=index_mode, progress=progress,
        )

    def ingest_edges_polars(
        self,
        df,
        *,
        edge_id: str = "edge_id",
        source: str = "source",
        target: str = "target",
        edge_type: str = "edge_type",
        edge_value: str = "edge_value",
        append_only: bool = True,
        native: bool = True,
        chunk_size: int = 100_000,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest typed edges from a Polars DataFrame.

        The ``edge_value`` column is required and must contain serialized edge
        payload bytes compatible with the current ``GraphDB`` serializer.
        """
        if self._implicit_transaction_required():
            with self.transaction() as tx:
                return tx.ingest_edges_polars(
                    df, edge_id=edge_id, source=source, target=target,
                    edge_type=edge_type, edge_value=edge_value,
                    append_only=append_only, native=native, chunk_size=chunk_size,
                    index_mode=index_mode, progress=progress,
                )
        return _ingest_edges.ingest_edges_polars(
            self.store, self.entity_serializer, self.indexed_edge_properties,
            _STALE_INDEXES_METADATA_KEY, self._typed_adjacency_count_cache,
            df, edge_id=edge_id, source=source, target=target, edge_type=edge_type,
            edge_value=edge_value, append_only=append_only, native=native,
            chunk_size=chunk_size, index_mode=index_mode, progress=progress,
        )

    def ingest_edges_polars_entities(
        self,
        df,
        *,
        edge_id: str = "edge_id",
        source: str = "source",
        target: str = "target",
        edge_type: str = "edge_type",
        property_columns: list[str] | tuple[str, ...] | None = None,
        append_only: bool = True,
        native: bool = True,
        chunk_size: int = 100_000,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest edge entity columns from a Polars DataFrame.

        With ``JSONSerializer``, edge payloads are built with Polars struct JSON
        encoding. Other serializers fall back to Python ``Edge`` serialization.
        """
        return _ingest_edges.ingest_edges_polars_entities(
            self.store, self.entity_serializer, self.serializer,
            self.indexed_edge_properties, _STALE_INDEXES_METADATA_KEY,
            self._typed_adjacency_count_cache, self.ingest_edges_arrow,
            df, edge_id=edge_id, source=source, target=target, edge_type=edge_type,
            property_columns=property_columns, append_only=append_only,
            native=native, chunk_size=chunk_size, index_mode=index_mode,
            progress=progress,
        )

    def ingest_edges_arrow_entities(
        self,
        edge_ids,
        sources,
        targets,
        edge_types,
        *,
        properties: dict[str, object] | None = None,
        append_only: bool = True,
        native: bool = True,
        chunk_size: int = 100_000,
        index_mode: str = "maintain",
        progress: bool = False,
    ):
        """Ingest edge entity columns from Arrow-like columns."""
        return _ingest_edges.ingest_edges_arrow_entities(
            self.store, self.entity_serializer, self.serializer,
            self.indexed_edge_properties, _STALE_INDEXES_METADATA_KEY,
            self._typed_adjacency_count_cache, self.ingest_edges_arrow,
            self.ingest_edges_polars_entities,
            edge_ids, sources, targets, edge_types, properties=properties,
            append_only=append_only, native=native, chunk_size=chunk_size,
            index_mode=index_mode, progress=progress,
        )

    def _put_edge_property_indexes_for_columnar_chunk(self, chunk: EdgeList) -> None:
        """Maintain configured edge property indexes for columnar edges."""
        _ingest_edges.put_edge_property_indexes_for_columnar_chunk(
            self.store, self.entity_serializer, self.indexed_edge_properties, chunk)

    # ------------------------
    # Temporal Version History
    # ------------------------

    @staticmethod
    def _temporal_commit_key(commit_id: int) -> bytes:
        return _temporal_keys.temporal_commit_key(commit_id)

    @staticmethod
    def _temporal_visible_key(commit_id: int) -> bytes:
        return _temporal_keys.temporal_visible_key(commit_id)

    @staticmethod
    def _temporal_record_key(commit_id: int, ordinal: int) -> bytes:
        return _temporal_keys.temporal_record_key(commit_id, ordinal)

    @staticmethod
    def _temporal_locator(commit_id: int, ordinal: int) -> bytes:
        return _temporal_keys.temporal_locator(commit_id, ordinal)

    @staticmethod
    def _decode_temporal_locator(locator: bytes) -> tuple[int, int]:
        return _temporal_keys.decode_temporal_locator(locator)

    @staticmethod
    def _temporal_instant_index_value(instant: TemporalInstant) -> bytes:
        return _temporal_keys.temporal_instant_index_value(instant)

    def _load_temporal_index_state(self) -> dict | None:
        return _temporal_indexes.load_index_state(self.store, _TEMPORAL_INDEX_STATE_KEY)

    def _temporal_index_state(self) -> int | None:
        return _temporal_indexes.index_state_horizon(self.store, _TEMPORAL_INDEX_STATE_KEY)

    def _temporal_index_generation(self) -> int:
        return _temporal_indexes.index_state_generation(self.store, _TEMPORAL_INDEX_STATE_KEY)

    @staticmethod
    def _temporal_index_name(index_name: str, generation: int) -> str:
        return _temporal_indexes.index_name(index_name, generation)

    def _temporal_visibility_checksum(
        self, through_commit: int, *, pending_marker=None
    ) -> str:
        return _temporal_indexes.visibility_checksum(
            self.store, through_commit, pending_marker=pending_marker
        )

    def _persist_temporal_index_state(
        self, commit_id: int, generation: int, *, pending_marker=None
    ) -> None:
        _temporal_indexes.persist_index_state(
            self.store, _TEMPORAL_INDEX_STATE_KEY, commit_id, generation,
            pending_marker=pending_marker,
        )

    def _ensure_temporal_indexes(self, horizon: int) -> None:
        _temporal_indexes.ensure_temporal_indexes(
            self.store, _TEMPORAL_INDEX_STATE_KEY, _STALE_INDEXES_METADATA_KEY, horizon
        )

    def _has_visible_temporal_history(self) -> bool:
        return _temporal_indexes.has_visible_temporal_history(
            self.store, _TEMPORAL_SEQUENCE_KEY, self._store_path,
            _TEMPORAL_SEQUENCE_FILENAME,
        )

    def _temporal_index_entries(self, version, *, generation=None):
        generation = self._temporal_index_generation() if generation is None else generation
        return _temporal_indexes.temporal_index_entries(version, generation=generation)

    def _write_temporal_indexes(self, versions, commit_id: int, *, generation=None) -> dict[str, int]:
        generation = self._temporal_index_generation() if generation is None else generation
        return _temporal_indexes.write_temporal_indexes(
            self.store, versions, commit_id, generation=generation)

    @contextmanager
    def _temporal_writer_guard(self):
        if self._store_path is None:
            with self._temporal_write_lock:
                yield
            return
        self.database_id
        with _temporal_writer_lock(self._store_path):
            yield

    @staticmethod
    def _decode_temporal_sequence(payload: bytes) -> tuple[int, TemporalInstant]:
        return _temporal_sequence.decode_temporal_sequence(payload)

    def _temporal_sequence(self) -> tuple[int, TemporalInstant | None]:
        return _temporal_sequence.read_temporal_sequence(
            self.store, _TEMPORAL_SEQUENCE_KEY, self._store_path,
            _TEMPORAL_SEQUENCE_FILENAME,
        )

    def _reserve_temporal_sequence(self) -> tuple[int, TemporalInstant]:
        return _temporal_sequence.reserve_temporal_sequence(
            self.store, _TEMPORAL_SEQUENCE_KEY, self._store_path,
            _TEMPORAL_SEQUENCE_FILENAME, self._temporal_clock,
        )

    def _next_temporal_system_time(self, previous: TemporalInstant | None) -> TemporalInstant:
        return _temporal_sequence.next_temporal_system_time(self._temporal_clock, previous)

    # ------------------------
    # Positive Horn Rules
    # ------------------------

    def _load_rule_catalog(self) -> tuple[RuleVersion, ...]:
        return _stored_rules.load_rule_catalog(self.store)

    def create_rule(
        self, rule_id, *, when, then, not_when=(), closed_world=(), version_id=None
    ) -> RuleVersion:
        """Validate and append one immutable version of a Horn rule.

        ``not_when`` atoms are closed-world absence checks. Every negated
        predicate must be named in the active program's ``closed_world``
        declarations, and the resulting active program must be stratified.
        Unstratifiable programs are rejected before the catalog changes.
        """
        return _stored_rules.create_rule(
            self.store, self._temporal_write_lock, self._temporal_clock,
            rule_id, when=when, then=then, not_when=not_when,
            closed_world=closed_world, version_id=version_id,
        )

    def iter_rule_versions(self, rule_id=None, *, system_time=None):
        """Iterate immutable rule versions in catalog order at a system-time horizon."""
        return _stored_rules.iter_rule_versions(self.store, rule_id, system_time=system_time)

    def get_rule(self, rule_id, *, system_time=None) -> RuleVersion | None:
        """Return the latest version of a named rule at a system-time horizon."""
        return _stored_rules.get_rule(self.store, rule_id, system_time=system_time)

    @staticmethod
    def _rule_justifications(
        provenance,
    ) -> set[tuple[str, tuple[str, ...], tuple[RuleAbsence, ...]]]:
        return _stored_rules.rule_justifications(provenance)
    @staticmethod
    def _justification_order(justification):
        return _stored_rules.justification_order(justification)

    def _rule_program(self, *, system_time):
        """Return active rule versions and their stratification schedule."""
        return _stored_rules.rule_program(self.store, system_time=system_time)

    @staticmethod
    def _rule_scope(rules) -> tuple[str, ...]:
        return _stored_rules.rule_scope(rules)

    @staticmethod
    def _absence_checks(rule, bindings, valid, world, facts_by_predicate, scope):
        """Return ground absence witnesses or ``None`` when negation is blocked.

        A negated atom is satisfied only when no matching positive claim
        overlaps the candidate conclusion's whole validity interval. Partial
        overlap blocks the derivation instead of producing an interval fragment
        that the claim model cannot represent.
        """
        return _stored_rules.absence_checks(rule, bindings, valid, world, facts_by_predicate, scope)

    def run_rules(
        self,
        *,
        as_of,
        system_time=None,
        world=None,
        max_iterations=100,
        max_derivations=10_000,
        max_justifications=100_000,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> RuleRunResult:
        """Evaluate active rules semi-naively and persist a bounded fixpoint.

        Positive components run in stratification order. Only positive
        entity-object claims participate; ``not_when`` atoms are closed-world
        absence checks against completed lower strata, not negative claims.
        Premises must share a world, and each conclusion receives their
        half-open validity intersection. No claims are written if any resource
        bound is exceeded.

        Examples:
            Persist a conclusion, explain it, and maintain it after evidence loss
            (requires ``lmdb``)::

                >>> from tempfile import TemporaryDirectory
                >>> from gestaltdb.graphdb import GraphDB
                >>> with TemporaryDirectory() as root:
                ...     graph = GraphDB.create(root + "/db", backend="lmdb", serializer="json")
                ...     try:
                ...         base = graph.assert_claim(subject="alice", predicate="member", object="team",
                ...             agent="registry", polarity="positive", valid=(0, 100))
                ...         _ = graph.create_rule("classify", when=[("?x", "member", "?team")],
                ...             then=("?x", "is", "person"))
                ...         _ = graph.run_rules(as_of=50)
                ...         conclusion = next(graph.iter_claims_as_of(valid_time=50, predicate="is"))
                ...         assert graph.explain_claim(conclusion.logical_id, valid_time=50) is not None
                ...         _ = graph.retract_claim(base.logical_id, valid=(0, 100))
                ...         _ = graph.maintain_truth(as_of=50)
                ...         assert not tuple(graph.iter_claims_as_of(valid_time=50, predicate="is"))
                ...     finally:
                ...         graph.close()

        This API writes derived temporal claims. Standalone ``DatalogEngine``
        returns an in-memory closure instead; use explicit adapters to connect
        that evaluator to database sources and publication.
        """
        return _stored_rules.run_rules(
            self._temporal_reader(), self.commit_versions,
            lambda: self.store, as_of=as_of, system_time=system_time,
            world=world, max_iterations=max_iterations,
            max_derivations=max_derivations,
            max_justifications=max_justifications, index_mode=index_mode,
        )

    def _last_truth_maintenance_instant(self) -> TemporalInstant | None:
        return _truth_maintenance.last_truth_maintenance_instant(
            self.store, self._temporal_reader(), _TRUTH_MAINTENANCE_STATE_KEY)

    def maintain_truth(
        self,
        *,
        as_of=None,
        system_time=None,
        world=None,
        max_iterations=100,
        max_derivations=10_000,
        max_justifications=100_000,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> TruthMaintenanceResult:
        """Incrementally reconcile derived claims with the current rule fixpoint.

        The authoritative base excludes claims emitted by the rule engine, so a
        cycle cannot preserve itself after its final independent support is
        removed, and completed lower strata are rechecked for closed-world
        absence. If ``as_of`` is omitted, the valid time from the most recent
        rule run or maintenance pass is resumed.
        """
        return _truth_maintenance.maintain_truth(
            self._temporal_reader(), self.commit_versions, lambda: self.store,
            self._temporal_write_lock, _TRUTH_MAINTENANCE_STATE_KEY, as_of=as_of,
            system_time=system_time, world=world, max_iterations=max_iterations,
            max_derivations=max_derivations, max_justifications=max_justifications,
            index_mode=index_mode,
        )

    def explain_claim(
        self,
        claim_id,
        *,
        valid_time,
        system_time=None,
        max_depth=100,
        max_nodes=10_000,
    ) -> ClaimExplanation | None:
        """Return a finite derivation graph for a visible historical claim."""
        return _truth_maintenance.explain_claim(
            self._temporal_reader(), self.store, claim_id=claim_id,
            valid_time=valid_time, system_time=system_time,
            max_depth=max_depth, max_nodes=max_nodes,
        )

    def put_node_version(
        self, node: Node, *, valid, version_id=None, metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> NodeVersion:
        """Append an immutable node assertion without changing the current graph."""
        return _temporal_entity_writes.put_node_version(
            self.commit_versions, node, valid=valid, version_id=version_id,
            metadata=metadata, index_mode=index_mode,
        )

    def put_edge_version(
        self, edge: Edge, *, valid, version_id=None, metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> EdgeVersion:
        """Append an immutable edge assertion without changing the current graph."""
        return _temporal_entity_writes.put_edge_version(
            self.commit_versions, edge, valid=valid, version_id=version_id,
            metadata=metadata, index_mode=index_mode,
        )

    def migrate_time_indexed_edges(
        self,
        *,
        delete_legacy: bool = False,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> tuple[EdgeVersion, ...]:
        """Append legacy ``TimeIndexedEdge`` records as temporal edge history.

        Records are grouped by logical edge ID and ordered by timestamp. Each
        record is valid until the next timestamp for that ID; the final record
        has an open end. Existing equivalent assertions from an earlier run are
        skipped, making cleanup retryable after a completed or interrupted
        migration. Changed legacy input after a successful migration is rejected
        instead of creating overlapping history. When requested, legacy
        current-state records are deleted only after the temporal commit is
        visible.

        Returns:
            Newly appended edge versions in migration order.

        Raises:
            TemporalVersionError: If timestamps are duplicated or legacy input
                changed after an earlier migration.
            TemporalCorruptionError: If a legacy record cannot be decoded or its
                timestamp-prefixed key does not match its payload.
        """
        return _temporal_entity_writes.migrate_time_indexed_edges(
            self.store, self.entity_serializer, self._temporal_reader(),
            self.commit_versions, self.delete_edge, metadata=metadata,
            index_mode=index_mode, delete_legacy=delete_legacy,
        )

    def correct_node_version(
        self,
        node: Node,
        *,
        supersedes_version_id: str,
        valid=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> NodeVersion:
        """Append a corrected node payload while preserving the superseded version."""
        return _temporal_entity_writes.correct_node_version(
            self.commit_versions, node, supersedes_version_id=supersedes_version_id,
            valid=valid, version_id=version_id, metadata=metadata, index_mode=index_mode,
        )

    def correct_edge_version(
        self,
        edge: Edge,
        *,
        supersedes_version_id: str,
        valid=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> EdgeVersion:
        """Append a corrected edge payload while preserving the superseded version."""
        return _temporal_entity_writes.correct_edge_version(
            self.commit_versions, edge, supersedes_version_id=supersedes_version_id,
            valid=valid, version_id=version_id, metadata=metadata, index_mode=index_mode,
        )

    def retract_node_version(
        self,
        logical_id: str,
        *,
        valid=None,
        valid_from=None,
        supersedes_version_id=None,
        reason=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> NodeVersion:
        """Append a node retraction over an explicit or inherited interval.

        Raise TemporalVersionError if the node has no asserted temporal history.
        """
        return _temporal_entity_writes.retract_node_version(
            self.commit_versions, logical_id, valid=valid,
            valid_from=valid_from, supersedes_version_id=supersedes_version_id,
            reason=reason, version_id=version_id, metadata=metadata,
            index_mode=index_mode,
        )

    def retract_edge_version(
        self,
        logical_id: str,
        *,
        valid=None,
        valid_from=None,
        supersedes_version_id=None,
        reason=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> EdgeVersion:
        """Append an edge retraction over an explicit or inherited interval.

        Raise TemporalVersionError if the edge has no asserted temporal history.
        """
        return _temporal_entity_writes.retract_edge_version(
            self.commit_versions, logical_id, valid=valid,
            valid_from=valid_from, supersedes_version_id=supersedes_version_id,
            reason=reason, version_id=version_id, metadata=metadata,
            index_mode=index_mode,
        )

    def assert_claim(
        self,
        *,
        subject,
        predicate,
        object,
        polarity,
        agent,
        source=None,
        confidence=None,
        world="default",
        provenance=None,
        object_kind=ClaimObjectKind.ENTITY,
        valid=None,
        valid_from=None,
        valid_to=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> ClaimVersion:
        """Append an immutable sourced assertion or denial."""
        return _temporal_entity_writes.assert_claim(
            self.commit_versions, subject=subject, predicate=predicate,
            object=object, polarity=polarity, agent=agent, source=source,
            confidence=confidence, world=world, provenance=provenance,
            object_kind=object_kind, valid=valid, valid_from=valid_from,
            valid_to=valid_to, version_id=version_id, metadata=metadata,
            index_mode=index_mode,
        )
    def assert_world_accessibility(
        self,
        *,
        agent,
        from_world,
        to_world,
        kind,
        valid=None,
        valid_from=None,
        valid_to=None,
        source=None,
        provenance=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> ClaimVersion:
        """Append a positive temporal accessibility fact for one agent frame."""
        try:
            frame = AccessibilityKind(kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("kind must be 'belief', 'knowledge', or 'modal'") from exc
        return self.assert_claim(
            subject=from_world,
            predicate=ACCESSIBILITY_PREDICATES[frame],
            object=to_world,
            polarity=ClaimPolarity.POSITIVE,
            agent=agent,
            source=source,
            world=from_world,
            provenance={} if provenance is None else provenance,
            valid=valid,
            valid_from=valid_from,
            valid_to=valid_to,
            version_id=version_id,
            metadata=metadata,
            index_mode=index_mode,
        )

    def entails(
        self,
        agent,
        proposition,
        mode,
        *,
        world,
        valid_time,
        system_time=None,
        through_commit=None,
        max_depth=4,
        max_states=1_000,
    ):
        """Evaluate a bounded modal proposition under one pinned temporal view."""
        try:
            operator = ModalOperator(str(mode).upper())
        except ValueError as exc:
            raise ValueError("mode must be BELIEVES, KNOWS, POSSIBLE, or NECESSARY") from exc
        if operator not in {
            ModalOperator.BELIEVES, ModalOperator.KNOWS,
            ModalOperator.POSSIBLE, ModalOperator.NECESSARY,
        }:
            raise ValueError("mode must be BELIEVES, KNOWS, POSSIBLE, or NECESSARY")
        expression = ModalExpression.from_value({
            "operator": operator.value,
            "agent": agent,
            "formula": proposition,
        })
        with self.read_view(
            valid_time=valid_time, system_time=system_time, through_commit=through_commit
        ) as view:
            return evaluate_modal(
                view, expression, world=world, max_depth=max_depth, max_states=max_states
            )

    def correct_claim(
        self,
        claim,
        *,
        supersedes_version_id,
        confidence=_UNSET,
        provenance=_UNSET,
        valid=None,
        valid_from=None,
        valid_to=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> ClaimVersion:
        """Append corrected confidence/provenance for one claim identity.

        Explicit confidence/provenance keywords override the supplied Claim's
        values, or the superseded version's values when given a claim ID.
        Omitted keywords preserve those values; the claim identity cannot change.
        """
        return _temporal_entity_writes.correct_claim(
            self._temporal_reader(), self.commit_versions, claim,
            supersedes_version_id=supersedes_version_id, confidence=confidence,
            provenance=provenance, valid=valid, valid_from=valid_from,
            valid_to=valid_to, version_id=version_id, metadata=metadata,
            index_mode=index_mode,
        )
    def retract_claim(
        self,
        claim_id,
        *,
        valid=None,
        valid_from=None,
        valid_to=None,
        supersedes_version_id=None,
        reason=None,
        version_id=None,
        metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> ClaimVersion:
        """Append a bitemporal retraction without deleting claim history.

        Raise TemporalVersionError if the claim has no asserted temporal history.
        """
        return _temporal_entity_writes.retract_claim(
            self.commit_versions, claim_id, valid=valid,
            valid_from=valid_from, valid_to=valid_to,
            supersedes_version_id=supersedes_version_id, reason=reason,
            version_id=version_id, metadata=metadata, index_mode=index_mode,
        )
    def _claim_valid_interval(valid, valid_from, valid_to, *, required):
        return _temporal_reads.claim_valid_interval(
            valid, valid_from, valid_to, required=required)

    def commit_versions(
        self, writes, *, metadata=None, index_mode=IndexMaintenanceMode.MAINTAIN
    ) -> TemporalCommit:
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
        with self._temporal_writer_guard():
            if getattr(self.store, "supports_transactions", False) and not self._temporal_transaction_bound:
                with self.transaction() as transaction:
                    result = transaction._commit_versions_direct(writes, normalized_metadata, write_mode)
            else:
                result = self._commit_versions_direct(writes, normalized_metadata, write_mode)
        if rebuild_after:
            self.rebuild_temporal_indexes()
        return result

    def _temporal_committer(self) -> _temporal_writes.TemporalCommitter:
        """Bind the temporal write capability to this handle's scope."""
        return _temporal_writes.TemporalCommitter(
            store=self.store,
            entity_serializer=self.entity_serializer,
            reader=self._temporal_reader(),
            clock=self._temporal_clock,
            require_retraction_fn=self._require_temporal_retraction_identities,
        )

    def _commit_versions_direct(self, writes, metadata, index_mode) -> TemporalCommit:
        return self._temporal_committer().commit_direct(writes, metadata, index_mode)

    def _require_temporal_retraction_identities(self, identities) -> None:
        """Validate against published history, independently of deferred indexes.

        Called under the temporal writer guard before reserving a commit ID.
        Scan once for all unresolved identities in the batch; unpublished records
        and prior retraction-only history cannot establish an identity.
        """
        _temporal_writes.require_retraction_identities(self._temporal_reader(), identities)

    def _temporal_version_id_exists(self, version_id: str) -> bool:
        return self._temporal_committer().version_id_exists(version_id)

    def _temporal_reader(self) -> _temporal_reads.TemporalReader:
        """Bind the historical read capability to this handle's scope."""
        return _temporal_reads.TemporalReader(
            store=self.store,
            entity_serializer=self.entity_serializer,
            commit_cache=self._temporal_commit_cache,
            sequence_key=_TEMPORAL_SEQUENCE_KEY,
            store_path=self._store_path,
            sequence_filename=_TEMPORAL_SEQUENCE_FILENAME,
            state_key=_TEMPORAL_INDEX_STATE_KEY,
            stale_key=_STALE_INDEXES_METADATA_KEY,
        )

    def get_temporal_commit(
        self, commit_id: int, *, _validate_supersession: bool = True
    ) -> TemporalCommit | None:
        """Return a validated visible commit with detached mutable entities.

        Decode reuse is bounded and scoped to the exact store object. Pinned
        verifiable read snapshots can reuse validated records directly;
        live/unverifiable stores recheck descriptor, marker and envelopes.
        """
        return self._temporal_reader().get_commit(
            commit_id, _validate_supersession=_validate_supersession)

    @staticmethod
    def _copy_temporal_payload(version):
        return _temporal_reads.TemporalReader.copy_payload(version)

    def _get_temporal_commit_cached(
        self, commit_id: int, *, _validate_supersession: bool = True
    ) -> TemporalCommit | None:
        return self._temporal_reader().get_commit_cached(
            commit_id, _validate_supersession=_validate_supersession)

    def _decode_temporal_version(self, envelope: bytes):
        return self._temporal_reader().decode_version(envelope)

    def iter_temporal_commits(self, *, through_commit=None):
        """Iterate visible commits in commit order, skipping incomplete commits."""
        return self._temporal_reader().iter_commits(through_commit=through_commit)

    def _temporal_orphan_details(self, commit_id: int, descriptor_payload: bytes):
        return _temporal_orphans.orphan_details(
            self.store, self._temporal_reader().decode_version,
            commit_id, descriptor_payload,
        )

    def _list_temporal_orphans_locked(self, through_commit=None) -> TemporalOrphanReport:
        return _temporal_orphans.list_orphans_locked(
            self.store, self._temporal_reader(), through_commit)

    def list_temporal_orphans(self, *, through_commit=None) -> TemporalOrphanReport:
        """List durable allocation gaps and incomplete unpublished commits."""
        with self._temporal_writer_guard():
            return self._list_temporal_orphans_locked(through_commit)

    def reclaim_temporal_orphans(self, *, through_commit: int) -> TemporalOrphanReclaimResult:
        """Delete validated unpublished artifacts through an explicit commit horizon."""
        with self._temporal_writer_guard():
            if getattr(self.store, "supports_transactions", False) and not self._temporal_transaction_bound:
                with self.transaction() as transaction:
                    return transaction._reclaim_temporal_orphans_locked(through_commit)
            return self._reclaim_temporal_orphans_locked(through_commit)

    def _reclaim_temporal_orphans_locked(self, through_commit: int) -> TemporalOrphanReclaimResult:
        return _temporal_orphans.reclaim_orphans_locked(
            self.store, self._temporal_reader(), _STALE_INDEXES_METADATA_KEY,
            self.rebuild_temporal_indexes, through_commit,
        )

    def _get_temporal_version(self, version_id: str):
        return self._temporal_reader().get_version(version_id)

    def _get_temporal_version_at(self, locator: bytes):
        return self._temporal_reader().get_version_at(locator)

    def _get_temporal_version_through(self, version_id: str, through_commit: int):
        return self._temporal_reader().get_version_through(version_id, through_commit)

    def get_node_version(
        self, version_id: str, *, system_time=None, through_commit=None
    ) -> NodeVersion | None:
        """Return a visible node version by UUID using the temporal index."""
        return self._temporal_reader().get_node_version(
            version_id, system_time=system_time, through_commit=through_commit)

    def get_edge_version(
        self, version_id: str, *, system_time=None, through_commit=None
    ) -> EdgeVersion | None:
        """Return a visible edge version by UUID using the temporal index."""
        return self._temporal_reader().get_edge_version(
            version_id, system_time=system_time, through_commit=through_commit)

    def get_claim_version(
        self, version_id: str, *, system_time=None, through_commit=None
    ) -> ClaimVersion | None:
        """Return a visible epistemic claim version by UUID."""
        return self._temporal_reader().get_claim_version(
            version_id, system_time=system_time, through_commit=through_commit)

    def iter_node_versions(self, logical_id=None, *, system_time=None, through_commit=None):
        """Iterate node versions, using the logical-history index when filtered."""
        if logical_id is not None:
            logical_id = normalize_logical_id(logical_id)
            horizon = self._temporal_system_horizon(system_time=system_time, through_commit=through_commit)
            self._ensure_temporal_indexes(horizon)
            end = self._temporal_locator(horizon, (1 << 32) - 1)
            for locator in self.store.iter_range_index(
                self._temporal_index_name(
                    _TEMPORAL_LOGICAL_COMMIT_INDEX, self._temporal_index_generation()
                ),
                [b"n", logical_id.encode("utf-8")],
                None,
                end,
                True,
                True,
            ):
                version = self._get_temporal_version_at(locator)
                if isinstance(version, NodeVersion):
                    yield version
            return
        horizon = self._temporal_system_horizon(system_time=system_time, through_commit=through_commit)
        for commit in self.iter_temporal_commits(through_commit=horizon):
            for version in commit.versions:
                if isinstance(version, NodeVersion):
                    yield version

    def iter_edge_versions(self, logical_id=None, *, system_time=None, through_commit=None):
        """Iterate edge versions, using the logical-history index when filtered."""
        if logical_id is not None:
            logical_id = normalize_logical_id(logical_id)
            horizon = self._temporal_system_horizon(system_time=system_time, through_commit=through_commit)
            self._ensure_temporal_indexes(horizon)
            end = self._temporal_locator(horizon, (1 << 32) - 1)
            for locator in self.store.iter_range_index(
                self._temporal_index_name(
                    _TEMPORAL_LOGICAL_COMMIT_INDEX, self._temporal_index_generation()
                ),
                [b"e", logical_id.encode("utf-8")],
                None,
                end,
                True,
                True,
            ):
                version = self._get_temporal_version_at(locator)
                if isinstance(version, EdgeVersion):
                    yield version
            return
        horizon = self._temporal_system_horizon(system_time=system_time, through_commit=through_commit)
        for commit in self.iter_temporal_commits(through_commit=horizon):
            for version in commit.versions:
                if isinstance(version, EdgeVersion):
                    yield version

    def iter_claim_versions(self, claim_id=None, *, system_time=None, through_commit=None):
        """Iterate claim versions, optionally restricted to one deterministic claim ID."""
        if claim_id is not None:
            claim_id = normalize_logical_id(claim_id)
            horizon = self._temporal_system_horizon(
                system_time=system_time, through_commit=through_commit
            )
            self._ensure_temporal_indexes(horizon)
            end = self._temporal_locator(horizon, (1 << 32) - 1)
            for locator in self.store.iter_range_index(
                self._temporal_index_name(
                    _TEMPORAL_LOGICAL_COMMIT_INDEX, self._temporal_index_generation()
                ),
                [b"c", claim_id.encode("utf-8")],
                None,
                end,
                True,
                True,
            ):
                version = self._get_temporal_version_at(locator)
                if isinstance(version, ClaimVersion):
                    yield version
            return
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        for commit in self.iter_temporal_commits(through_commit=horizon):
            for version in commit.versions:
                if isinstance(version, ClaimVersion):
                    yield version

    def _temporal_system_horizon(self, *, system_time=None, through_commit=None) -> int:
        return self._temporal_reader().system_horizon(
            system_time=system_time, through_commit=through_commit)

    def _temporal_interval_locators(self, index_name, parts, instant):
        return self._temporal_reader().interval_locators(index_name, parts, instant)

    def _temporal_entity_as_of(
        self, kind, logical_id, *, valid_time, system_time=None, through_commit=None
    ):
        return self._temporal_reader().entity_as_of(
            kind, logical_id, valid_time=valid_time, system_time=system_time,
            through_commit=through_commit,
        )

    def get_node_as_of(
        self, logical_id, *, valid_time, system_time=None, through_commit=None
    ) -> NodeVersion | None:
        """Resolve a logical node at valid time and an optional system horizon."""
        return self._temporal_reader().entity_as_of(
            "node", logical_id, valid_time=valid_time, system_time=system_time,
            through_commit=through_commit, hydrate=self._get_temporal_version_at,
        )

    def get_edge_as_of(
        self, logical_id, *, valid_time, system_time=None, through_commit=None
    ) -> EdgeVersion | None:
        """Resolve a logical edge at valid time and an optional system horizon."""
        return self._temporal_reader().entity_as_of(
            "edge", logical_id, valid_time=valid_time, system_time=system_time,
            through_commit=through_commit, hydrate=self._get_temporal_version_at,
        )

    def get_claim_as_of(
        self, claim_id, *, valid_time, system_time=None, through_commit=None
    ) -> ClaimVersion | None:
        """Resolve one claim identity at valid time and an optional system horizon."""
        return self._temporal_reader().entity_as_of(
            "claim", claim_id, valid_time=valid_time, system_time=system_time,
            through_commit=through_commit, hydrate=self._get_temporal_version_at,
        )

    def iter_claims_as_of(
        self,
        *,
        valid_time,
        statement_id=None,
        subject=None,
        predicate=None,
        object=_UNSET,
        object_kind=None,
        agent=None,
        source=_UNSET,
        world=None,
        polarity=None,
        system_time=None,
        through_commit=None,
    ):
        """Iterate visible claims matching indexed epistemic dimensions.

        Locator reads are grouped by commit, with full commit validation per
        group rather than per row. Winners are buffered at one logical horizon.
        Live-store commit integrity is checked again on successful exhaustion;
        partially consumed iterators are not completed authenticated reads.
        """
        return self._temporal_reader().iter_claims_as_of(
            valid_time=valid_time, statement_id=statement_id, subject=subject,
            predicate=predicate, object=object, object_kind=object_kind,
            agent=agent, source=source, world=world, polarity=polarity,
            system_time=system_time, through_commit=through_commit,
            live_store=lambda: self.store,
        )

    def claim_status(
        self,
        subject,
        predicate,
        object,
        *,
        valid_time,
        object_kind=ClaimObjectKind.ENTITY,
        agent=None,
        source=_UNSET,
        world=None,
        system_time=None,
        through_commit=None,
    ) -> ClaimStatus:
        """Return supported/refuted/both/unknown under open-world semantics."""
        return self._temporal_reader().claim_status(
            subject, predicate, object, valid_time=valid_time,
            object_kind=object_kind, agent=agent, source=source, world=world,
            system_time=system_time, through_commit=through_commit,
        )

    def iter_nodes_as_of(
        self, *, valid_time, system_time=None, through_commit=None
    ):
        """Iterate node versions visible at one valid and system-time point."""
        return self._temporal_reader().iter_nodes_as_of(
            valid_time=valid_time, system_time=system_time,
            through_commit=through_commit,
            hydrate=self._get_temporal_version_at,
        )

    def iter_edges_as_of(
        self,
        *,
        edge_type=None,
        direction="out",
        source=None,
        target=None,
        valid_time,
        system_time=None,
        through_commit=None,
    ):
        """Iterate endpoint edges visible at valid time and a system horizon."""
        return self._temporal_reader().iter_edges_as_of(
            valid_time=valid_time, system_time=system_time,
            through_commit=through_commit, edge_type=edge_type,
            direction=direction, source=source, target=target,
            hydrate=self._get_temporal_version_at,
        )

    def rebuild_temporal_indexes(self, *, through_commit=None) -> dict[str, int]:
        """Rebuild all derived temporal indexes from visible canonical history."""
        with self._temporal_writer_guard():
            return _temporal_indexes.rebuild_temporal_indexes(
                self.store, self._temporal_reader(), _TEMPORAL_INDEX_STATE_KEY,
                _STALE_INDEXES_METADATA_KEY,
                write_indexes_fn=self._write_temporal_indexes,
                through_commit=through_commit,
            )

    def _delete_temporal_index_generation(self, generation: int, through_commit: int) -> None:
        _temporal_indexes.delete_temporal_index_generation(
            self.store, self._temporal_reader(), generation, through_commit)

    def query(
        self,
        cypher: str,
        parameters: Optional[dict[str, object]] = None,
        *,
        read_snapshot: bool | None = None,
        label_hierarchy=None,
        relation_hierarchy=None,
    ):
        """Execute a supported Cypher query.

        Read queries run directly; queries with ``CREATE``/``SET``/``REMOVE``
        run inside :meth:`transaction` when the backend supports it and fall
        back to best-effort direct writes otherwise.

        Args:
            cypher: Query text in the supported GestaltDB Cypher subset.
            parameters: Optional Cypher parameter values keyed without the
                leading ``$``.
            read_snapshot: Require or disable a unified mutable-state read
                snapshot. The default uses one automatically when supported.
            label_hierarchy: Optional ``LabelHierarchy``. When supplied, a
                required label also matches nodes carrying any transitive
                sub-type label, including in label expressions, endpoint
                filters and indexed lookups. Default matching stays exact.
            relation_hierarchy: Optional ``RelationHierarchy``. When supplied,
                a required relationship type also matches edges carrying any
                transitive sub-property type, including property maps,
                variable-length paths, and index-backed lookups. Default
                matching stays exact.

        Returns:
            ``gestaltdb.QueryResult`` containing projected records.

        Examples:
            >>> graph_db.query('MATCH (n:Drug) RETURN n')  # doctest: +SKIP
            >>> graph_db.query('MATCH (a {id: "drug-1"})-[:drug-to-protein]->(b) RETURN a, b')  # doctest: +SKIP
        """
        from .query_engine.cypher import execute

        return execute(
            self,
            cypher,
            parameters=parameters,
            read_snapshot=read_snapshot,
            label_hierarchy=label_hierarchy,
            relation_hierarchy=relation_hierarchy,
        )

    def visualize(self, cypher=None, *, seeds=None, pattern=None, parameters=None, options=None, rng=None):
        """Build an offline interactive visualization of this graph.

        The front-end bundle is prebuilt and packaged with the library, so
        this works with no JavaScript toolchain and no network access. The
        returned figure saves self-contained ``.html`` artifacts and renders
        inline in Jupyter.

        Args:
            cypher: Optional Cypher query whose matched entities are
                visualized (with query matches highlighted).
            seeds: Optional seed node IDs for typed-subgraph sampling; requires
                ``pattern``.
            pattern: ``SamplingPattern`` or hop dicts used with ``seeds``.
            parameters: Optional Cypher parameters for ``cypher``.
            options: Optional ``gestaltdb.viz.api.VizOptions`` or mapping.
            rng: Optional random generator for sampling mode.

        Returns:
            ``gestaltdb.viz.api.VizFigure``.

        Examples:
            >>> figure = graph_db.visualize('MATCH (a:Person) RETURN a LIMIT 25')  # doctest: +SKIP
            >>> figure.save("/tmp/people.html")  # doctest: +SKIP
        """
        from .viz.api import visualize_query, visualize_sample

        if cypher is not None:
            if seeds is not None or pattern is not None:
                raise ValueError("visualize() accepts either cypher= or seeds=/pattern=, not both")
            return visualize_query(self, cypher, parameters=parameters, options=options)
        if seeds is not None or pattern is not None:
            if pattern is None:
                raise ValueError("visualize() with seeds= requires pattern=")
            return visualize_sample(self, seeds, pattern, rng=rng, options=options)
        raise ValueError("visualize() requires cypher= or seeds=/pattern=")

    def _implicit_transaction_required(self) -> bool:
        return bool(
            getattr(self.store, "supports_transactions", False)
            and not self._temporal_transaction_bound
        )

    @contextmanager
    def transaction(self, **options):
        """Run graph operations in a backend transaction when supported.

        The transaction commits on clean context exit and rolls back if an
        exception leaves the context.
        """
        with self._temporal_writer_guard():
            tx_store = self.store.transaction(**options)
            try:
                tx_graph = GraphDB(tx_store, self.serializer)
                configure_transaction_child(self, tx_graph)
                yield tx_graph
            except Exception:
                tx_store.rollback()
                raise
            else:
                tx_store.commit()
                self._typed_adjacency_count_cache.clear()

    def close(self):
        """Close the underlying key-value store.

        Examples:
            >>> graph_db.close()  # doctest: +SKIP
        """
        self.store.close()
        
