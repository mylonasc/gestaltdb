
from __future__ import annotations

import pickle
import json
import hashlib
import copy
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
from ._graph import index_maintenance as _index_maintenance
from ._graph import edge_records as _edge_records
from ._graph import index_lookup as _index_lookup
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
from ._graph import temporal_selection as _temporal_selection
from ._graph.temporal_selection import _UNSET
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
from .concurrency import DEFAULT_LOCK_MANAGER, ReentrantFileLock as _TemporalWriterLock
from .temporal_engine import TemporalEngine
from .epistemic_engine import EpistemicEngine
from .rules_engine import RulesEngine

_NODE_PROPERTY_INDEXES_METADATA_KEY = b"schema:indexes:node_properties"
_EDGE_PROPERTY_INDEXES_METADATA_KEY = b"schema:indexes:edge_properties"
_NODE_CONSTRAINTS_METADATA_KEY = b"schema:constraints:nodes"
_STALE_INDEXES_METADATA_KEY = b"schema:indexes:stale"
_MANIFEST_METADATA_KEY = b"schema:manifest"
_DATABASE_ID_METADATA_KEY = b"schema:database_identity:v1"
_INDEX_REBUILD_BATCH_SIZE = 100_000
_TEMPORAL_WRITER_LOCK_FILENAME = ".gestaltdb_temporal_writer.lock"


def _temporal_writer_lock(path: Path) -> _TemporalWriterLock:
    return DEFAULT_LOCK_MANAGER.for_path(path / _TEMPORAL_WRITER_LOCK_FILENAME)


# Entity/value helpers live in `gestaltdb._graph.entities`; re-exported above.


class GraphDB:
    """Persistent current-graph records, indexes, queries, and scoped services.

    Choose an installed backend and serializer with :meth:`create`; :meth:`open`
    restores those choices from the database manifest. Always close the handle.

    Temporal history lives on ``graph.temporal``, sourced claims and modal
    evaluation on ``graph.epistemic``, and Horn rules on ``graph.rules``.
    Those services own their state and operations; this class keeps no
    wrapper methods for them. Ordinary node/edge writes stay here.

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
        self._store = store
        self.serializer = serializer
        store_path = getattr(store, "path", None)
        self._store_path: Path | None = None if store_path is None else Path(store_path)
        self._backend_name: str | None = _registry_name_for_instance(store, _backend_registry())
        self._serializer_name: str | None = _registry_name_for_instance(serializer, _serializer_registry())
        self._manifest: dict | None = None
        self._database_id: str | None = None
        self._lock_manager = DEFAULT_LOCK_MANAGER
        self.entity_serializer = GraphEntityDictSerializer(
            self.serializer
        )
        self.temporal = TemporalEngine(
            store=self.store,
            entity_serializer=self.entity_serializer,
            clock=lambda: datetime.datetime.now(datetime.timezone.utc),
            store_path=self._store_path,
        )
        self.epistemic = EpistemicEngine(self.temporal)
        self.rules = RulesEngine(self.temporal)
        self._typed_adjacency_count_cache: dict[tuple[str, bytes, str], int] = {}
        self._verified_current_provenance: dict[str, str] = {}
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
    def store(self):
        """The bound backend store; assigning rebinds scope-owned engines."""
        return self._store

    @store.setter
    def store(self, value) -> None:
        self._store = value
        temporal = getattr(self, "temporal", None)
        if temporal is None:
            return
        path = getattr(value, "path", None)
        self.temporal = temporal.rebound(
            value, store_path=None if path is None else Path(path))
        self.epistemic = self.epistemic.rebound(self.temporal)
        self.rules = self.rules.rebound(self.temporal)
        self._node_repository = None
        self._edge_repository = None

    @property
    def database_id(self) -> str:
        """Return the stable UUID persisted with this database."""
        if self._database_id is not None:
            return self._database_id
        return self._initialize_database_id(None)

    def _initialize_database_id(self, expected: str | None) -> str:
        """Persist or reconcile the database UUID under a local filesystem lock."""
        value = _lifecycle.initialize_database_id(
            self.store, self._store_path, self._lock_manager,
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
        return _lifecycle.manifest_from_current(
            self.store, backend_name=backend_name, serializer_name=serializer_name,
            database_id=self.database_id,
            indexed_node_properties=self.indexed_node_properties,
            indexed_edge_properties=self.indexed_edge_properties,
            created_at=created_at,
        )

    def _validate_backend_manifest(self, root_manifest: dict) -> None:
        _lifecycle.validate_backend_manifest(
            self.store, _MANIFEST_METADATA_KEY, self.database_id, root_manifest)

    def _mark_indexes_stale(self, *index_names: str) -> None:
        """Mark index families stale after a deferred bulk load."""
        _index_maintenance.mark_indexes_stale(self.store, _STALE_INDEXES_METADATA_KEY, *index_names)

    def stale_indexes(self) -> tuple[str, ...]:
        """Return index families requiring rebuild after deferred ingestion."""
        return self._ingestion_coordinator.stale_indexes()

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
    @property
    def nodes(self):
        """Current-node repository; see the architecture guide for standalone use.

        Borrows this handle's lifetime and store scope. Temporal records and
        schema changes remain separate capabilities.
        """
        from .current import bind_nodes

        self._node_repository = bind_nodes(getattr(self, "_node_repository", None),
            self.store, self.serializer, entity_serializer=self.entity_serializer,
            indexed_node_properties=self.indexed_node_properties,
            indexed_edge_properties=self.indexed_edge_properties,
            adjacency_counts=self._typed_adjacency_count_cache,
            transaction_bound=self.temporal.is_transaction_bound,
            write_guard=lambda: self.temporal.write_guard(), lock_manager=self._lock_manager,
        )
        return self._node_repository

    def put_node(self, node: Node):
        """Store a node.

        Args:
            node: Node to serialize and write.

        Examples:
            >>> graph_db.put_node(Node(node_id="drug-1"))  # doctest: +SKIP
        """
        return self.nodes.put(node)

    def get_node(self, node_id) -> Node:
        """Return a node by byte key.

        Args:
            node_id: Node ID bytes as stored in the backend.

        Returns:
            The decoded node, or ``None`` when absent.

        Examples:
            >>> graph_db.get_node(b"drug-1")  # doctest: +SKIP
        """
        return self.nodes.get(node_id)

    def delete_node(self, node_id):
        """Delete a node by byte key.

        Args:
            node_id: Node ID bytes.

        Examples:
            >>> graph_db.delete_node(b"drug-1")  # doctest: +SKIP
        """
        return self.nodes.delete(node_id)

    def _put_node_indexes(self, node: Node):
        """Maintain label and configured property indexes for a node."""
        _index_maintenance.put_node_indexes(self.store, self.indexed_node_properties, node)

    def _delete_node_indexes(self, node: Node):
        """Remove label and configured property indexes for a node."""
        _index_maintenance.delete_node_indexes(self.store, self.indexed_node_properties, node)

    def _flush_index_rebuild_batches(self, entries: list, range_entries: list) -> None:
        """Write accumulated rebuild entries and clear the buffers."""
        _index_maintenance.flush_index_rebuild_batches(self.store, entries, range_entries)

    @property
    def indexes(self):
        """Current index lookup, DDL and rebuild authority for this store scope."""
        from .indexes import IndexService

        return IndexService(self.nodes._session)

    def rebuild_node_indexes(
        self,
        *,
        labels: bool = True,
        properties: list[str] | tuple[str, ...] | set[str] | None = None,
        batch_size: int = _INDEX_REBUILD_BATCH_SIZE,
    ) -> dict[str, int]:
        """Rebuild requested node secondary indexes in one node scan."""
        return self.indexes.rebuild_node_indexes(
            labels=labels, properties=properties, batch_size=batch_size)

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
        return self.indexes.create_node_property_index(property_name)

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
        return self.indexes.rebuild_node_property_index(property_name)

    def rebuild_label_index(self):
        """Rebuild the node label index from stored nodes.

        Returns:
            Number of label index entries written.

        Examples:
            >>> graph_db.rebuild_label_index()  # doctest: +SKIP
            12
        """
        return self.indexes.rebuild_label_index()

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
        return self.indexes.iter_node_ids_by_label(label)

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
        return self.indexes.iter_node_ids_by_property(property_name, value)

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
        return self.indexes.iter_node_ids_by_label_property(label, property_name, value)

    def nodes_by_label_property(self, label: str, property_name: str, value):
        """Return nodes using the composite label/property exact-match index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_label_property(label, property_name, value)]

    def count_nodes_by_label_property(self, label: str, property_name: str, value) -> int:
        """Return the number of nodes indexed for a label and exact property value."""
        return sum(1 for _ in self.iter_node_ids_by_label_property(label, property_name, value))

    def iter_node_ids_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield node IDs from a scalar property range index."""
        return self.indexes.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)

    def nodes_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return nodes using a scalar property range index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)]

    def count_nodes_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of nodes indexed in a scalar property range."""
        return sum(1 for _ in self.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end))

    def iter_node_ids_by_label_property_range(self, label: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield node IDs from a composite label/property range index."""
        return self.indexes.iter_node_ids_by_label_property_range(label, property_name, start_value, end_value, include_start, include_end)

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
    @property
    def edges(self):
        """Current-edge repository bound to this handle's store and lifetime."""
        from .current import bind_edges

        self._edge_repository = bind_edges(getattr(self, "_edge_repository", None),
            self.store, self.serializer, entity_serializer=self.entity_serializer,
            indexed_node_properties=self.indexed_node_properties,
            indexed_edge_properties=self.indexed_edge_properties,
            adjacency_counts=self._typed_adjacency_count_cache,
            transaction_bound=self.temporal.is_transaction_bound,
            write_guard=lambda: self.temporal.write_guard(), lock_manager=self._lock_manager,
        )
        return self._edge_repository

    def put_edge(self, edge: Edge, update_adjacency = True):
        """Store an edge and update adjacency indexes.

        Args:
            edge: Edge to serialize and write.
            update_adjacency: Whether to update the legacy untyped adjacency list.

        Examples:
            >>> graph_db.put_edge(Edge(source="drug-1", target="protein-1"))  # doctest: +SKIP
        """
        return self.edges.put(edge, update_adjacency=update_adjacency)

    def rebuild_edge_indexes(
        self,
        *,
        edge_types: bool = True,
        properties: list[str] | tuple[str, ...] | set[str] | None = None,
        batch_size: int = _INDEX_REBUILD_BATCH_SIZE,
    ) -> dict[str, int]:
        """Rebuild requested edge secondary indexes in one edge scan."""
        return self.indexes.rebuild_edge_indexes(
            edge_types=edge_types, properties=properties, batch_size=batch_size)

    def rebuild_relationship_type_index(self):
        """Rebuild the relationship type catalog from stored edges.

        Returns:
            Number of typed edge records indexed.

        Examples:
            >>> graph_db.rebuild_relationship_type_index()  # doctest: +SKIP
            20
        """
        return self.indexes.rebuild_relationship_type_index()

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
        return self.indexes.create_edge_property_index(property_name)

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
        return self.indexes.rebuild_edge_property_index(property_name)

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
        return self.indexes.iter_edge_ids_by_type(edge_type)

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
        return self.indexes.iter_edge_ids_by_property(property_name, value)

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
        return self.indexes.iter_edge_ids_by_type_property(edge_type, property_name, value)

    def edges_by_type_property(self, edge_type: str, property_name: str, value):
        """Return edges using the composite type/property exact-match index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_type_property(edge_type, property_name, value)]

    def count_edges_by_type_property(self, edge_type: str, property_name: str, value) -> int:
        """Return the number of edges indexed for a type and exact property value."""
        return sum(1 for _ in self.iter_edge_ids_by_type_property(edge_type, property_name, value))

    def iter_edge_ids_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield edge IDs from a scalar property range index."""
        return self.indexes.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)

    def edges_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return edges using a scalar property range index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)]

    def count_edges_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of edges indexed in a scalar property range."""
        return sum(1 for _ in self.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end))

    def iter_edge_ids_by_type_property_range(self, edge_type: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield edge IDs from a composite type/property range index."""
        return self.indexes.iter_edge_ids_by_type_property_range(edge_type, property_name, start_value, end_value, include_start, include_end)

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
        return self._ingestion_coordinator.rebuild_deferred_indexes()

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
            with self.temporal.open_read_view(
                self, manifest=self.manifest, database_id=self.database_id,
                valid_time=0, system_time=system_time,
            ) as view:
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

    @property
    def traversal(self):
        """Typed adjacency and live sampling bound to this current store scope."""
        from .traversal import TraversalService

        return TraversalService(self.nodes._session)

    def get_typed_adjacency(self, node_id, edge_type: str, direction: str = 'out'):
        """Return typed adjacency records with clean direction semantics.

        `out` means source -> target, `in` means target -> source, and `any`
        returns the union of both directions.
        """
        return self.traversal.get_typed_adjacency(node_id, edge_type, direction)

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
        return self.traversal.count_typed_adjacency(node_id, edge_type, direction)

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
        return self.traversal.iter_typed_adjacency(node_id, edge_type, direction)

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
        return self.traversal.neighbors_by_edge_type(node_id, edge_type, direction)

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
        return self.traversal.edges_by_edge_type(node_id, edge_type, direction)

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
        return self.traversal.sample_nodes(
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
        return self.traversal.sample_neighbors(
            node_id, edge_type, direction=direction,
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
        return self.traversal.sample_typed_paths(
            seed_ids, pattern, rng=rng, relation_hierarchy=relation_hierarchy,
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
        return self.traversal.sample_typed_subgraph(
            seed_ids, pattern, rng=rng, relation_hierarchy=relation_hierarchy,
        )

    def rebuild_typed_adjacency(self):
        """Rebuild typed adjacency indexes from stored edge records.

        Returns:
            Number of typed edges indexed.

        Examples:
            >>> graph_db.rebuild_typed_adjacency()  # doctest: +SKIP
        """
        return self.traversal.rebuild_typed_adjacency()

    def get_edge(self, edge_id) -> Edge:
        """Return an edge by byte key.

        Args:
            edge_id: Edge ID bytes as stored in the backend.

        Returns:
            The decoded edge, or ``None`` when absent.

        Examples:
            >>> graph_db.get_edge(b"d1-p1")  # doctest: +SKIP
        """
        return self.edges.get(edge_id)

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
        return self.nodes.update(node_id, new_data, merge_func)

    def update_edge(self, edge_id: str, new_data: dict, merge_func) -> Edge:
        """
        Similar approach for edges. The new_data might include new properties,
        or you might also allow changing source/target if that makes sense.
        """
        return self.edges.update(edge_id, new_data, merge_func)

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
        return self.nodes.put_many(nodes)

    @property
    def ingest(self):
        """Arrow/Polars ingestion capabilities bound to this current store scope."""
        from .ingestion_service import IngestionService

        return IngestionService(self.nodes._session)

    @property
    def _ingestion_coordinator(self):
        """Graph-wide loading/rebuild coordination for this exact scope."""
        from .ingestion_service import IngestionCoordinator

        return IngestionCoordinator(self.nodes._session, temporal=self.temporal)

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
        return self._ingestion_coordinator.ingest_polars(
            node_df, edge_df,
            ingestion_mode=ingestion_mode, index_mode=index_mode,
            node_id=node_id, node_value=node_value, labels=labels,
            node_property_columns=node_property_columns,
            edge_id=edge_id, source=source, target=target,
            edge_type=edge_type, edge_value=edge_value,
            edge_property_columns=edge_property_columns,
            native=native, chunk_size=chunk_size, progress=progress,
        )

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
        return self._ingestion_coordinator.ingest_arrow(
            node_ids, edge_ids, sources, targets, edge_types,
            ingestion_mode=ingestion_mode, index_mode=index_mode,
            node_values=node_values, edge_values=edge_values, labels=labels,
            node_properties=node_properties, edge_properties=edge_properties,
            native=native, chunk_size=chunk_size, progress=progress,
        )

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
        return self.ingest.arrow.nodes(
            node_ids, node_values, native=native,
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
        return self.ingest.polars.nodes(
            df, node_id=node_id, node_value=node_value,
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
        return self.ingest.polars.node_entities(
            df, node_id=node_id, labels=labels,
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
        return self.ingest.arrow.node_entities(
            node_ids, labels=labels, properties=properties, native=native,
            chunk_size=chunk_size, append_only=append_only, index_mode=index_mode,
            progress=progress,
        )

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
        return self.nodes.get_many(node_ids)
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
        return self.nodes.keys(num_nodes, key_offset)

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
        return self.edges.delete(edge_id)

    # -----------------------
    # BFS Example
    # -----------------------
    def bfs(self, start_node_id: bytes, direction = 'any', edge_key_serializer = lambda x : x.encode('utf-8'), node_key_serializer = lambda x : x.encode('utf-8')) -> list[str]:
        """
        Returns a list of node_ids in BFS order starting from `start_node_id`.
        Demonstrates how adjacency is used for graph traversal.
        """
        return _edge_records.bfs(
            self.store, self.entity_serializer, self.serializer,
            start_node_id, direction=direction,
            edge_key_serializer=edge_key_serializer,
            node_key_serializer=node_key_serializer,
        )

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
        return self.edges.put_many(edges, check_existing=check_existing)

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
        return self.ingest.arrow.edges(
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
        return self.ingest.polars.edges(
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
        return self.ingest.polars.edge_entities(
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
        return self.ingest.arrow.edge_entities(
            edge_ids, sources, targets, edge_types, properties=properties,
            append_only=append_only, native=native, chunk_size=chunk_size,
            index_mode=index_mode, progress=progress,
        )

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
            and not self.temporal.is_transaction_bound
        )

    @contextmanager
    def transaction(self, **options):
        """Run graph operations in a backend transaction when supported.

        The transaction commits on clean context exit and rolls back if an
        exception leaves the context.
        """
        with self.temporal.write_guard():
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
