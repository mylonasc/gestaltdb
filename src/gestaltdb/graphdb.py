
from __future__ import annotations

import pickle
import json
import hashlib
import os
import random
import shutil
import sys
import threading
import time
import uuid
import base64
from collections.abc import Mapping
from contextlib import contextmanager
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

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
    RuleError,
    RuleEvaluationLimitError,
    RuleJustification,
    RuleRunResult,
    RuleVersion,
    TruthMaintenanceResult,
    normalize_rule_definition,
)
from .sampling import SamplingPattern, as_sampling_pattern
from .serializers import JSONSerializer
from .temporal import TemporalInstant, TemporalInterval
from .versioning import (
    ClaimVersion,
    ClaimVersionWrite,
    EdgeVersion,
    EdgeVersionWrite,
    NodeVersion,
    NodeVersionWrite,
    TemporalCommit,
    TemporalCorruptionError,
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


_NODE_PROPERTY_INDEXES_METADATA_KEY = b"schema:indexes:node_properties"
_EDGE_PROPERTY_INDEXES_METADATA_KEY = b"schema:indexes:edge_properties"
_NODE_CONSTRAINTS_METADATA_KEY = b"schema:constraints:nodes"
_STALE_INDEXES_METADATA_KEY = b"schema:indexes:stale"
_MANIFEST_METADATA_KEY = b"schema:manifest"
_DATABASE_ID_METADATA_KEY = b"schema:database_identity:v1"
MANIFEST_FILENAME = "gestaltdb_manifest.json"
MANIFEST_FORMAT_VERSION = 1
_VALID_INDEX_MODES = {IndexMaintenanceMode.MAINTAIN.value, IndexMaintenanceMode.DEFER.value}
_INDEX_REBUILD_BATCH_SIZE = 100_000
_TEMPORAL_PREFIX = b"temporal:v1:"
_TEMPORAL_SEQUENCE_KEY = _TEMPORAL_PREFIX + b"sequence"
_TEMPORAL_COMMIT_PREFIX = _TEMPORAL_PREFIX + b"commit:"
_TEMPORAL_RECORD_PREFIX = _TEMPORAL_PREFIX + b"record:"
_TEMPORAL_VISIBLE_PREFIX = _TEMPORAL_PREFIX + b"visible:"
_TEMPORAL_INDEX_STATE_KEY = b"temporal:indexes:v1:state"
_TEMPORAL_VERSION_INDEX = "temporal_v1_version"
_TEMPORAL_LOGICAL_COMMIT_INDEX = "temporal_v1_logical_commit"
_TEMPORAL_LOGICAL_VALID_INDEX = "temporal_v1_logical_valid"
_TEMPORAL_SYSTEM_INDEX = "temporal_v1_system_commit"
_TEMPORAL_EDGE_OUT_INDEX = "temporal_v1_edge_out"
_TEMPORAL_EDGE_IN_INDEX = "temporal_v1_edge_in"
_TEMPORAL_NODE_CATALOG_INDEX = "temporal_v1_node_catalog"
_TEMPORAL_EDGE_OUT_CATALOG_INDEX = "temporal_v1_edge_out_catalog"
_TEMPORAL_EDGE_IN_CATALOG_INDEX = "temporal_v1_edge_in_catalog"
_TEMPORAL_CLAIM_CATALOG_INDEX = "temporal_v1_claim_catalog"
_TEMPORAL_CLAIM_STATEMENT_INDEX = "temporal_v1_claim_statement"
_TEMPORAL_CLAIM_DIMENSION_INDEX = "temporal_v1_claim_dimension"
_RULE_CATALOG_KEY = b"rules:catalog:v1"
_TRUTH_MAINTENANCE_STATE_KEY = b"rules:truth-maintenance:v1"
_RULE_ENGINE_AGENT = "gestaltdb:rules"
_DATABASE_ID_LOCK = threading.Lock()
_UNSET = object()


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _gestaltdb_version() -> str | None:
    try:
        return importlib_metadata.version("gestaltdb")
    except importlib_metadata.PackageNotFoundError:
        return None


def _backend_registry():
    from .kvstores import LMDBStore, LevelDBStore, PyRexStore

    return {
        "lmdb": LMDBStore,
        "leveldb": LevelDBStore,
        "pyrex": PyRexStore,
    }


def _serializer_registry():
    from .serializers import JSONSerializer, MessagePackSerializer, PickleSerializer, ProtobufSerializer

    return {
        "pickle": PickleSerializer,
        "json": JSONSerializer,
        "messagepack": MessagePackSerializer,
        "protobuf": ProtobufSerializer,
    }


def _registry_name_for_instance(instance, registry: dict[str, type]) -> str | None:
    for name, cls in registry.items():
        if isinstance(instance, cls):
            return name
    return None


class _SimpleProgress:
    """Small stderr progress fallback used when tqdm is unavailable."""

    def __init__(self, *, total=None, desc: str = "ingest", unit: str = "rows"):
        self.total = total
        self.desc = desc
        self.unit = unit
        self.current = 0
        self.started_at = time.perf_counter()

    def update(self, count: int) -> None:
        self.current += count
        elapsed = max(time.perf_counter() - self.started_at, 1e-9)
        rate = self.current / elapsed
        total = "?" if self.total is None else str(self.total)
        print(f"{self.desc}: {self.current}/{total} {self.unit} ({rate:,.0f} {self.unit}/s)", file=sys.stderr)

    def close(self) -> None:
        pass


def _normalize_labels(labels):
    """Return deterministic string labels without duplicates.

    Args:
        labels: Optional iterable of label values.

    Returns:
        Tuple of string labels preserving first-seen order.

    Examples:
        >>> _normalize_labels(["Drug", "Drug", "Compound"])
        ('Drug', 'Compound')
    """
    if labels is None:
        return tuple()
    return tuple(dict.fromkeys(str(label) for label in labels))


def _property_value_to_index_bytes(value) -> bytes:
    """Encode a property value for exact-match sorted indexes.

    Args:
        value: JSON-like property value, with bytes supported through tagging.

    Returns:
        Stable UTF-8 bytes suitable for sorted exact-match index keys.

    Examples:
        >>> _property_value_to_index_bytes("drug")
        b'"drug"'
    """
    def normalize(item):
        if isinstance(item, bytes):
            return {"__gestaltdb_type__": "bytes", "value": base64.b64encode(item).decode("ascii")}
        if isinstance(item, tuple):
            return [normalize(value) for value in item]
        if isinstance(item, list):
            return [normalize(value) for value in item]
        if isinstance(item, dict):
            return {str(key): normalize(value) for key, value in sorted(item.items())}
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _property_value_to_range_index_bytes(value) -> bytes | None:
    """Encode supported scalar values for sorted range indexes."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        packed = bytearray(struct.pack(">d", float(value)))
        if packed[0] & 0x80:
            packed = bytearray(byte ^ 0xFF for byte in packed)
        else:
            packed[0] ^= 0x80
        return b"n" + bytes(packed).hex().encode("ascii")
    if isinstance(value, str):
        return b"s" + value.encode("utf-8").hex().encode("ascii")
    return None

def datetime_to_bytes(dt: datetime.datetime, tzinfo = datetime.timezone.utc) -> bytes:
    """Convert a datetime to big-endian microseconds since the Unix epoch.

    Args:
        dt: Datetime at or after 1970-01-01.
        tzinfo: Time zone used for the epoch reference.

    Returns:
        Eight bytes containing the timestamp as an unsigned integer.

    Examples:
        >>> datetime_to_bytes(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc))
        b'\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00'
    """
    # Make sure `dt` is in UTC, or at least consistently handled.
    # (If dt has no tzinfo, Python treats it as local time for .timestamp().)
    epoch = datetime.datetime(1970, 1, 1, tzinfo=tzinfo)
    delta = dt - epoch
    # Convert to integer microseconds
    microseconds = int(delta.total_seconds() * 1_000_000)
    # Pack as an unsigned 64-bit integer in big-endian order
    return struct.pack('>Q', microseconds)

def bytes_to_datetime(b: bytes, tzinfo = datetime.timezone.utc) -> datetime.datetime:
    """Convert bytes produced by ``datetime_to_bytes`` back to a datetime.

    Args:
        b: Eight-byte timestamp generated by ``datetime_to_bytes``.
        tzinfo: Time zone used for the epoch reference.

    Returns:
        Decoded datetime.

    Examples:
        >>> bytes_to_datetime(b'\\x00' * 8)
        datetime.datetime(1970, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
    """
    epoch = datetime.datetime(1970, 1, 1, tzinfo=tzinfo)
    (microseconds,) = struct.unpack('>Q', b)
    return epoch + datetime.timedelta(microseconds=microseconds)


# =========================================
#  Node and Edge Models
# =========================================

class Node:
    """Graph node with an ID, native labels, and arbitrary properties.

    Args:
        node_id: Optional stable node identifier. A UUID is generated when omitted.
        properties: Optional dictionary of node attributes.
        labels: Optional iterable of node labels. Labels are stored natively and
            maintained in the label index by ``GraphDB``.

    Examples:
        >>> Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}).get_id
        'drug-1'
        >>> Node(node_id="drug-1", labels=["Drug", "Drug"]).labels
        ('Drug',)
    """

    def __init__(self, node_id=None, properties=None, labels=None):
        """Initialize a node, generating a UUID when ``node_id`` is omitted."""
        self._id = node_id or str(uuid.uuid4())
        self.properties = properties or {}
        self.labels = _normalize_labels(labels)

    @property
    def get_id(self):
        """Unique identifier for this node."""
        return self._id
    
    @property
    def get_id_bytes(self):
        """Return the node ID encoded as UTF-8 bytes.

        Examples:
            >>> Node(node_id="drug-1").get_id_bytes
            b'drug-1'
        """
        return self._id.encode('utf-8')

    def to_dict(self):
        """Convert to a dictionary form for serialization.

        Returns:
            Dictionary containing ``id``, ``properties``, and ``labels``.

        Examples:
            >>> Node(node_id="n1", labels=["Drug"]).to_dict()["labels"]
            ['Drug']
        """
        return {
            'id': self._id,
            'properties': self.properties,
            'labels': list(self.labels),
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create a node from serialized dictionary data.

        Args:
            data: Dictionary produced by ``to_dict``. Older dictionaries without
                ``labels`` deserialize with an empty label tuple.

        Returns:
            ``Node`` instance.

        Examples:
            >>> Node.from_dict({"id": "n1", "properties": {}}).labels
            ()
        """
        return cls(node_id=data['id'], properties=data['properties'], labels=data.get('labels'))

class Edge:
    """Directed graph edge with source, target, and properties.

    Args:
        edge_id: Optional stable edge identifier. A UUID is generated when omitted.
        source: Source node ID or ``Node`` instance.
        target: Target node ID or ``Node`` instance.
        properties: Optional edge attributes. Typed traversal reads ``properties["type"]``.

    Examples:
        >>> Edge(edge_id="d1-p1", source="drug-1", target="protein-1").source
        'drug-1'
    """

    def __init__(self, edge_id=None, source=None, target=None, properties=None):
        """If no edge_id is provided, generate a UUID."""
        self._id = edge_id or str(uuid.uuid4())
        self.source = source  # node_id or Node instance
        self.target = target  # node_id or Node instance
        self.properties = properties or {}
        
    @property
    def get_id(self):
        """Unique identifier for this edge."""
        return self._id
    
    @property
    def get_id_bytes(self):
        """Return the edge ID encoded as UTF-8 bytes.

        Examples:
            >>> Edge(edge_id="d1-p1").get_id_bytes
            b'd1-p1'
        """
        return self._id.encode('utf-8')

    @property
    def get_type(self):
        """Return the typed traversal edge type.

        Examples:
            >>> Edge(properties={"type": "drug-to-protein"}).get_type
            'drug-to-protein'
        """
        return self.properties.get('type')

    def to_dict(self):
        """Convert to a dictionary for serialization."""
        return {
            'id': self._id,
            'source': self.source if isinstance(self.source, str) else self.source.get_id,
            'target': self.target if isinstance(self.target, str) else self.target.get_id,
            'properties': self.properties
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        """Factory from dictionary."""
        return cls(edge_id=data['id'],
                   source=data['source'],
                   target=data['target'],
                   properties=data['properties'])

class TimeIndexedEdge(Edge):
    """Edge whose byte key is prefixed by a timestamp.

    Args:
        timestamp_dat: Datetime used as the sortable key prefix.
        *args: Positional arguments passed to ``Edge``.
        **kwargs: Keyword arguments passed to ``Edge``.

    Examples:
        >>> edge = TimeIndexedEdge(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc), edge_id="e1")
        >>> edge.get_id_bytes.endswith(b':e1')
        True
    """

    def __init__(self, timestamp_dat, *args, **kwargs):
        """Initialize a timestamp-prefixed edge.

        Args:
            timestamp_dat: Datetime used as the sortable key prefix.
            *args: Positional arguments passed to ``Edge``.
            **kwargs: Keyword arguments passed to ``Edge``.
        """
        super().__init__(*args, **kwargs)
        self.timestamp_dat = timestamp_dat
        self.id_string = self.get_id

    @property
    def get_id_bytes(self):
        """Return timestamp-prefixed edge ID bytes.

        Examples:
            >>> edge = TimeIndexedEdge(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc), edge_id="e1")
            >>> edge.get_id_bytes.endswith(b':e1')
            True
        """
        b2 = self.id_string.encode('utf-8')
        sep = b':'
        b1 = datetime_to_bytes(self.timestamp_dat)
        return b1 + sep + b2

    def to_dict(self):
        """Convert to a dictionary for serialization."""
        return {
            'timestamp_dat' : self.timestamp_dat,
            'id': self._id,
            'source': self.source if isinstance(self.source, str) else self.source.get_id,
            'target': self.target if isinstance(self.target, str) else self.target.get_id,
            'properties': self.properties
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        """Factory from dictionary."""
        return cls(timestamp_dat = data['timestamp_dat'],
                   edge_id=data['id'],
                   source=data['source'],
                   target=data['target'],
                   properties=data['properties'])

class GraphEntityDictSerializer:
    """Serialize graph entities through a dictionary-compatible serializer.

    Args:
        serializer: Serializer used for the final bytes conversion.

    Examples:
        >>> from gestaltdb.serializers import JSONSerializer
        >>> s = GraphEntityDictSerializer(JSONSerializer())
        >>> s.deserialize(s.serialize(Node(node_id="n1"), "Node"), "Node").get_id
        'n1'
    """

    _ent_type_encoder = {
        'Edge' : lambda x : x.to_dict(),
        'Node' : lambda x : x.to_dict(),
        'AdjacencyList' : lambda x : x
    }

    _ent_type_decoder = {
        'Edge' : lambda x : Edge.from_dict(x),
        'Node' : lambda x : Node.from_dict(x),
        'AdjacencyList' : lambda x : x
    }
    
    def __init__(self, serializer : Serializer):
        """Initialize the entity serializer wrapper.

        Args:
            serializer: Serializer used to encode dictionaries as bytes.
        """
        self.serializer = serializer
    
    def serialize(self, entity, entity_type : str):
        """Serialize a graph entity by entity type.

        Args:
            entity: ``Node``, ``Edge``, or adjacency-list object.
            entity_type: One of ``"Node"``, ``"Edge"``, or ``"AdjacencyList"``.

        Returns:
            Serialized bytes.

        Examples:
            >>> from gestaltdb.serializers import PickleSerializer
            >>> GraphEntityDictSerializer(PickleSerializer()).serialize(Node("n1"), "Node")[:1]
            b'\\x80'
        """
        enc_obj = self._ent_type_encoder[entity_type](entity)
        return self.serializer.serialize(enc_obj)
    
    def deserialize(self, val, entity_type : str):
        """ Deserializer (conditional on entity type)

        Args:
            val: bytes containing the data
            entity_type : (str) is Edge, Node, AdjacencyList
        """
        deser_val = self.serializer.deserialize(val)
        return self._ent_type_decoder[entity_type](deser_val)


class GraphDB:
    """High-level interface to manage Node/Edge storing, retrieval, and indexing."""
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
        options = dict(manifest.get("backend", {}).get("options") or {})
        options.update(backend_options or {})

        graph_metadata = manifest.get("graph", {})
        store = backend_cls(path=str(path), **options)
        try:
            graph = cls(
                store,
                serializer_cls(),
                indexed_node_properties=graph_metadata.get("indexed_node_properties") or [],
                indexed_edge_properties=graph_metadata.get("indexed_edge_properties") or [],
            )
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
        if expected is not None:
            try:
                expected = str(uuid.UUID(expected))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError("invalid database identity in root manifest") from exc
        lock_handle = None
        with _DATABASE_ID_LOCK:
            if self._store_path is not None:
                lock_path = self._store_path / ".gestaltdb_identity.lock"
                lock_handle = lock_path.open("a+b")
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                elif os.name == "nt":  # pragma: no cover - Windows-only
                    import msvcrt

                    lock_handle.write(b"\0")
                    lock_handle.flush()
                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
                else:  # pragma: no cover - unsupported platform
                    raise RuntimeError("database identity locking is unsupported on this platform")
            try:
                return self._initialize_database_id_locked(expected)
            finally:
                if lock_handle is not None:
                    if os.name == "posix":
                        import fcntl

                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                    elif os.name == "nt":  # pragma: no cover - Windows-only
                        import msvcrt

                        lock_handle.seek(0)
                        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    lock_handle.close()

    def _initialize_database_id_locked(self, expected: str | None) -> str:
        try:
            payload = self.store.get_metadata(_DATABASE_ID_METADATA_KEY)
        except NotImplementedError as exc:
            raise ValueError("the configured store cannot persist a database identity") from exc
        if payload is None:
            if expected is None and self._store_path is None:
                raise ValueError("cannot assign a database identity without a managed store path; call save_manifest(path=...)")
            candidate = expected or str(uuid.uuid4())
            self.store.put_metadata(_DATABASE_ID_METADATA_KEY, candidate.encode("ascii"))
            payload = self.store.get_metadata(_DATABASE_ID_METADATA_KEY)
        try:
            value = str(uuid.UUID(payload.decode("ascii")))
        except (AttributeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("invalid persisted database identity") from exc
        if expected is not None and value != expected:
            raise ValueError("persisted database identity does not match root manifest")
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
        registry = _backend_registry()
        if name not in registry:
            allowed = ", ".join(sorted(registry))
            raise ValueError(f"unknown GestaltDB backend '{name}'. Expected one of: {allowed}")
        return registry[name]

    @staticmethod
    def _serializer_class(name: str):
        registry = _serializer_registry()
        if name not in registry:
            allowed = ", ".join(sorted(registry))
            raise ValueError(f"unknown GestaltDB serializer '{name}'. Expected one of: {allowed}")
        return registry[name]

    @staticmethod
    def _read_manifest(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
            raise ValueError("unsupported GestaltDB manifest format version")
        return manifest

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
        try:
            payload = self.store.get_metadata(_STALE_INDEXES_METADATA_KEY)
        except NotImplementedError:
            return set()
        if not payload:
            return set()
        return set(json.loads(payload.decode("utf-8")))

    def _persist_stale_indexes(self, stale_indexes: set[str]) -> None:
        """Persist stale index metadata."""
        payload = json.dumps(sorted(stale_indexes), separators=(",", ":")).encode("utf-8")
        self.store.put_metadata(_STALE_INDEXES_METADATA_KEY, payload)

    def _mark_indexes_stale(self, *index_names: str) -> None:
        """Mark index families stale after a deferred bulk load."""
        if not index_names:
            return
        try:
            stale_indexes = self._load_stale_indexes()
            stale_indexes.update(index_names)
            self._persist_stale_indexes(stale_indexes)
        except NotImplementedError:
            return

    def _clear_stale_indexes(self, *index_names: str) -> None:
        """Clear stale markers after rebuilding index families."""
        try:
            stale_indexes = self._load_stale_indexes()
            stale_indexes.difference_update(index_names)
            self._persist_stale_indexes(stale_indexes)
        except NotImplementedError:
            return

    def stale_indexes(self) -> tuple[str, ...]:
        """Return index families requiring rebuild after deferred ingestion."""
        stale = self._load_stale_indexes()
        if self._temporal_index_state() is None and self._has_visible_temporal_history():
            stale.add("temporal")
        return tuple(sorted(stale))

    def _ensure_indexes_current(self, *index_names: str) -> None:
        """Prevent stale secondary indexes from silently returning wrong results."""
        stale = self._load_stale_indexes().intersection(index_names)
        if stale:
            names = ", ".join(sorted(stale))
            raise RuntimeError(f"stale indexes require rebuild before query: {names}")

    def _validate_index_mode(self, index_mode: str) -> None:
        """Validate the columnar ingestion index mode."""
        if isinstance(index_mode, IndexMaintenanceMode):
            index_mode = index_mode.value
        if index_mode not in _VALID_INDEX_MODES:
            allowed = ", ".join(sorted(_VALID_INDEX_MODES))
            raise ValueError(f"index_mode must be one of: {allowed}")

    def _low_level_index_mode(self, index_mode: str | IndexMaintenanceMode) -> str:
        """Return the low-level index mode used by primitive ingestion calls."""
        if isinstance(index_mode, IndexMaintenanceMode):
            index_mode = index_mode.value
        if index_mode == IndexMaintenanceMode.DEFER_REBUILD.value:
            return IndexMaintenanceMode.DEFER.value
        self._validate_index_mode(index_mode)
        index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        return index_mode

    def _coerce_columnar_ingestion_mode(self, ingestion_mode: str | ColumnarIngestionMode) -> str:
        """Normalize a high-level columnar ingestion mode."""
        if isinstance(ingestion_mode, ColumnarIngestionMode):
            return ingestion_mode.value
        values = {mode.value for mode in ColumnarIngestionMode}
        if ingestion_mode not in values:
            allowed = ", ".join(sorted(values))
            raise ValueError(f"ingestion_mode must be one of: {allowed}")
        return ingestion_mode

    def _should_rebuild_after_ingest(self, index_mode: str | IndexMaintenanceMode) -> bool:
        """Return whether high-level ingestion should rebuild deferred indexes."""
        return (index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode) == IndexMaintenanceMode.DEFER_REBUILD.value

    @contextmanager
    def _ingestion_progress(self, enabled: bool, *, total=None, desc: str):
        """Return an optional progress reporter for chunked ingestion."""
        if not enabled:
            yield None
            return
        try:
            from tqdm.auto import tqdm
        except ImportError:
            progress = _SimpleProgress(total=total, desc=desc)
        else:
            progress = tqdm(total=total, desc=desc, unit="rows", unit_scale=True)
        try:
            yield progress
        finally:
            progress.close()

    def _load_property_index_metadata(self, key: bytes) -> set[str]:
        """Load persisted property index definitions from backend metadata."""
        try:
            payload = self.store.get_metadata(key)
        except NotImplementedError:
            return set()
        if not payload:
            return set()
        return set(json.loads(payload.decode("utf-8")))

    def _persist_property_index_metadata(self, key: bytes, property_names: set[str]) -> None:
        """Persist property index definitions to backend metadata."""
        payload = json.dumps(sorted(property_names), separators=(",", ":")).encode("utf-8")
        self.store.put_metadata(key, payload)

    def _load_node_constraint_metadata(self) -> list[dict[str, str]]:
        """Load persisted node constraint definitions from backend metadata."""
        try:
            payload = self.store.get_metadata(_NODE_CONSTRAINTS_METADATA_KEY)
        except NotImplementedError:
            return []
        if not payload:
            return []
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict) or decoded.get("version") != 1:
            raise ValueError("Unsupported node constraint catalog format")
        constraints = decoded.get("constraints", [])
        if not isinstance(constraints, list):
            raise ValueError("Invalid node constraint catalog")
        return [dict(constraint) for constraint in constraints]

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
        old_node = self.get_node(node.get_id_bytes)
        if old_node is not None:
            self._delete_node_indexes(old_node)
        value = self.entity_serializer.serialize(node, 'Node')
        self.store.put_node(node.get_id_bytes, value)
        self._put_node_indexes(node)

    def get_node(self, node_id) -> Node:
        """Return a node by byte key.

        Args:
            node_id: Node ID bytes as stored in the backend.

        Returns:
            The decoded node, or ``None`` when absent.

        Examples:
            >>> graph_db.get_node(b"drug-1")  # doctest: +SKIP
        """
        data = self.store.get_node(node_id)
        if data:
            # node_dict = self.serializer.deserialize(data)
            # return Node.from_dict(node_dict)
            return self.entity_serializer.deserialize(data,'Node')
        else:
            return None

    def delete_node(self, node_id):
        """Delete a node by byte key.

        Args:
            node_id: Node ID bytes.

        Examples:
            >>> graph_db.delete_node(b"drug-1")  # doctest: +SKIP
        """
        node_id = self.node_key_to_bytes(node_id)
        incident_edge_ids = []
        for edge_id in self.store.get_edge_keys_generator():
            edge = self.get_edge(edge_id)
            if edge is None:
                continue
            if self.node_key_to_bytes(edge.source) == node_id or self.node_key_to_bytes(edge.target) == node_id:
                incident_edge_ids.append(edge_id)

        for edge_id in incident_edge_ids:
            self.delete_edge(edge_id)

        old_node = self.get_node(node_id)
        if old_node is not None:
            self._delete_node_indexes(old_node)
        self.store.delete_adjacency(node_id)
        self.store.delete_node(node_id)

    def _put_node_indexes(self, node: Node):
        """Maintain label and configured property indexes for a node."""
        entries = []
        range_entries = []
        node_id = node.get_id_bytes
        for label in node.labels:
            entries.append(("node_label", [label.encode("utf-8")], node_id))
        for property_name in self.indexed_node_properties:
            if property_name in node.properties:
                raw_value = node.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                entries.append((
                    "node_property",
                    [property_name.encode("utf-8"), property_value],
                    node_id,
                ))
                for label in node.labels:
                    entries.append((
                        "node_label_property",
                        [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                        node_id,
                    ))
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    range_entries.append(("node_property", [property_name.encode("utf-8")], range_value, node_id))
                    for label in node.labels:
                        range_entries.append(("node_label_property", [label.encode("utf-8"), property_name.encode("utf-8")], range_value, node_id))
        if entries:
            self.store.put_index_entries_bulk(entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)

    def _delete_node_indexes(self, node: Node):
        """Remove label and configured property indexes for a node."""
        node_id = node.get_id_bytes
        for label in node.labels:
            self.store.delete_index_entry("node_label", [label.encode("utf-8")], node_id)
        for property_name in self.indexed_node_properties:
            if property_name in node.properties:
                raw_value = node.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                self.store.delete_index_entry(
                    "node_property",
                    [property_name.encode("utf-8"), property_value],
                    node_id,
                )
                for label in node.labels:
                    self.store.delete_index_entry(
                        "node_label_property",
                        [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                        node_id,
                    )
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    self.store.delete_range_index_entry("node_property", [property_name.encode("utf-8")], range_value, node_id)
                    for label in node.labels:
                        self.store.delete_range_index_entry("node_label_property", [label.encode("utf-8"), property_name.encode("utf-8")], range_value, node_id)

    def _flush_index_rebuild_batches(self, entries: list, range_entries: list) -> None:
        """Write accumulated rebuild entries and clear the buffers."""
        if entries:
            self.store.put_index_entries_bulk(entries)
            entries.clear()
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)
            range_entries.clear()

    def rebuild_node_indexes(
        self,
        *,
        labels: bool = True,
        properties: list[str] | tuple[str, ...] | set[str] | None = None,
        batch_size: int = _INDEX_REBUILD_BATCH_SIZE,
    ) -> dict[str, int]:
        """Rebuild requested node secondary indexes in one node scan."""
        property_names = sorted(self.indexed_node_properties if properties is None else set(properties))
        entries = []
        range_entries = []
        rebuilt = {"node_label": 0}
        rebuilt.update({f"node_property:{property_name}": 0 for property_name in property_names})

        for node_id in self.store.get_node_keys_generator():
            node = self.get_node(node_id)
            if node is None:
                continue
            node_key = node.get_id_bytes
            if labels:
                for label in node.labels:
                    entries.append(("node_label", [label.encode("utf-8")], node_key))
                    rebuilt["node_label"] += 1
            for property_name in property_names:
                if property_name not in node.properties:
                    continue
                raw_value = node.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                entries.append(("node_property", [property_name.encode("utf-8"), property_value], node_key))
                for label in node.labels:
                    entries.append((
                        "node_label_property",
                        [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                        node_key,
                    ))
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    range_entries.append(("node_property", [property_name.encode("utf-8")], range_value, node_key))
                    for label in node.labels:
                        range_entries.append((
                            "node_label_property",
                            [label.encode("utf-8"), property_name.encode("utf-8")],
                            range_value,
                            node_key,
                        ))
                rebuilt[f"node_property:{property_name}"] += 1
            if len(entries) + len(range_entries) >= batch_size:
                self._flush_index_rebuild_batches(entries, range_entries)

        self._flush_index_rebuild_batches(entries, range_entries)
        if labels:
            self._clear_stale_indexes("node_label")
        if property_names and self.indexed_node_properties.issubset(set(property_names)):
            self._clear_stale_indexes("node_property")
        rebuilt["node_property"] = sum(rebuilt[f"node_property:{property_name}"] for property_name in property_names)
        return rebuilt

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
        self._ensure_indexes_current("node_label")
        yield from self.store.iter_index_prefix("node_label", [label.encode("utf-8")])

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
        self._ensure_indexes_current("node_property")
        yield from self.store.iter_index_prefix(
            "node_property",
            [property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
        )

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
        self._ensure_indexes_current("node_label", "node_property")
        yield from self.store.iter_index_prefix(
            "node_label_property",
            [label.encode("utf-8"), property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
        )

    def nodes_by_label_property(self, label: str, property_name: str, value):
        """Return nodes using the composite label/property exact-match index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_label_property(label, property_name, value)]

    def count_nodes_by_label_property(self, label: str, property_name: str, value) -> int:
        """Return the number of nodes indexed for a label and exact property value."""
        return sum(1 for _ in self.iter_node_ids_by_label_property(label, property_name, value))

    def iter_node_ids_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield node IDs from a scalar property range index."""
        self._ensure_indexes_current("node_property")
        start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
        end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
        if (start_value is not None and start is None) or (end_value is not None and end is None):
            return iter(())
        if start is not None and end is not None and start[:1] != end[:1]:
            return iter(())
        return self.store.iter_range_index(
            "node_property",
            [property_name.encode("utf-8")],
            start,
            end,
            include_start,
            include_end,
        )

    def nodes_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return nodes using a scalar property range index."""
        return [self.get_node(node_id) for node_id in self.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)]

    def count_nodes_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of nodes indexed in a scalar property range."""
        return sum(1 for _ in self.iter_node_ids_by_property_range(property_name, start_value, end_value, include_start, include_end))

    def iter_node_ids_by_label_property_range(self, label: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield node IDs from a composite label/property range index."""
        self._ensure_indexes_current("node_label", "node_property")
        start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
        end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
        if (start_value is not None and start is None) or (end_value is not None and end is None):
            return iter(())
        if start is not None and end is not None and start[:1] != end[:1]:
            return iter(())
        return self.store.iter_range_index(
            "node_label_property",
            [label.encode("utf-8"), property_name.encode("utf-8")],
            start,
            end,
            include_start,
            include_end,
        )

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
        if isinstance(node_key, bytes):
            return node_key
        return node_key.encode('utf-8')

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
        if isinstance(edge_key, bytes):
            return edge_key
        return edge_key.encode('utf-8')

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
        if isinstance(key, bytes):
            return key.decode('utf-8')
        return key

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
        # edge_dict = edge.to_dict()
        old_edge = self.get_edge(edge.get_id_bytes)
        if old_edge is not None:
            self._delete_typed_adjacency_for_edge(old_edge)
            self._delete_edge_indexes(old_edge)
            self._remove_edge_from_endpoint_adjacencies(old_edge)
        value = self.entity_serializer.serialize(edge,'Edge')
        self.store.put_edge(edge.get_id_bytes, value)
        self._put_typed_adjacency_for_edge(edge)
        self._put_edge_indexes(edge)
        if update_adjacency:
            # Get the edge lists from the adjacency store, 
            # and if they exist update them, if they don't exist 
            # create them.
            for dict_flag, io_node in zip(['source','target'], (edge.source, edge.target)): 
                io_node_key = self.node_key_to_bytes(io_node)
                adj_list = self.store.get_adjacency(io_node_key)
                new_edge_list = {dict_flag : [edge.get_id]}
                if adj_list is None:
                    serialized_adj_list = self.entity_serializer.serialize(new_edge_list,'AdjacencyList')
                    self.store.put_adjacency(io_node_key, serialized_adj_list)
                else:
                    adj_edge_list = self.entity_serializer.deserialize(adj_list,'AdjacencyList')
                    if edge.get_id not in adj_edge_list.setdefault(dict_flag, []):
                        adj_edge_list[dict_flag].append(edge.get_id)
                    self.store.put_adjacency(io_node_key, self.serializer.serialize(adj_edge_list))

    def _put_typed_adjacency_for_edge(self, edge: Edge):
        """Write typed adjacency index records for an edge.

        Args:
            edge: Edge whose ``properties["type"]`` determines the typed index.
        """
        edge_type = self.edge_type(edge)
        if edge_type is None:
            return
        source_id = self.node_key_to_bytes(edge.source)
        target_id = self.node_key_to_bytes(edge.target)
        self.store.put_typed_adjacency(source_id, target_id, edge_type, edge.get_id_bytes)
        self._update_typed_adjacency_count_cache([(source_id, target_id, edge_type, edge.get_id_bytes)], 1)

    def _delete_typed_adjacency_for_edge(self, edge: Edge):
        """Remove typed adjacency index records for an edge.

        Args:
            edge: Edge whose current source, target, type, and ID identify records.
        """
        edge_type = self.edge_type(edge)
        if edge_type is None:
            return
        source_id = self.node_key_to_bytes(edge.source)
        target_id = self.node_key_to_bytes(edge.target)
        self.store.delete_typed_adjacency(source_id, target_id, edge_type, edge.get_id_bytes)
        self._update_typed_adjacency_count_cache([(source_id, target_id, edge_type, edge.get_id_bytes)], -1)

    def _update_typed_adjacency_count_cache(self, records, delta: int) -> None:
        """Update cached typed adjacency counts for writes made by this graph handle."""
        out_deltas: dict[tuple[str, bytes, str], int] = {}
        invalidated_in: set[tuple[str, bytes, str]] = set()
        for source_id, target_id, edge_type, _ in records:
            out_key = ("out", source_id, edge_type)
            out_deltas[out_key] = out_deltas.get(out_key, 0) + delta
            invalidated_in.add(("in", target_id, edge_type))
        for out_key, out_delta in out_deltas.items():
            current = self._typed_adjacency_count_cache.get(out_key, 0)
            self._typed_adjacency_count_cache[out_key] = max(0, current + out_delta)
        for in_key in invalidated_in:
            self._typed_adjacency_count_cache.pop(in_key, None)

    def _put_edge_indexes(self, edge: Edge):
        """Maintain relationship type and configured property indexes."""
        entries = []
        range_entries = []
        edge_type = self.edge_type(edge)
        edge_id = edge.get_id_bytes
        if edge_type is not None:
            entries.append(("edge_type", [str(edge_type).encode("utf-8")], edge_id))
        for property_name in self.indexed_edge_properties:
            if property_name in edge.properties:
                raw_value = edge.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                entries.append((
                    "edge_property",
                    [property_name.encode("utf-8"), property_value],
                    edge_id,
                ))
                if edge_type is not None:
                    entries.append((
                        "edge_type_property",
                        [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                        edge_id,
                    ))
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    range_entries.append(("edge_property", [property_name.encode("utf-8")], range_value, edge_id))
                    if edge_type is not None:
                        range_entries.append(("edge_type_property", [str(edge_type).encode("utf-8"), property_name.encode("utf-8")], range_value, edge_id))
        if entries:
            self.store.put_index_entries_bulk(entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)

    def _delete_edge_indexes(self, edge: Edge):
        """Remove relationship type and configured property indexes."""
        edge_type = self.edge_type(edge)
        edge_id = edge.get_id_bytes
        if edge_type is not None:
            self.store.delete_index_entry("edge_type", [str(edge_type).encode("utf-8")], edge_id)
        for property_name in self.indexed_edge_properties:
            if property_name in edge.properties:
                raw_value = edge.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                self.store.delete_index_entry(
                    "edge_property",
                    [property_name.encode("utf-8"), property_value],
                    edge_id,
                )
                if edge_type is not None:
                    self.store.delete_index_entry(
                        "edge_type_property",
                        [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                        edge_id,
                    )
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    self.store.delete_range_index_entry("edge_property", [property_name.encode("utf-8")], range_value, edge_id)
                    if edge_type is not None:
                        self.store.delete_range_index_entry("edge_type_property", [str(edge_type).encode("utf-8"), property_name.encode("utf-8")], range_value, edge_id)

    def rebuild_edge_indexes(
        self,
        *,
        edge_types: bool = True,
        properties: list[str] | tuple[str, ...] | set[str] | None = None,
        batch_size: int = _INDEX_REBUILD_BATCH_SIZE,
    ) -> dict[str, int]:
        """Rebuild requested edge secondary indexes in one edge scan."""
        property_names = sorted(self.indexed_edge_properties if properties is None else set(properties))
        entries = []
        range_entries = []
        rebuilt = {"edge_type": 0}
        rebuilt.update({f"edge_property:{property_name}": 0 for property_name in property_names})

        for edge_id in self.store.get_edge_keys_generator():
            edge = self.get_edge(edge_id)
            if edge is None:
                continue
            edge_key = edge.get_id_bytes
            edge_type = self.edge_type(edge)
            if edge_types and edge_type is not None:
                entries.append(("edge_type", [str(edge_type).encode("utf-8")], edge_key))
                rebuilt["edge_type"] += 1
            for property_name in property_names:
                if property_name not in edge.properties:
                    continue
                raw_value = edge.properties[property_name]
                property_value = _property_value_to_index_bytes(raw_value)
                entries.append(("edge_property", [property_name.encode("utf-8"), property_value], edge_key))
                if edge_type is not None:
                    entries.append((
                        "edge_type_property",
                        [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                        edge_key,
                    ))
                range_value = _property_value_to_range_index_bytes(raw_value)
                if range_value is not None:
                    range_entries.append(("edge_property", [property_name.encode("utf-8")], range_value, edge_key))
                    if edge_type is not None:
                        range_entries.append((
                            "edge_type_property",
                            [str(edge_type).encode("utf-8"), property_name.encode("utf-8")],
                            range_value,
                            edge_key,
                        ))
                rebuilt[f"edge_property:{property_name}"] += 1
            if len(entries) + len(range_entries) >= batch_size:
                self._flush_index_rebuild_batches(entries, range_entries)

        self._flush_index_rebuild_batches(entries, range_entries)
        if edge_types:
            self._clear_stale_indexes("edge_type")
        if property_names and self.indexed_edge_properties.issubset(set(property_names)):
            self._clear_stale_indexes("edge_property")
        rebuilt["edge_property"] = sum(rebuilt[f"edge_property:{property_name}"] for property_name in property_names)
        return rebuilt

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
        self._ensure_indexes_current("edge_type")
        yield from self.store.iter_index_prefix("edge_type", [str(edge_type).encode("utf-8")])

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
        if num_edges == 0:
            return
        yield from self.store.get_edge_keys_generator(num_edges, key_offset)

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
        self._ensure_indexes_current("edge_property")
        yield from self.store.iter_index_prefix(
            "edge_property",
            [property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
        )

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
        self._ensure_indexes_current("edge_type", "edge_property")
        yield from self.store.iter_index_prefix(
            "edge_type_property",
            [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), _property_value_to_index_bytes(value)],
        )

    def edges_by_type_property(self, edge_type: str, property_name: str, value):
        """Return edges using the composite type/property exact-match index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_type_property(edge_type, property_name, value)]

    def count_edges_by_type_property(self, edge_type: str, property_name: str, value) -> int:
        """Return the number of edges indexed for a type and exact property value."""
        return sum(1 for _ in self.iter_edge_ids_by_type_property(edge_type, property_name, value))

    def iter_edge_ids_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield edge IDs from a scalar property range index."""
        self._ensure_indexes_current("edge_property")
        start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
        end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
        if (start_value is not None and start is None) or (end_value is not None and end is None):
            return iter(())
        if start is not None and end is not None and start[:1] != end[:1]:
            return iter(())
        return self.store.iter_range_index(
            "edge_property",
            [property_name.encode("utf-8")],
            start,
            end,
            include_start,
            include_end,
        )

    def edges_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Return edges using a scalar property range index."""
        return [self.get_edge(edge_id) for edge_id in self.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end)]

    def count_edges_by_property_range(self, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True) -> int:
        """Return the number of edges indexed in a scalar property range."""
        return sum(1 for _ in self.iter_edge_ids_by_property_range(property_name, start_value, end_value, include_start, include_end))

    def iter_edge_ids_by_type_property_range(self, edge_type: str, property_name: str, start_value=None, end_value=None, include_start: bool = True, include_end: bool = True):
        """Yield edge IDs from a composite type/property range index."""
        self._ensure_indexes_current("edge_type", "edge_property")
        start = None if start_value is None else _property_value_to_range_index_bytes(start_value)
        end = None if end_value is None else _property_value_to_range_index_bytes(end_value)
        if (start_value is not None and start is None) or (end_value is not None and end is None):
            return iter(())
        if start is not None and end is not None and start[:1] != end[:1]:
            return iter(())
        return self.store.iter_range_index(
            "edge_type_property",
            [str(edge_type).encode("utf-8"), property_name.encode("utf-8")],
            start,
            end,
            include_start,
            include_end,
        )

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
        **kwargs,
    ):
        """Build a read-optimized array sampler snapshot from this graph.

        Args:
            output_path: Directory where snapshot arrays and metadata are written.
            temporal: Build temporal-history arrays from one pinned read view.
            system_time: Optional system-time horizon for a temporal build.
            time_bucket: Temporal candidate-index bucket policy: ``None``,
                ``"none"``, ``"hour"``, or ``"day"``.
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
        return SamplerSnapshot.build(self, output_path, **kwargs)

    @contextmanager
    def read_view(self, *, valid_time, system_time=None, through_commit=None):
        """Yield a temporal read view pinned to one visible commit horizon."""
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
        visibility_digest = hashlib.sha256(b"".join(markers[:horizon])).hexdigest()
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
        return list(self.iter_typed_adjacency(node_id, edge_type, direction))

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
        if direction not in {'out', 'in', 'any'}:
            raise ValueError("direction must be 'out', 'in', or 'any'")

        node_id_bytes = self.node_key_to_bytes(node_id)
        if direction == 'any':
            return self.count_typed_adjacency(node_id_bytes, edge_type, 'out') + self.count_typed_adjacency(node_id_bytes, edge_type, 'in')

        cache_key = (direction, node_id_bytes, edge_type)
        cached = self._typed_adjacency_count_cache.get(cache_key)
        if cached is not None:
            return cached

        count = self.store.count_typed_adjacency(node_id_bytes, edge_type, direction)
        self._typed_adjacency_count_cache[cache_key] = count
        return count

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
        if direction not in {'out', 'in', 'any'}:
            raise ValueError("direction must be 'out', 'in', or 'any'")

        node_id_bytes = self.node_key_to_bytes(node_id)
        directions = ['out', 'in'] if direction == 'any' else [direction]
        for current_direction in directions:
            for edge_id, neighbor_id in self.store.iter_typed_adjacency(node_id_bytes, edge_type, current_direction):
                if current_direction == 'out':
                    source_id = node_id_bytes
                    target_id = neighbor_id
                else:
                    source_id = neighbor_id
                    target_id = node_id_bytes
                yield {
                    'edge_id': edge_id,
                    'neighbor_id': neighbor_id,
                    'source_id': source_id,
                    'target_id': target_id,
                    'edge_type': edge_type,
                    'direction': current_direction,
                }

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
        return [record['neighbor_id'] for record in self.iter_typed_adjacency(node_id, edge_type, direction)]

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
        return [record['edge_id'] for record in self.get_typed_adjacency(node_id, edge_type, direction)]

    def sample_neighbors(self, node_id, edge_type: str, direction: str = 'out', sample_size: int = 10, rng=None):
        """Sample typed neighbors using reservoir sampling.

        Args:
            node_id: Node ID as string or bytes.
            edge_type: Edge type to traverse.
            direction: ``"out"``, ``"in"``, or ``"any"``.
            sample_size: Maximum number of records to return.
            rng: Optional random number generator with ``randrange``.

        Returns:
            List of typed adjacency records.

        Examples:
            >>> graph_db.sample_neighbors("drug-1", "drug-to-protein", sample_size=2)  # doctest: +SKIP
        """
        rng = rng or random
        sample = []
        seen = 0
        for record in self.iter_typed_adjacency(node_id, edge_type, direction):
            seen += 1
            if len(sample) < sample_size:
                sample.append(record)
                continue
            replacement_idx = rng.randrange(seen)
            if replacement_idx < sample_size:
                sample[replacement_idx] = record
        return sample

    def sample_typed_paths(self, seed_ids, pattern: Union[SamplingPattern, list[dict]], rng=None):
        """Sample paths that follow an ordered typed edge pattern.

        Args:
            seed_ids: Starting node IDs as strings or bytes.
            pattern: ``SamplingPattern`` or list of dictionaries such as
                ``{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}``.
            rng: Optional random number generator with ``randrange``.

        Returns:
            List of dictionaries with ``seed`` and sampled ``path`` records.

        Examples:
            >>> from gestaltdb.sampling import SamplingHop, SamplingPattern
            >>> pattern = SamplingPattern([SamplingHop("drug-to-protein", sample_size=2)])
            >>> graph_db.sample_typed_paths(["drug-1"], pattern)  # doctest: +SKIP
        """
        rng = rng or random
        pattern = as_sampling_pattern(pattern)
        paths = []

        for seed_id in seed_ids:
            seed_id_bytes = self.node_key_to_bytes(seed_id)
            frontier = [{'seed': seed_id_bytes, 'path': [], 'current_node_id': seed_id_bytes}]
            for hop in pattern:
                next_frontier = []
                edge_type = hop.edge_type
                direction = hop.direction
                sample_size = hop.sample_size
                for partial in frontier:
                    sampled_records = self.sample_neighbors(
                        partial['current_node_id'],
                        edge_type,
                        direction=direction,
                        sample_size=sample_size,
                        rng=rng,
                    )
                    for record in sampled_records:
                        next_frontier.append({
                            'seed': partial['seed'],
                            'path': partial['path'] + [record],
                            'current_node_id': record['neighbor_id'],
                        })
                frontier = next_frontier
                if not frontier:
                    break
            for partial in frontier:
                paths.append({'seed': partial['seed'], 'path': partial['path']})
        return paths

    def sample_typed_subgraph(self, seed_ids, pattern: Union[SamplingPattern, list[dict]], rng=None):
        """Sample and materialize a typed subgraph around seed nodes.

        Args:
            seed_ids: Starting node IDs as strings or bytes.
            pattern: ``SamplingPattern`` or list of dictionary hop configs.
            rng: Optional random number generator with ``randrange``.

        Returns:
            Dictionary with ``nodes``, ``edges``, and ``paths`` entries.

        Examples:
            >>> pattern = [{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}]
            >>> graph_db.sample_typed_subgraph(["drug-1"], pattern)  # doctest: +SKIP
        """
        paths = self.sample_typed_paths(seed_ids, pattern, rng=rng)
        node_ids = {self.node_key_to_bytes(seed_id) for seed_id in seed_ids}
        edge_ids = set()
        for sampled_path in paths:
            node_ids.add(sampled_path['seed'])
            for hop in sampled_path['path']:
                node_ids.add(hop['source_id'])
                node_ids.add(hop['target_id'])
                edge_ids.add(hop['edge_id'])

        return {
            'nodes': {node_id: self.get_node(node_id) for node_id in node_ids},
            'edges': {edge_id: self.get_edge(edge_id) for edge_id in edge_ids},
            'paths': paths,
        }

    def rebuild_typed_adjacency(self):
        """Rebuild typed adjacency indexes from stored edge records.

        Returns:
            Number of typed edges indexed.

        Examples:
            >>> graph_db.rebuild_typed_adjacency()  # doctest: +SKIP
        """
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
        data = self.store.get_edge(edge_id)
        if data:
            return self.entity_serializer.deserialize(data,'Edge')
        else:
            return None

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

        Example:
            >>> graph.ingest_arrow(node_ids, edge_ids, sources, targets, edge_types, node_properties={"kind": kinds})  # doctest: +SKIP
        """
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
        self._validate_index_mode(index_mode)
        index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        node_list = NodeList.from_arrow(node_ids, node_values)
        with self._ingestion_progress(progress, total=len(node_list.node_ids), desc="ingest nodes") as progress_bar:
            for chunk in node_list.chunks(chunk_size):
                if index_mode == "maintain" and not append_only:
                    self._delete_existing_node_indexes_for_columnar_chunk(chunk)
                self.store.ingest_nodes_columnar(chunk, native=native)
                if index_mode == "maintain":
                    self._put_node_indexes_for_columnar_chunk(chunk)
                if progress_bar is not None:
                    progress_bar.update(len(chunk.node_ids))
        if index_mode == "defer":
            self._mark_indexes_stale("node_label")
            if self.indexed_node_properties:
                self._mark_indexes_stale("node_property")
        return len(node_list.node_ids)

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
        try:
            import polars as pl
        except ImportError as exc:
            from .ingestion import _missing_dependency_error

            raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_nodes_polars") from exc
        self._validate_index_mode(index_mode)
        index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        self._validate_polars_frame(pl, df, "GraphDB.ingest_nodes_polars")
        total_rows = self._polars_row_count(pl, df)
        total = 0
        with self._ingestion_progress(progress, total=total_rows, desc="ingest nodes") as progress_bar:
            for df_chunk in self._iter_polars_chunks(pl, df, chunk_size):
                node_list = NodeList.from_polars(df_chunk, node_id=node_id, node_value=node_value)
                for chunk in node_list.chunks(chunk_size):
                    if index_mode == "maintain" and not append_only:
                        self._delete_existing_node_indexes_for_columnar_chunk(chunk)
                    self.store.ingest_nodes_columnar(chunk, native=native)
                    if index_mode == "maintain":
                        self._put_node_indexes_for_columnar_chunk(chunk)
                    if progress_bar is not None:
                        progress_bar.update(len(chunk.node_ids))
                total += len(node_list.node_ids)
        if index_mode == "defer":
            self._mark_indexes_stale("node_label")
            if self.indexed_node_properties:
                self._mark_indexes_stale("node_property")
        return total

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
        try:
            import polars as pl
        except ImportError as exc:
            from .ingestion import _missing_dependency_error

            raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_nodes_polars_entities") from exc
        self._validate_polars_frame(pl, df, "GraphDB.ingest_nodes_polars_entities")
        columns = self._polars_columns(pl, df)
        if node_id not in columns:
            raise ValueError(f"missing required columns: {node_id}")

        if property_columns is None:
            excluded = {node_id}
            if isinstance(labels, str):
                excluded.add(labels)
            property_columns = [column for column in columns if column not in excluded]
        missing = [column for column in property_columns if column not in columns]
        if missing:
            raise ValueError(f"missing property columns: {', '.join(missing)}")

        total = 0
        total_rows = self._polars_row_count(pl, df)
        with self._ingestion_progress(progress, total=total_rows, desc="ingest nodes") as progress_bar:
            for df_chunk in self._iter_polars_chunks(pl, df, chunk_size):
                if isinstance(self.serializer, JSONSerializer):
                    label_expr = self._polars_labels_expr(pl, df_chunk, labels)
                    value_expr = pl.struct(
                        [
                            pl.col(node_id).alias("id"),
                            self._polars_properties_expr(pl, property_columns).alias("properties"),
                            label_expr.alias("labels"),
                        ]
                    ).struct.json_encode()
                    payloads = df_chunk.select(value_expr.alias("node_value"))["node_value"].to_arrow().cast("binary")
                else:
                    payloads = []
                    selected = [node_id, *property_columns]
                    label_column = labels if isinstance(labels, str) and labels in df_chunk.columns else None
                    if label_column is not None:
                        selected.append(label_column)
                    for row in df_chunk.select(selected).rows(named=True):
                        node_labels = row[label_column] if label_column is not None else ([] if labels is None or isinstance(labels, str) else labels)
                        if isinstance(node_labels, str):
                            node_labels = [node_labels]
                        payloads.append(
                            self.serialize_node_value(
                                Node(
                                    node_id=row[node_id],
                                    labels=node_labels,
                                    properties={column: row[column] for column in property_columns},
                                )
                            )
                        )

                total += self.ingest_nodes_arrow(
                    df_chunk[node_id].to_arrow(),
                    payloads,
                    native=native,
                    chunk_size=chunk_size,
                    append_only=append_only,
                    index_mode=index_mode,
                    progress=False,
                )
                if progress_bar is not None:
                    progress_bar.update(df_chunk.height)
        return total

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
        try:
            import polars as pl
        except ImportError:
            pl = None

        if isinstance(self.serializer, JSONSerializer) and pl is not None:
            data = {"node_id": node_ids}
            if labels is not None:
                data["labels"] = labels
            for name, column in (properties or {}).items():
                data[name] = column
            return self.ingest_nodes_polars_entities(
                pl.DataFrame(data),
                node_id="node_id",
                labels="labels" if labels is not None else None,
                property_columns=list((properties or {}).keys()),
                native=native,
                chunk_size=chunk_size,
                append_only=append_only,
                index_mode=index_mode,
                progress=progress,
            )

        raw_node_ids = node_ids.to_pylist() if hasattr(node_ids, "to_pylist") else list(node_ids)
        raw_labels = labels.to_pylist() if hasattr(labels, "to_pylist") else labels
        raw_properties = {
            name: column.to_pylist() if hasattr(column, "to_pylist") else list(column)
            for name, column in (properties or {}).items()
        }
        payloads = []
        for index, node_key in enumerate(raw_node_ids):
            node_labels = raw_labels[index] if isinstance(raw_labels, list) else raw_labels
            if isinstance(node_labels, str):
                node_labels = [node_labels]
            payloads.append(
                self.serialize_node_value(
                    Node(
                        node_id=node_key,
                        labels=node_labels,
                        properties={name: values[index] for name, values in raw_properties.items()},
                    )
                )
            )
        return self.ingest_nodes_arrow(
            raw_node_ids,
            payloads,
            native=native,
            chunk_size=chunk_size,
            append_only=append_only,
            index_mode=index_mode,
            progress=progress,
        )

    def _polars_labels_expr(self, pl, df, labels):
        """Return a Polars expression for node labels."""
        if labels is None:
            return pl.lit([])
        if isinstance(labels, str):
            return pl.col(labels) if labels in df.columns else pl.lit([])
        return pl.lit(list(labels))

    def _polars_properties_expr(self, pl, property_columns):
        """Return a Polars expression for an entity properties object."""
        if not property_columns:
            return pl.lit({})
        return pl.struct(list(property_columns))

    def _validate_polars_frame(self, pl, df, feature_name: str) -> None:
        """Validate that input is a Polars eager or lazy frame."""
        if not isinstance(df, (pl.DataFrame, pl.LazyFrame)):
            raise TypeError(f"df must be a polars.DataFrame or polars.LazyFrame for {feature_name}")

    def _polars_columns(self, pl, df) -> list[str]:
        """Return column names without collecting LazyFrame data."""
        if isinstance(df, pl.LazyFrame):
            return df.collect_schema().names()
        return df.columns

    def _polars_row_count(self, pl, df):
        """Return an eager row count, or None for lazy inputs."""
        if isinstance(df, pl.DataFrame):
            return df.height
        return None

    def _iter_polars_chunks(self, pl, df, chunk_size: int):
        """Yield eager DataFrame chunks from eager or lazy Polars input."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if isinstance(df, pl.DataFrame):
            for start in range(0, df.height, chunk_size):
                yield df.slice(start, chunk_size)
            return

        if hasattr(df, "collect_batches"):
            for chunk in df.collect_batches(chunk_size=chunk_size):
                if chunk.height:
                    yield chunk
            return

        offset = 0
        while True:
            chunk = df.slice(offset, chunk_size).collect()
            if chunk.height == 0:
                break
            yield chunk
            if chunk.height < chunk_size:
                break
            offset += chunk_size

    def _delete_existing_node_indexes_for_columnar_chunk(self, chunk: NodeList) -> None:
        """Remove stale indexes before opaque columnar node upserts."""
        for node_id in chunk.node_ids:
            old_node = self.get_node(node_id)
            if old_node is not None:
                self._delete_node_indexes(old_node)

    def _put_node_indexes_for_columnar_chunk(self, chunk: NodeList) -> None:
        """Maintain label and configured property indexes for columnar nodes."""
        entries = []
        range_entries = []
        for node_value in chunk.node_values:
            node = self.entity_serializer.deserialize(node_value, "Node")
            for label in node.labels:
                entries.append(("node_label", [label.encode("utf-8")], node.get_id_bytes))
            for property_name in self.indexed_node_properties:
                if property_name in node.properties:
                    raw_value = node.properties[property_name]
                    property_value = _property_value_to_index_bytes(raw_value)
                    entries.append((
                        "node_property",
                        [property_name.encode("utf-8"), property_value],
                        node.get_id_bytes,
                    ))
                    for label in node.labels:
                        entries.append((
                            "node_label_property",
                            [label.encode("utf-8"), property_name.encode("utf-8"), property_value],
                            node.get_id_bytes,
                        ))
                    range_value = _property_value_to_range_index_bytes(raw_value)
                    if range_value is not None:
                        range_entries.append(("node_property", [property_name.encode("utf-8")], range_value, node.get_id_bytes))
                        for label in node.labels:
                            range_entries.append(("node_label_property", [label.encode("utf-8"), property_name.encode("utf-8")], range_value, node.get_id_bytes))
        if entries:
            self.store.put_index_entries_bulk(entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)

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
        results = []
        raw_dict = self.store.get_nodes_bulk(node_ids)  # dict[node_id, bytes]
        for node_id in node_ids:
            raw_data = raw_dict.get(node_id)
            if raw_data is not None:
                node_dict = self.serializer.deserialize(raw_data)
                results.append(Node.from_dict(node_dict))
            else:
                results.append(None)  # or skip it
        return results
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
        return self.store.get_node_keys_generator(num_nodes, key_offset)

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
        
        raw = self.store.get_adjacency(node_id)
        if raw is None:
            return []
        adj = self.serializer.deserialize(raw)
        
        if return_raw:
            return adj

        if direction == 'forward':
            return adj.get('target',[]) 
        if direction == 'backward':
            return adj.get('source',[])
        if direction == 'any' : 
            _s = adj.get('source',[])
            _t = adj.get('target',[])
            return list(set(_s).union(set(_t)))
        
    def put_adjacency_list(self, node_id: str, edges_list: list[str]):
        """Stores the adjacency list for node_id."""
        raw = self.serializer.serialize(edges_list)
        self.store.put_adjacency(node_id, raw)

    # -----------------------
    # Deletion (Optional)
    # -----------------------
    def delete_edge(self, edge_id: str, edge_key_serializer= lambda x:  x.encode('utf-8')):
        """
        Removes the edge from the edge store, and from adjacency
        of both source and target nodes. If either node doesn't exist,
        we skip gracefully.
        """
        e = self.get_edge(edge_id)
        if not e:
            return  # Edge not found

        self._delete_typed_adjacency_for_edge(e)
        self._delete_edge_indexes(e)
        self._remove_edge_from_endpoint_adjacencies(e)

        # If your store had a 'delete_edge' method, you'd call it here.
        # We'll assume you add that to KVStore if needed, e.g.:
        self.store.delete_edge(edge_id)

    def _edge_id_variants(self, edge_id):
        """Return string/byte forms used by legacy adjacency records."""
        variants = [edge_id]
        if isinstance(edge_id, bytes):
            try:
                edge_id_text = edge_id.decode("utf-8")
            except UnicodeDecodeError:
                edge_id_text = None
            if edge_id_text is not None:
                variants.append(edge_id_text)
        elif isinstance(edge_id, str):
            variants.append(edge_id.encode("utf-8"))
        return variants

    def _remove_edge_from_endpoint_adjacencies(self, edge: Edge):
        """Remove an edge from the legacy adjacency blobs for its endpoints."""
        source_id = self.node_key_to_bytes(edge.source)
        target_id = self.node_key_to_bytes(edge.target)
        self._remove_edge_from_adjacency(source_id, edge.get_id)
        self._remove_edge_from_adjacency(source_id, edge.get_id_bytes)
        if target_id != source_id:
            self._remove_edge_from_adjacency(target_id, edge.get_id)
            self._remove_edge_from_adjacency(target_id, edge.get_id_bytes)

    def _remove_edge_from_adjacency(self, node_id: str, edge_id: str):
        """Remove an edge ID from a node's stored adjacency lists.

        Args:
            node_id: Node key whose adjacency list should be updated.
            edge_id: Edge key to remove.
        """

        
        adj_list = self.get_adjacency_list(node_id,return_raw = True)
        _changed = False
        edge_id_variants = set(self._edge_id_variants(edge_id))
        for _dir in ['source', 'target']:
            if _dir in adj_list:
                filtered_edges = [existing_edge_id for existing_edge_id in adj_list[_dir] if existing_edge_id not in edge_id_variants]
                if len(filtered_edges) != len(adj_list[_dir]):
                    adj_list[_dir] = filtered_edges
                    _changed = True
        if _changed:
            if not adj_list.get('source') and not adj_list.get('target'):
                self.store.delete_adjacency(node_id)
            else:
                self.put_adjacency_list(node_id, adj_list)

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
        # 1) Build a dict[edge_id, bytes] to store all edges in one go
        edge_dict = {}
        typed_adjacency_records = []
        index_entries = []
        range_entries = []
        # 2) Accumulate adjacency changes in memory: node_id -> set(edge_ids)
        adjacency_accumulator = {} # the keys are "nodes" and the values are sets of edges for where the nodes appear as source or destinations (separately). 
        # adjacency_accumulator_target = {}

        for e in edges:
            if check_existing:
                old_edge = self.get_edge(e.get_id_bytes)
                if old_edge is not None:
                    self._delete_typed_adjacency_for_edge(old_edge)
                    self._delete_edge_indexes(old_edge)
                    self._remove_edge_from_endpoint_adjacencies(old_edge)
            e_bytes = self.entity_serializer.serialize(e, 'Edge')
            _source = self.node_key_to_bytes(e.source)
            _target = self.node_key_to_bytes(e.target)
            edge_dict[e.get_id_bytes] = e_bytes
            edge_type = self.edge_type(e)
            if edge_type is not None:
                typed_adjacency_records.append((_source, _target, edge_type, e.get_id_bytes))
                index_entries.append(("edge_type", [str(edge_type).encode("utf-8")], e.get_id_bytes))
            for property_name in self.indexed_edge_properties:
                if property_name in e.properties:
                    property_value = _property_value_to_index_bytes(e.properties[property_name])
                    index_entries.append((
                        "edge_property",
                        [property_name.encode("utf-8"), property_value],
                        e.get_id_bytes,
                    ))
                    if edge_type is not None:
                        index_entries.append((
                            "edge_type_property",
                            [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                            e.get_id_bytes,
                        ))
                    range_value = _property_value_to_range_index_bytes(e.properties[property_name])
                    if range_value is not None:
                        range_entries.append((
                            "edge_property",
                            [property_name.encode("utf-8")],
                            range_value,
                            e.get_id_bytes,
                        ))
                        if edge_type is not None:
                            range_entries.append((
                                "edge_type_property",
                                [str(edge_type).encode("utf-8"), property_name.encode("utf-8")],
                                range_value,
                                e.get_id_bytes,
                            ))
            # adjacency accum update
            adjacency_accumulator.setdefault(_source, {'target' : [], 'source' : []})
            adjacency_accumulator[_source]['source'].append(e.get_id_bytes)
            adjacency_accumulator.setdefault(_target, {'target' : [], 'source' : []})
            adjacency_accumulator[_target]['target'].append(e.get_id_bytes)
            
        # 3) Use the store's put_edges_bulk
        self.store.put_edges_bulk(edge_dict)
        self.store.put_typed_adjacency_bulk(typed_adjacency_records)
        self._update_typed_adjacency_count_cache(typed_adjacency_records, 1)
        if index_entries:
            self.store.put_index_entries_bulk(index_entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)


        # 4) Build adjacency dict so we do one read+write per node
        #    node_id -> final adjacency (existing + new edges)
        final_adjacency = {}

        # For each node in adjacency_accumulator, fetch old adjacency,
        # union with new edges, and store in final_adjacency dict
        for node_id, new_edges_source_target in adjacency_accumulator.items():

            # raw_adj = self.store.get_adjacency(node_key_serializer(node_id))
            raw_adj = self.store.get_adjacency(node_id)
            # raw_adj = self.store.get_adjacency(node_id)
            if raw_adj is None:
                old_edges = {'source' : set(),'target' : set()}
            else:
                old_edges = self.serializer.deserialize(raw_adj)
                if 'target' not in old_edges:
                    old_edges['target'] = set()
                
                if 'source' not in old_edges:
                    old_edges['source'] = set()
            source_edges = old_edges['source']
            target_edges = old_edges['target']
            if 'source' in new_edges_source_target:
                source_edges = set(source_edges).union(new_edges_source_target['source'])
            if 'target' in new_edges_source_target:
                target_edges = set(target_edges).union(new_edges_source_target['target'])
            # Here we cast to list because some objects do not support set serialization. 
            new_adj_value = {'source' : list(source_edges),'target' : list(target_edges)}
            final_adjacency[node_id] = self.entity_serializer.serialize(new_adj_value,'AdjacencyList')

        # 5) One batch write for adjacency
        self.store.put_adjacency_bulk(final_adjacency)

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
        if not append_only:
            raise NotImplementedError("columnar edge ingestion currently requires append_only=True")
        self._validate_index_mode(index_mode)
        index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        edge_list = EdgeList.from_arrow(edge_ids, sources, targets, edge_types, edge_values)
        with self._ingestion_progress(progress, total=len(edge_list.edge_ids), desc="ingest edges") as progress_bar:
            for chunk in edge_list.chunks(chunk_size):
                self.store.ingest_edges_columnar(
                    chunk,
                    append_only=append_only,
                    native=native,
                    maintain_indexes=index_mode == "maintain",
                )
                self._update_typed_adjacency_count_cache(
                    zip(chunk.sources, chunk.targets, chunk.edge_types, chunk.edge_ids),
                    1,
                )
                if index_mode == "maintain":
                    self._put_edge_property_indexes_for_columnar_chunk(chunk)
                if progress_bar is not None:
                    progress_bar.update(len(chunk.edge_ids))
        if index_mode == "defer":
            self._mark_indexes_stale("edge_type")
            if self.indexed_edge_properties:
                self._mark_indexes_stale("edge_property")
        return len(edge_list.edge_ids)

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
        if not append_only:
            raise NotImplementedError("columnar edge ingestion currently requires append_only=True")
        try:
            import polars as pl
        except ImportError as exc:
            from .ingestion import _missing_dependency_error

            raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_edges_polars") from exc
        self._validate_index_mode(index_mode)
        index_mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        self._validate_polars_frame(pl, df, "GraphDB.ingest_edges_polars")
        total_rows = self._polars_row_count(pl, df)
        total = 0
        with self._ingestion_progress(progress, total=total_rows, desc="ingest edges") as progress_bar:
            for df_chunk in self._iter_polars_chunks(pl, df, chunk_size):
                edge_list = EdgeList.from_polars(
                    df_chunk,
                    edge_id=edge_id,
                    source=source,
                    target=target,
                    edge_type=edge_type,
                    edge_value=edge_value,
                )
                for chunk in edge_list.chunks(chunk_size):
                    self.store.ingest_edges_columnar(
                        chunk,
                        append_only=append_only,
                        native=native,
                        maintain_indexes=index_mode == "maintain",
                    )
                    if index_mode == "maintain":
                        self._put_edge_property_indexes_for_columnar_chunk(chunk)
                    if progress_bar is not None:
                        progress_bar.update(len(chunk.edge_ids))
                total += len(edge_list.edge_ids)
        if index_mode == "defer":
            self._mark_indexes_stale("edge_type")
            if self.indexed_edge_properties:
                self._mark_indexes_stale("edge_property")
        return total

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
        try:
            import polars as pl
        except ImportError as exc:
            from .ingestion import _missing_dependency_error

            raise _missing_dependency_error("polars", feature_name="GraphDB.ingest_edges_polars_entities") from exc
        self._validate_polars_frame(pl, df, "GraphDB.ingest_edges_polars_entities")
        columns = self._polars_columns(pl, df)
        required = [edge_id, source, target, edge_type]
        missing = [column for column in required if column not in columns]
        if missing:
            raise ValueError(f"missing required columns: {', '.join(missing)}")
        if property_columns is None:
            excluded = set(required)
            property_columns = [column for column in columns if column not in excluded]
        missing = [column for column in property_columns if column not in columns]
        if missing:
            raise ValueError(f"missing property columns: {', '.join(missing)}")

        total = 0
        total_rows = self._polars_row_count(pl, df)
        with self._ingestion_progress(progress, total=total_rows, desc="ingest edges") as progress_bar:
            for df_chunk in self._iter_polars_chunks(pl, df, chunk_size):
                if isinstance(self.serializer, JSONSerializer):
                    property_exprs = [pl.col(edge_type).alias("type")]
                    property_exprs.extend(pl.col(column) for column in property_columns if column != "type")
                    value_expr = pl.struct(
                        [
                            pl.col(edge_id).alias("id"),
                            pl.col(source).alias("source"),
                            pl.col(target).alias("target"),
                            pl.struct(property_exprs).alias("properties"),
                        ]
                    ).struct.json_encode()
                    payloads = df_chunk.select(value_expr.alias("edge_value"))["edge_value"].to_arrow().cast("binary")
                else:
                    payloads = []
                    selected = [edge_id, source, target, edge_type, *property_columns]
                    for row in df_chunk.select(selected).rows(named=True):
                        properties = {column: row[column] for column in property_columns if column != "type"}
                        properties["type"] = row[edge_type]
                        payloads.append(
                            self.serialize_edge_value(
                                Edge(
                                    edge_id=row[edge_id],
                                    source=row[source],
                                    target=row[target],
                                    properties=properties,
                                )
                            )
                        )

                total += self.ingest_edges_arrow(
                    df_chunk[edge_id].to_arrow(),
                    df_chunk[source].to_arrow(),
                    df_chunk[target].to_arrow(),
                    df_chunk[edge_type].to_arrow(),
                    payloads,
                    append_only=append_only,
                    native=native,
                    chunk_size=chunk_size,
                    index_mode=index_mode,
                    progress=False,
                )
                if progress_bar is not None:
                    progress_bar.update(df_chunk.height)
        return total

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
        try:
            import polars as pl
        except ImportError:
            pl = None

        if isinstance(self.serializer, JSONSerializer) and pl is not None:
            data = {"edge_id": edge_ids, "source": sources, "target": targets, "edge_type": edge_types}
            for name, column in (properties or {}).items():
                data[name] = column
            return self.ingest_edges_polars_entities(
                pl.DataFrame(data),
                property_columns=list((properties or {}).keys()),
                append_only=append_only,
                native=native,
                chunk_size=chunk_size,
                index_mode=index_mode,
                progress=progress,
            )

        raw_edge_ids = edge_ids.to_pylist() if hasattr(edge_ids, "to_pylist") else list(edge_ids)
        raw_sources = sources.to_pylist() if hasattr(sources, "to_pylist") else list(sources)
        raw_targets = targets.to_pylist() if hasattr(targets, "to_pylist") else list(targets)
        raw_edge_types = edge_types.to_pylist() if hasattr(edge_types, "to_pylist") else list(edge_types)
        raw_properties = {
            name: column.to_pylist() if hasattr(column, "to_pylist") else list(column)
            for name, column in (properties or {}).items()
        }
        payloads = []
        for index, edge_key in enumerate(raw_edge_ids):
            edge_properties = {name: values[index] for name, values in raw_properties.items() if name != "type"}
            edge_properties["type"] = raw_edge_types[index]
            payloads.append(
                self.serialize_edge_value(
                    Edge(
                        edge_id=edge_key,
                        source=raw_sources[index],
                        target=raw_targets[index],
                        properties=edge_properties,
                    )
                )
            )
        return self.ingest_edges_arrow(
            raw_edge_ids,
            raw_sources,
            raw_targets,
            raw_edge_types,
            payloads,
            append_only=append_only,
            native=native,
            chunk_size=chunk_size,
            index_mode=index_mode,
            progress=progress,
        )

    def _put_edge_property_indexes_for_columnar_chunk(self, chunk: EdgeList) -> None:
        """Maintain configured edge property indexes for columnar edges."""
        if not self.indexed_edge_properties:
            return
        entries = []
        range_entries = []
        for edge_value in chunk.edge_values:
            edge = self.entity_serializer.deserialize(edge_value, "Edge")
            edge_type = self.edge_type(edge)
            for property_name in self.indexed_edge_properties:
                if property_name in edge.properties:
                    raw_value = edge.properties[property_name]
                    property_value = _property_value_to_index_bytes(raw_value)
                    entries.append((
                        "edge_property",
                        [property_name.encode("utf-8"), property_value],
                        edge.get_id_bytes,
                    ))
                    if edge_type is not None:
                        entries.append((
                            "edge_type_property",
                            [str(edge_type).encode("utf-8"), property_name.encode("utf-8"), property_value],
                            edge.get_id_bytes,
                        ))
                    range_value = _property_value_to_range_index_bytes(raw_value)
                    if range_value is not None:
                        range_entries.append(("edge_property", [property_name.encode("utf-8")], range_value, edge.get_id_bytes))
                        if edge_type is not None:
                            range_entries.append(("edge_type_property", [str(edge_type).encode("utf-8"), property_name.encode("utf-8")], range_value, edge.get_id_bytes))
        if entries:
            self.store.put_index_entries_bulk(entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)

    # ------------------------
    # Temporal Version History
    # ------------------------

    @staticmethod
    def _temporal_commit_key(commit_id: int) -> bytes:
        return _TEMPORAL_COMMIT_PREFIX + int(commit_id).to_bytes(8, "big", signed=False)

    @staticmethod
    def _temporal_visible_key(commit_id: int) -> bytes:
        return _TEMPORAL_VISIBLE_PREFIX + int(commit_id).to_bytes(8, "big", signed=False)

    @staticmethod
    def _temporal_record_key(commit_id: int, ordinal: int) -> bytes:
        return (
            _TEMPORAL_RECORD_PREFIX
            + int(commit_id).to_bytes(8, "big", signed=False)
            + int(ordinal).to_bytes(4, "big", signed=False)
        )

    @staticmethod
    def _temporal_locator(commit_id: int, ordinal: int) -> bytes:
        return f"{commit_id:016x}{ordinal:08x}".encode("ascii")

    @staticmethod
    def _decode_temporal_locator(locator: bytes) -> tuple[int, int]:
        try:
            if not isinstance(locator, bytes) or len(locator) != 24:
                raise ValueError
            return int(locator[:16], 16), int(locator[16:], 16)
        except (TypeError, ValueError) as exc:
            raise TemporalCorruptionError("invalid temporal index locator") from exc

    @staticmethod
    def _temporal_instant_index_value(instant: TemporalInstant) -> bytes:
        return instant.encode_sortable().hex().encode("ascii")

    def _temporal_index_state(self) -> int | None:
        payload = self.store.get_metadata(_TEMPORAL_INDEX_STATE_KEY)
        if payload is None:
            return None
        try:
            value = json.loads(payload.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError
            if value.get("format_version") in {1, 2}:
                # Later temporal stages added catalogs and epistemic indexes.
                # Older derived indexes must be rebuilt before querying.
                return None
            if value.get("format_version") != 3:
                raise ValueError
            indexed = value["indexed_through_commit"]
            if isinstance(indexed, bool) or not isinstance(indexed, int) or indexed < 0:
                raise ValueError
            return indexed
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TemporalCorruptionError("invalid temporal index state") from exc

    def _persist_temporal_index_state(self, commit_id: int) -> None:
        self.store.put_metadata(
            _TEMPORAL_INDEX_STATE_KEY,
            canonical_json_bytes({"format_version": 3, "indexed_through_commit": commit_id}),
        )

    def _ensure_temporal_indexes(self, horizon: int) -> None:
        self._ensure_indexes_current("temporal")
        indexed = self._temporal_index_state()
        if horizon == 0 and indexed is None:
            return
        if indexed is None or indexed < horizon:
            raise RuntimeError("stale indexes require rebuild before query: temporal")

    def _has_visible_temporal_history(self) -> bool:
        last_commit_id, _ = self._temporal_sequence()
        return any(
            self.store.get_metadata(self._temporal_visible_key(commit_id)) is not None
            for commit_id in range(1, last_commit_id + 1)
        )

    def _temporal_index_entries(self, version):
        if isinstance(version, NodeVersion):
            kind = b"n"
        elif isinstance(version, EdgeVersion):
            kind = b"e"
        else:
            kind = b"c"
        locator = self._temporal_locator(version.commit_id, version.commit_ordinal)
        exact = [(_TEMPORAL_VERSION_INDEX, [version.version_id.encode("ascii")], locator)]
        ranges = [
            (_TEMPORAL_LOGICAL_COMMIT_INDEX, [kind, version.logical_id.encode("utf-8")], locator, locator),
            (
                _TEMPORAL_LOGICAL_VALID_INDEX,
                [kind, version.logical_id.encode("utf-8")],
                self._temporal_instant_index_value(version.valid.start),
                locator,
            ),
        ]
        if isinstance(version, NodeVersion):
            ranges.append(
                (
                    _TEMPORAL_NODE_CATALOG_INDEX,
                    [b"nodes"],
                    self._temporal_instant_index_value(version.valid.start),
                    locator,
                )
            )
        if isinstance(version, EdgeVersion) and version.edge is not None:
            ranges.extend([
                (
                    _TEMPORAL_EDGE_OUT_CATALOG_INDEX,
                    [str(version.edge.source).encode("utf-8")],
                    self._temporal_instant_index_value(version.valid.start),
                    locator,
                ),
                (
                    _TEMPORAL_EDGE_IN_CATALOG_INDEX,
                    [str(version.edge.target).encode("utf-8")],
                    self._temporal_instant_index_value(version.valid.start),
                    locator,
                ),
            ])
            edge_type = version.edge.properties.get("type")
            if edge_type is not None:
                ranges.extend([
                    (
                        _TEMPORAL_EDGE_OUT_INDEX,
                        [str(version.edge.source).encode("utf-8"), str(edge_type).encode("utf-8")],
                        self._temporal_instant_index_value(version.valid.start),
                        locator,
                    ),
                    (
                        _TEMPORAL_EDGE_IN_INDEX,
                        [str(version.edge.target).encode("utf-8"), str(edge_type).encode("utf-8")],
                        self._temporal_instant_index_value(version.valid.start),
                        locator,
                    ),
                ])
        if isinstance(version, ClaimVersion) and version.claim is not None:
            claim = version.claim
            start = self._temporal_instant_index_value(version.valid.start)
            ranges.extend([
                (_TEMPORAL_CLAIM_CATALOG_INDEX, [b"claims"], start, locator),
                (
                    _TEMPORAL_CLAIM_STATEMENT_INDEX,
                    [claim.statement_id.encode("ascii")],
                    start,
                    locator,
                ),
            ])
            dimensions = {
                "subject": claim.subject,
                "predicate": claim.predicate,
                "agent": claim.agent,
                "source": claim.source,
                "world": claim.world,
                "polarity": claim.polarity.value,
                "object_kind": claim.object_kind.value,
            }
            for name, value in dimensions.items():
                ranges.append((
                    _TEMPORAL_CLAIM_DIMENSION_INDEX,
                    [name.encode("ascii"), canonical_json_bytes(value)],
                    start,
                    locator,
                ))
        return exact, ranges

    def _write_temporal_indexes(self, versions, commit_id: int) -> dict[str, int]:
        exact_entries = []
        range_entries = []
        for version in versions:
            exact, ranges = self._temporal_index_entries(version)
            exact_entries.extend(exact)
            range_entries.extend(ranges)
        if exact_entries:
            self.store.put_index_entries_bulk(exact_entries)
        if range_entries:
            self.store.put_range_index_entries_bulk(range_entries)
        commit = versions[0] if versions else None
        if commit is not None:
            self.store.put_range_index_entry(
                _TEMPORAL_SYSTEM_INDEX,
                [b"commit"],
                self._temporal_instant_index_value(commit.system_time),
                f"{commit_id:016x}".encode("ascii"),
            )
        self._persist_temporal_index_state(commit_id)
        return {"temporal_exact": len(exact_entries), "temporal_range": len(range_entries) + (1 if commit else 0)}

    def _temporal_sequence(self) -> tuple[int, TemporalInstant | None]:
        try:
            payload = self.store.get_metadata(_TEMPORAL_SEQUENCE_KEY)
        except NotImplementedError as exc:
            raise TemporalVersionError("the configured store does not support temporal metadata") from exc
        if payload is None:
            return 0, None
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise TypeError
            commit_id = decoded["commit_id"]
            system_time_us = decoded["system_time_us"]
            if isinstance(commit_id, bool) or not isinstance(commit_id, int):
                raise TypeError
            if isinstance(system_time_us, bool) or not isinstance(system_time_us, int):
                raise TypeError
            system_time = TemporalInstant(system_time_us)
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TemporalCorruptionError("invalid temporal commit sequence") from exc
        if commit_id < 0:
            raise TemporalCorruptionError("invalid temporal commit sequence")
        return commit_id, system_time

    def _next_temporal_system_time(self, previous: TemporalInstant | None) -> TemporalInstant:
        current = self._temporal_clock()
        if isinstance(current, TemporalInstant):
            instant = current
        else:
            instant = TemporalInstant.from_datetime(current)
        if previous is not None and instant <= previous:
            return TemporalInstant(previous.epoch_microseconds + 1)
        return instant

    # ------------------------
    # Positive Horn Rules
    # ------------------------

    def _load_rule_catalog(self) -> tuple[RuleVersion, ...]:
        payload = self.store.get_metadata(_RULE_CATALOG_KEY)
        if payload is None:
            return ()
        try:
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict) or decoded.get("format_version") != 1:
                raise TypeError
            versions = tuple(RuleVersion.from_dict(item) for item in decoded["versions"])
            if [item.catalog_ordinal for item in versions] != list(range(1, len(versions) + 1)):
                raise ValueError
            if len({item.version_id for item in versions}) != len(versions):
                raise ValueError
            return versions
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuleError("invalid rule catalog") from exc

    def create_rule(self, rule_id, *, when, then, version_id=None) -> RuleVersion:
        """Validate and append one immutable version of a positive Horn rule."""
        with self._temporal_write_lock:
            catalog = self._load_rule_catalog()
            normalized_version_id = normalize_version_id(version_id)
            if any(item.version_id == normalized_version_id for item in catalog):
                raise RuleError(f"rule version ID already exists: {normalized_version_id}")
            previous_time = catalog[-1].system_time if catalog else None
            system_time = self._next_temporal_system_time(previous_time)
            rule = normalize_rule_definition(
                rule_id,
                when,
                then,
                version_id=normalized_version_id,
                system_time=system_time,
                catalog_ordinal=len(catalog) + 1,
            )
            self.store.put_metadata(
                _RULE_CATALOG_KEY,
                canonical_json_bytes({
                    "format_version": 1,
                    "versions": [item.to_dict() for item in (*catalog, rule)],
                }),
            )
            return rule

    def iter_rule_versions(self, rule_id=None, *, system_time=None):
        """Iterate immutable rule versions in catalog order at a system-time horizon."""
        if rule_id is not None and (not isinstance(rule_id, str) or not rule_id):
            raise RuleError("rule_id must be a non-empty string")
        horizon = None if system_time is None else normalize_temporal_instant(system_time)
        for rule in self._load_rule_catalog():
            if horizon is not None and rule.system_time > horizon:
                continue
            if rule_id is None or rule.rule_id == rule_id:
                yield rule

    def get_rule(self, rule_id, *, system_time=None) -> RuleVersion | None:
        """Return the latest version of a named rule at a system-time horizon."""
        versions = tuple(self.iter_rule_versions(rule_id, system_time=system_time))
        return versions[-1] if versions else None

    @staticmethod
    def _rule_justifications(provenance) -> set[tuple[str, tuple[str, ...]]]:
        if not isinstance(provenance, Mapping) or provenance.get("derived_by") != "gestaltdb.rules":
            return set()
        result = set()
        values = provenance.get("justifications", ())
        if not isinstance(values, (tuple, list)):
            return result
        for value in values:
            if not isinstance(value, Mapping):
                continue
            try:
                justification = RuleJustification(
                    value["rule_version_id"], tuple(value["premise_version_ids"])
                )
            except (KeyError, TypeError, ValueError):
                continue
            result.add((justification.rule_version_id, justification.premise_version_ids))
        return result

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

        Only positive entity-object claims participate. Premises must share a
        world, and each conclusion receives their half-open validity
        intersection. No claims are written if any resource bound is exceeded.
        """
        limits = {
            "max_iterations": max_iterations,
            "max_derivations": max_derivations,
            "max_justifications": max_justifications,
        }
        for name, value in limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if world is not None and (not isinstance(world, str) or not world):
            raise ValueError("world must be a non-empty string or None")
        mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        if mode not in {item.value for item in IndexMaintenanceMode}:
            raise ValueError("index_mode must be a valid IndexMaintenanceMode")

        instant = normalize_temporal_instant(as_of)
        horizon = self._temporal_system_horizon(system_time=system_time)
        rules_by_id = {}
        for rule in self.iter_rule_versions(system_time=system_time):
            rules_by_id[rule.rule_id] = rule
        rules = tuple(sorted(rules_by_id.values(), key=lambda item: item.rule_id))
        if not rules:
            return RuleRunResult(0, 0, 0, None, ())

        facts = []
        facts_by_predicate = {}
        existing_derived = {}

        def conclusion_key(subject, predicate, object_value, claim_world):
            return subject, predicate, object_value, claim_world

        def union_validity(left, right):
            start = min(left.start, right.start)
            if left.end is None or right.end is None:
                end = None
            else:
                end = max(left.end, right.end)
            return TemporalInterval(start, end)

        def add_fact(version_id, claim, valid):
            fact = {
                "version_id": version_id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "object": claim.object,
                "world": claim.world,
                "valid": valid,
            }
            facts.append(fact)
            facts_by_predicate.setdefault(claim.predicate, []).append(fact)
            return fact

        for version in self.iter_claims_as_of(
            valid_time=instant,
            polarity=ClaimPolarity.POSITIVE,
            world=world,
            through_commit=horizon,
        ):
            claim = version.claim
            if claim is None or claim.object_kind is not ClaimObjectKind.ENTITY:
                continue
            fact = add_fact(version.version_id, claim, version.valid)
            if claim.agent == _RULE_ENGINE_AGENT and claim.provenance.get("derived_by") == "gestaltdb.rules":
                existing_derived[conclusion_key(
                    claim.subject, claim.predicate, claim.object, claim.world
                )] = (version, fact)

        delta_ids = {fact["version_id"] for fact in facts}
        outputs = {}
        generated_justifications = set()
        generated_count = 0
        iterations = 0

        def unify(term, value, bindings):
            if not term.startswith("?"):
                return bindings if term == value else None
            current = bindings.get(term, _UNSET)
            if current is not _UNSET:
                return bindings if current == value else None
            updated = dict(bindings)
            updated[term] = value
            return updated

        while delta_ids:
            if iterations >= max_iterations:
                raise RuleEvaluationLimitError(
                    f"rule evaluation exceeded max_iterations={max_iterations}"
                )
            next_delta = {}
            for rule in rules:
                rows = [({}, (), None, None, False)]
                for atom in rule.when:
                    joined = []
                    for bindings, premises, valid, row_world, used_delta in rows:
                        for fact in facts_by_predicate.get(atom.predicate, ()):
                            if row_world is not None and fact["world"] != row_world:
                                continue
                            subject_bindings = unify(atom.subject, fact["subject"], bindings)
                            if subject_bindings is None:
                                continue
                            bound = unify(atom.object, fact["object"], subject_bindings)
                            if bound is None:
                                continue
                            intersection = fact["valid"] if valid is None else valid.intersection(fact["valid"])
                            if intersection is None:
                                continue
                            joined.append((
                                bound,
                                (*premises, fact["version_id"]),
                                intersection,
                                fact["world"],
                                used_delta or fact["version_id"] in delta_ids,
                            ))
                    rows = joined
                    if not rows:
                        break
                for bindings, premises, valid, row_world, used_delta in rows:
                    if not used_delta:
                        continue
                    subject = bindings.get(rule.then.subject, rule.then.subject)
                    object_value = bindings.get(rule.then.object, rule.then.object)
                    key = conclusion_key(subject, rule.then.predicate, object_value, row_world)
                    justification = (rule.version_id, premises)
                    marker = (key, justification)
                    output = outputs.get(key)
                    if output is None:
                        existing_entry = existing_derived.get(key)
                        existing = None if existing_entry is None else existing_entry[0]
                        version_id = (
                            existing.version_id if existing is not None else normalize_version_id()
                        )
                        output = {
                            "existing": existing,
                            "version_id": version_id,
                            "subject": subject,
                            "predicate": rule.then.predicate,
                            "object": object_value,
                            "world": row_world,
                            "valid": existing.valid if existing is not None else valid,
                            "fact": None if existing_entry is None else existing_entry[1],
                            "justifications": set(),
                        }
                        outputs[key] = output
                        if existing is None:
                            generated_count += 1
                            if generated_count > max_derivations:
                                raise RuleEvaluationLimitError(
                                    f"rule evaluation exceeded max_derivations={max_derivations}"
                                )
                            next_delta[key] = output
                    merged_valid = union_validity(output["valid"], valid)
                    if merged_valid != output["valid"]:
                        output["valid"] = merged_valid
                        next_delta[key] = output
                    if marker in generated_justifications:
                        continue
                    generated_justifications.add(marker)
                    if len(generated_justifications) > max_justifications:
                        raise RuleEvaluationLimitError(
                            f"rule evaluation exceeded max_justifications={max_justifications}"
                        )
                    output["justifications"].add(justification)
            iterations += 1
            added_facts = []
            for output in next_delta.values():
                fact = output["fact"]
                if fact is None:
                    claim = Claim(
                        output["subject"], output["predicate"], output["object"],
                        ClaimPolarity.POSITIVE, _RULE_ENGINE_AGENT,
                        world=output["world"],
                        provenance={"derived_by": "gestaltdb.rules"},
                    )
                    fact = add_fact(output["version_id"], claim, output["valid"])
                    output["fact"] = fact
                else:
                    fact["valid"] = output["valid"]
                added_facts.append(fact)
            delta_ids = {fact["version_id"] for fact in added_facts}

        writes = []
        output_order = sorted(outputs)
        for key in output_order:
            output = outputs[key]
            existing = output["existing"]
            justifications = set(output["justifications"])
            if existing is not None:
                justifications.update(self._rule_justifications(existing.claim.provenance))
            encoded_justifications = [
                RuleJustification(rule_version_id, premise_ids).to_dict()
                for rule_version_id, premise_ids in sorted(justifications)
            ]
            if (
                existing is not None
                and output["valid"] == existing.valid
                and justifications == self._rule_justifications(existing.claim.provenance)
            ):
                continue
            claim = Claim(
                output["subject"],
                output["predicate"],
                output["object"],
                ClaimPolarity.POSITIVE,
                _RULE_ENGINE_AGENT,
                world=output["world"],
                provenance={
                    "derived_by": "gestaltdb.rules",
                    "justifications": encoded_justifications,
                },
            )
            if existing is None:
                writes.append(ClaimVersionWrite.assertion(
                    claim, output["valid"], version_id=output["version_id"]
                ))
            else:
                writes.append(ClaimVersionWrite.correction(
                    claim,
                    supersedes_version_id=existing.version_id,
                    valid=output["valid"],
                ))

        if not writes:
            return RuleRunResult(iterations, 0, len(generated_justifications), None, ())
        commit = self.commit_versions(
            writes,
            metadata={
                "rule_run": {
                    "as_of_us": instant.epoch_microseconds,
                    "input_through_commit": horizon,
                    "iterations": iterations,
                }
            },
            index_mode=index_mode,
        )
        return RuleRunResult(
            iterations,
            len(commit.versions),
            len(generated_justifications),
            commit.commit_id,
            commit.versions,
        )

    def _last_truth_maintenance_instant(self) -> TemporalInstant | None:
        payload = self.store.get_metadata(_TRUTH_MAINTENANCE_STATE_KEY)
        state_instant = None
        state_horizon = -1
        if payload is not None:
            try:
                state = json.loads(payload.decode("utf-8"))
                if state.get("format_version") == 1:
                    state_instant = TemporalInstant(state["valid_time_us"])
                    state_horizon = state["through_commit"]
                    if isinstance(state_horizon, bool) or not isinstance(state_horizon, int):
                        raise TypeError
            except (AttributeError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                state_instant = None
                state_horizon = -1
        last_commit_id, _ = self._temporal_sequence()
        for commit_id in range(last_commit_id, 0, -1):
            commit = self.get_temporal_commit(commit_id)
            if commit is None:
                continue
            for name in ("truth_maintenance", "rule_run"):
                value = commit.metadata.get(name)
                if isinstance(value, Mapping) and isinstance(value.get("as_of_us"), int):
                    if commit_id > state_horizon:
                        return TemporalInstant(value["as_of_us"])
                    return state_instant
        return state_instant

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
        removed. If ``as_of`` is omitted, the valid time from the most recent
        rule run or maintenance pass is resumed.
        """
        limits = {
            "max_iterations": max_iterations,
            "max_derivations": max_derivations,
            "max_justifications": max_justifications,
        }
        for name, value in limits.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if world is not None and (not isinstance(world, str) or not world):
            raise ValueError("world must be a non-empty string or None")
        mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
        if mode not in {item.value for item in IndexMaintenanceMode}:
            raise ValueError("index_mode must be a valid IndexMaintenanceMode")
        instant = self._last_truth_maintenance_instant() if as_of is None else normalize_temporal_instant(as_of)
        if instant is None:
            raise ValueError("as_of is required for the first truth-maintenance pass")

        with self._temporal_write_lock:
            input_horizon = self._temporal_system_horizon(system_time=system_time)
            rules_by_id = {}
            for rule in self.iter_rule_versions(system_time=system_time):
                rules_by_id[rule.rule_id] = rule
            rules = tuple(sorted(rules_by_id.values(), key=lambda item: item.rule_id))

            facts = []
            facts_by_predicate = {}
            existing = {}

            def conclusion_key(subject, predicate, object_value, claim_world):
                return subject, predicate, object_value, claim_world

            def add_fact(reference, subject, predicate, object_value, claim_world, valid):
                fact = {
                    "ref": reference,
                    "subject": subject,
                    "predicate": predicate,
                    "object": object_value,
                    "world": claim_world,
                    "valid": valid,
                }
                facts.append(fact)
                facts_by_predicate.setdefault(predicate, []).append(fact)
                return fact

            for version in self.iter_claims_as_of(
                valid_time=instant,
                polarity=ClaimPolarity.POSITIVE,
                world=world,
                through_commit=input_horizon,
            ):
                claim = version.claim
                if claim is None or claim.object_kind is not ClaimObjectKind.ENTITY:
                    continue
                key = conclusion_key(claim.subject, claim.predicate, claim.object, claim.world)
                if claim.agent == _RULE_ENGINE_AGENT and claim.provenance.get("derived_by") == "gestaltdb.rules":
                    existing[key] = version
                    continue
                add_fact(
                    ("version", version.version_id),
                    claim.subject,
                    claim.predicate,
                    claim.object,
                    claim.world,
                    version.valid,
                )

            def union_validity(left, right):
                start = min(left.start, right.start)
                end = None if left.end is None or right.end is None else max(left.end, right.end)
                return TemporalInterval(start, end)

            def unify(term, value, bindings):
                if not term.startswith("?"):
                    return bindings if term == value else None
                current = bindings.get(term, _UNSET)
                if current is not _UNSET:
                    return bindings if current == value else None
                updated = dict(bindings)
                updated[term] = value
                return updated

            outputs = {}
            delta = {fact["ref"] for fact in facts}
            iterations = 0
            support_count = 0
            while delta:
                if iterations >= max_iterations:
                    raise RuleEvaluationLimitError(
                        f"truth maintenance exceeded max_iterations={max_iterations}"
                    )
                next_delta = {}
                for rule in rules:
                    rows = [({}, (), None, None, False)]
                    for atom in rule.when:
                        joined = []
                        for bindings, premises, valid, row_world, used_delta in rows:
                            for fact in facts_by_predicate.get(atom.predicate, ()):
                                if row_world is not None and fact["world"] != row_world:
                                    continue
                                subject_bindings = unify(atom.subject, fact["subject"], bindings)
                                if subject_bindings is None:
                                    continue
                                bound = unify(atom.object, fact["object"], subject_bindings)
                                if bound is None:
                                    continue
                                intersection = fact["valid"] if valid is None else valid.intersection(fact["valid"])
                                if intersection is None:
                                    continue
                                joined.append((
                                    bound,
                                    (*premises, fact["ref"]),
                                    intersection,
                                    fact["world"],
                                    used_delta or fact["ref"] in delta,
                                ))
                        rows = joined
                        if not rows:
                            break
                    for bindings, premises, valid, row_world, used_delta in rows:
                        if not used_delta:
                            continue
                        subject = bindings.get(rule.then.subject, rule.then.subject)
                        object_value = bindings.get(rule.then.object, rule.then.object)
                        key = conclusion_key(subject, rule.then.predicate, object_value, row_world)
                        output = outputs.get(key)
                        if output is None:
                            if len(outputs) >= max_derivations:
                                raise RuleEvaluationLimitError(
                                    f"truth maintenance exceeded max_derivations={max_derivations}"
                                )
                            output = {
                                "subject": subject,
                                "predicate": rule.then.predicate,
                                "object": object_value,
                                "world": row_world,
                                "valid": valid,
                                "supports": set(),
                                "fact": None,
                            }
                            outputs[key] = output
                            next_delta[key] = output
                        merged = union_validity(output["valid"], valid)
                        if merged != output["valid"]:
                            output["valid"] = merged
                            next_delta[key] = output
                        support = (rule.version_id, premises)
                        if support not in output["supports"]:
                            support_count += 1
                            if support_count > max_justifications:
                                raise RuleEvaluationLimitError(
                                    f"truth maintenance exceeded max_justifications={max_justifications}"
                                )
                            output["supports"].add(support)
                iterations += 1
                added = []
                for key, output in next_delta.items():
                    fact = output["fact"]
                    if fact is None:
                        fact = add_fact(
                            ("derived", key),
                            output["subject"],
                            output["predicate"],
                            output["object"],
                            output["world"],
                            output["valid"],
                        )
                        output["fact"] = fact
                    else:
                        fact["valid"] = output["valid"]
                    added.append(fact)
                delta = {fact["ref"] for fact in added}

            def semantic_premise(version_id):
                premise = self.get_claim_version(version_id, through_commit=input_horizon)
                if (
                    premise is not None
                    and premise.claim is not None
                    and premise.claim.agent == _RULE_ENGINE_AGENT
                    and premise.claim.provenance.get("derived_by") == "gestaltdb.rules"
                ):
                    claim = premise.claim
                    return ("derived", conclusion_key(
                        claim.subject, claim.predicate, claim.object, claim.world
                    ))
                return ("version", version_id)

            def existing_semantic_supports(version):
                return {
                    (rule_id, tuple(semantic_premise(premise) for premise in premises))
                    for rule_id, premises in self._rule_justifications(version.claim.provenance)
                }

            changed = set()
            for key, output in outputs.items():
                current = existing.get(key)
                if (
                    current is None
                    or current.valid != output["valid"]
                    or existing_semantic_supports(current) != output["supports"]
                ):
                    changed.add(key)
            propagated = True
            while propagated:
                propagated = False
                for key, output in outputs.items():
                    if key in changed:
                        continue
                    if any(
                        reference[0] == "derived" and reference[1] in changed
                        for _, premises in output["supports"]
                        for reference in premises
                    ):
                        changed.add(key)
                        propagated = True

            result_ids = {}
            for key in outputs:
                current = existing.get(key)
                result_ids[key] = (
                    normalize_version_id()
                    if current is None or key in changed
                    else current.version_id
                )

            def encoded_supports(output):
                encoded = []
                for rule_id, premises in sorted(output["supports"]):
                    premise_ids = tuple(
                        reference[1]
                        if reference[0] == "version"
                        else result_ids[reference[1]]
                        for reference in premises
                    )
                    encoded.append(RuleJustification(rule_id, premise_ids).to_dict())
                return encoded

            writes = []
            operations = []
            for key in sorted(outputs):
                if key not in changed:
                    continue
                output = outputs[key]
                claim = Claim(
                    output["subject"],
                    output["predicate"],
                    output["object"],
                    ClaimPolarity.POSITIVE,
                    _RULE_ENGINE_AGENT,
                    world=output["world"],
                    provenance={
                        "derived_by": "gestaltdb.rules",
                        "justifications": encoded_supports(output),
                    },
                )
                current = existing.get(key)
                if current is None:
                    writes.append(ClaimVersionWrite.assertion(
                        claim, output["valid"], version_id=result_ids[key]
                    ))
                    operations.append("assert")
                else:
                    writes.append(ClaimVersionWrite.correction(
                        claim,
                        supersedes_version_id=current.version_id,
                        valid=output["valid"],
                        version_id=result_ids[key],
                    ))
                    operations.append("correct")
                    if current.valid.start < output["valid"].start:
                        writes.append(ClaimVersionWrite.retraction(
                            current.logical_id,
                            supersedes_version_id=current.version_id,
                            valid=TemporalInterval(
                                current.valid.start, output["valid"].start
                            ),
                            reason="derived validity no longer supported",
                        ))
                    if output["valid"].end is not None and (
                        current.valid.end is None
                        or current.valid.end > output["valid"].end
                    ):
                        writes.append(ClaimVersionWrite.retraction(
                            current.logical_id,
                            supersedes_version_id=current.version_id,
                            valid=TemporalInterval(
                                output["valid"].end, current.valid.end
                            ),
                            reason="derived validity no longer supported",
                        ))
            for key in sorted(set(existing) - set(outputs)):
                current = existing[key]
                writes.append(ClaimVersionWrite.retraction(
                    current.logical_id,
                    supersedes_version_id=current.version_id,
                    valid=current.valid,
                    reason="final rule support disappeared",
                ))
                operations.append("retract")

            commit = None
            if writes:
                commit = self.commit_versions(
                    writes,
                    metadata={
                        "truth_maintenance": {
                            "as_of_us": instant.epoch_microseconds,
                            "input_through_commit": input_horizon,
                            "iterations": iterations,
                        }
                    },
                    index_mode=index_mode,
                )

            premise_dependents = {}
            rule_dependents = {}
            derived = {}
            for key, output in outputs.items():
                claim = Claim(
                    output["subject"], output["predicate"], output["object"],
                    ClaimPolarity.POSITIVE, _RULE_ENGINE_AGENT, world=output["world"],
                )
                derived[claim.claim_id] = {
                    "version_id": result_ids[key],
                    "support_count": len(output["supports"]),
                }
                for justification in encoded_supports(output):
                    rule_dependents.setdefault(justification["rule_version_id"], set()).add(claim.claim_id)
                    for premise_id in justification["premise_version_ids"]:
                        premise_dependents.setdefault(premise_id, set()).add(claim.claim_id)
            state_horizon = commit.commit_id if commit is not None else input_horizon
            self.store.put_metadata(_TRUTH_MAINTENANCE_STATE_KEY, canonical_json_bytes({
                "format_version": 1,
                "valid_time_us": instant.epoch_microseconds,
                "through_commit": state_horizon,
                "derived": derived,
                "premise_dependents": {
                    key: sorted(value) for key, value in sorted(premise_dependents.items())
                },
                "rule_dependents": {
                    key: sorted(value) for key, value in sorted(rule_dependents.items())
                },
            }))
            versions = () if commit is None else commit.versions
            return TruthMaintenanceResult(
                input_horizon,
                None if commit is None else commit.commit_id,
                operations.count("assert"),
                operations.count("correct"),
                operations.count("retract"),
                support_count,
                versions,
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
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
            raise ValueError("max_depth must be a non-negative integer")
        if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
            raise ValueError("max_nodes must be a positive integer")
        instant = normalize_temporal_instant(valid_time)
        horizon = self._temporal_system_horizon(system_time=system_time)
        root = self.get_claim_as_of(claim_id, valid_time=instant, through_commit=horizon)
        if root is None:
            return None
        commit = self.get_temporal_commit(horizon)
        if commit is None:
            raise TemporalCorruptionError("explanation horizon has no visible commit")
        rules = {rule.version_id: rule for rule in self.iter_rule_versions()}
        nodes = {}
        edges = set()
        expanded = set()
        truncated = False

        def add_node(node_id, kind, value):
            nonlocal truncated
            if node_id in nodes:
                return True
            if len(nodes) >= max_nodes:
                truncated = True
                return False
            nodes[node_id] = ExplanationNode(node_id, kind, value)
            return True

        def visit(version, depth):
            nonlocal truncated
            if not add_node(version.version_id, "claim", version):
                return
            if version.version_id in expanded:
                return
            expanded.add(version.version_id)
            supports = self._rule_justifications(version.claim.provenance)
            if not supports:
                return
            if depth >= max_depth:
                truncated = True
                return
            for rule_id, premise_ids in sorted(supports):
                rule = rules.get(rule_id)
                if rule is None or not add_node(rule_id, "rule", rule):
                    truncated = True
                    continue
                edges.add(ExplanationEdge(version.version_id, rule_id, "derived_by"))
                for ordinal, premise_id in enumerate(premise_ids):
                    premise = self.get_claim_version(premise_id, through_commit=horizon)
                    if premise is None:
                        truncated = True
                        continue
                    if not add_node(premise_id, "claim", premise):
                        continue
                    edges.add(ExplanationEdge(rule_id, premise_id, "premise", ordinal))
                    visit(premise, depth + 1)

        visit(root, 0)
        ordered_nodes = tuple(nodes[key] for key in sorted(nodes))
        ordered_edges = tuple(sorted(
            edges,
            key=lambda edge: (
                edge.source_id,
                edge.target_id,
                edge.relationship,
                -1 if edge.premise_ordinal is None else edge.premise_ordinal,
            ),
        ))
        return ClaimExplanation(
            root.logical_id,
            root.version_id,
            instant,
            commit.system_time,
            ordered_nodes,
            ordered_edges,
            truncated,
        )

    def put_node_version(
        self, node: Node, *, valid, version_id=None, metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> NodeVersion:
        """Append an immutable node assertion without changing the current graph."""
        commit = self.commit_versions(
            [NodeVersionWrite.assertion(node, valid, version_id=version_id)],
            metadata=metadata, index_mode=index_mode,
        )
        return commit.versions[0]  # type: ignore[return-value]

    def put_edge_version(
        self, edge: Edge, *, valid, version_id=None, metadata=None,
        index_mode=IndexMaintenanceMode.MAINTAIN,
    ) -> EdgeVersion:
        """Append an immutable edge assertion without changing the current graph."""
        commit = self.commit_versions(
            [EdgeVersionWrite.assertion(edge, valid, version_id=version_id)],
            metadata=metadata, index_mode=index_mode,
        )
        return commit.versions[0]  # type: ignore[return-value]

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
        commit = self.commit_versions([
            NodeVersionWrite.correction(
                node,
                supersedes_version_id=supersedes_version_id,
                valid=valid,
                version_id=version_id,
            )
        ], metadata=metadata, index_mode=index_mode)
        return commit.versions[0]  # type: ignore[return-value]

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
        commit = self.commit_versions([
            EdgeVersionWrite.correction(
                edge,
                supersedes_version_id=supersedes_version_id,
                valid=valid,
                version_id=version_id,
            )
        ], metadata=metadata, index_mode=index_mode)
        return commit.versions[0]  # type: ignore[return-value]

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
        """Append a node retraction over an explicit or inherited interval."""
        commit = self.commit_versions([
            NodeVersionWrite.retraction(
                logical_id,
                valid=valid,
                valid_from=valid_from,
                supersedes_version_id=supersedes_version_id,
                reason=reason,
                version_id=version_id,
            )
        ], metadata=metadata, index_mode=index_mode)
        return commit.versions[0]  # type: ignore[return-value]

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
        """Append an edge retraction over an explicit or inherited interval."""
        commit = self.commit_versions([
            EdgeVersionWrite.retraction(
                logical_id,
                valid=valid,
                valid_from=valid_from,
                supersedes_version_id=supersedes_version_id,
                reason=reason,
                version_id=version_id,
            )
        ], metadata=metadata, index_mode=index_mode)
        return commit.versions[0]  # type: ignore[return-value]

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
        interval = self._claim_valid_interval(valid, valid_from, valid_to, required=True)
        claim = Claim(
            subject=subject,
            predicate=predicate,
            object=object,
            object_kind=object_kind,
            polarity=polarity,
            agent=agent,
            source=source,
            confidence=confidence,
            world=world,
            provenance={} if provenance is None else provenance,
        )
        commit = self.commit_versions(
            [ClaimVersionWrite.assertion(claim, interval, version_id=version_id)],
            metadata=metadata,
            index_mode=index_mode,
        )
        return commit.versions[0]  # type: ignore[return-value]

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
        """Append corrected confidence/provenance for one claim identity."""
        target = self._get_temporal_version(normalize_version_id(supersedes_version_id))
        if not isinstance(target, ClaimVersion) or target.claim is None:
            raise TemporalVersionError("superseded claim version does not exist")
        if isinstance(claim, Claim):
            corrected = claim
        else:
            claim_id = normalize_logical_id(claim)
            if claim_id != target.logical_id:
                raise TemporalVersionError("superseded claim version has a different claim ID")
            payload = target.claim.to_dict()
            if confidence is not _UNSET:
                payload["confidence"] = confidence
            if provenance is not _UNSET:
                payload["provenance"] = provenance
            corrected = Claim.from_dict(payload)
        if corrected.claim_id != target.logical_id:
            raise TemporalVersionError("claim corrections cannot change epistemic identity")
        interval = self._claim_valid_interval(valid, valid_from, valid_to, required=False)
        commit = self.commit_versions([
            ClaimVersionWrite.correction(
                corrected,
                supersedes_version_id=supersedes_version_id,
                valid=interval,
                version_id=version_id,
            )
        ], metadata=metadata, index_mode=index_mode)
        return commit.versions[0]  # type: ignore[return-value]

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
        """Append a bitemporal retraction without deleting claim history."""
        interval = self._claim_valid_interval(valid, valid_from, valid_to, required=False)
        commit = self.commit_versions([
            ClaimVersionWrite.retraction(
                claim_id,
                valid=interval,
                supersedes_version_id=supersedes_version_id,
                reason=reason,
                version_id=version_id,
            )
        ], metadata=metadata, index_mode=index_mode)
        return commit.versions[0]  # type: ignore[return-value]

    @staticmethod
    def _claim_valid_interval(valid, valid_from, valid_to, *, required):
        if valid is not None and (valid_from is not None or valid_to is not None):
            raise TemporalVersionError("provide either valid or valid_from/valid_to, not both")
        if valid is not None:
            return normalize_temporal_interval(valid)
        if valid_from is None:
            if valid_to is not None:
                raise TemporalVersionError("valid_to requires valid_from")
            if required:
                raise TemporalVersionError("claim assertion requires valid or valid_from")
            return None
        return TemporalInterval(
            normalize_temporal_instant(valid_from),
            None if valid_to is None else normalize_temporal_instant(valid_to),
        )

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
        if getattr(self.store, "supports_transactions", False) and not self._temporal_transaction_bound:
            with self.transaction() as transaction:
                result = transaction._commit_versions_direct(writes, normalized_metadata, write_mode)
        else:
            with self._temporal_write_lock:
                result = self._commit_versions_direct(writes, normalized_metadata, write_mode)
        if rebuild_after:
            self.rebuild_temporal_indexes()
        return result

    def _commit_versions_direct(self, writes, metadata, index_mode) -> TemporalCommit:
        previous_commit_id, previous_system_time = self._temporal_sequence()
        commit_id = previous_commit_id + 1
        if commit_id >= 1 << 64:
            raise TemporalVersionError("temporal commit ID space is exhausted")
        system_time = self._next_temporal_system_time(previous_system_time)

        prepared = []
        batch_version_ids = set()
        for ordinal, write in enumerate(writes):
            if ordinal >= 1 << 32:
                raise TemporalVersionError("temporal commit contains too many versions")
            if not isinstance(write, (NodeVersionWrite, EdgeVersionWrite, ClaimVersionWrite)):
                raise TypeError(
                    "writes must contain NodeVersionWrite, EdgeVersionWrite, or ClaimVersionWrite values"
                )
            if not isinstance(write.operation, VersionOperation):
                raise TemporalVersionError("temporal version operation is invalid")
            valid_input = write.valid
            if valid_input is not None:
                try:
                    valid_input = normalize_temporal_interval(valid_input)
                except (TypeError, ValueError) as exc:
                    raise TemporalVersionError("temporal version validity interval is invalid") from exc
            version_id = normalize_version_id(write.version_id)
            if version_id in batch_version_ids or self._temporal_version_id_exists(version_id):
                raise TemporalVersionError(f"temporal version ID already exists: {version_id}")
            batch_version_ids.add(version_id)
            logical_id = normalize_logical_id(write.logical_id)
            target = None
            supersedes_version_id = None
            if write.supersedes_version_id is not None:
                supersedes_version_id = normalize_version_id(write.supersedes_version_id)
                target = self._get_temporal_version(supersedes_version_id)
                if target is None:
                    raise TemporalVersionError("superseded temporal version does not exist")
                expected_type = {
                    NodeVersionWrite: NodeVersion,
                    EdgeVersionWrite: EdgeVersion,
                    ClaimVersionWrite: ClaimVersion,
                }[type(write)]
                if not isinstance(target, expected_type) or target.logical_id != logical_id:
                    raise TemporalVersionError("superseded temporal version has a different kind or logical ID")
            if write.operation is VersionOperation.ASSERT and write.supersedes_version_id is not None:
                raise TemporalVersionError("assertions cannot supersede another version")
            if write.operation is not VersionOperation.RETRACT and write.reason is not None:
                raise TemporalVersionError("only retractions may include a reason")
            if write.reason is not None and (not isinstance(write.reason, str) or not write.reason):
                raise TemporalVersionError("retraction reason must be a non-empty string")
            if write.operation is VersionOperation.CORRECT and target is None:
                raise TemporalVersionError("corrections require a superseded version")
            valid = valid_input or (target.valid if target is not None else None)
            if valid is None:
                raise TemporalVersionError("a validity interval or superseded version is required")

            if isinstance(write, NodeVersionWrite):
                entity_kind = "node"
                entity = write.node
                if write.operation is not VersionOperation.RETRACT and not isinstance(entity, Node):
                    raise TemporalVersionError("node assertions and corrections require a Node payload")
                if entity is not None and entity.get_id != logical_id:
                    raise TemporalVersionError("node payload ID does not match logical ID")
                payload = b"" if entity is None else self.entity_serializer.serialize(entity, "Node")
                entity_type = "Node"
            elif isinstance(write, EdgeVersionWrite):
                entity_kind = "edge"
                entity = write.edge
                if write.operation is not VersionOperation.RETRACT and not isinstance(entity, Edge):
                    raise TemporalVersionError("edge assertions and corrections require an Edge payload")
                if entity is not None and entity.get_id != logical_id:
                    raise TemporalVersionError("edge payload ID does not match logical ID")
                payload = b"" if entity is None else self.entity_serializer.serialize(entity, "Edge")
                entity_type = "Edge"
            else:
                entity_kind = "claim"
                entity = write.claim
                if write.operation is not VersionOperation.RETRACT and not isinstance(entity, Claim):
                    raise TemporalVersionError("claim assertions and corrections require a Claim payload")
                if entity is not None and entity.claim_id != logical_id:
                    raise TemporalVersionError("claim payload ID does not match logical ID")
                payload = b"" if entity is None else canonical_json_bytes(entity.to_dict())
                entity_type = "Claim"
            if write.operation is VersionOperation.RETRACT and entity is not None:
                raise TemporalVersionError("retractions cannot contain entity payloads")
            if not isinstance(payload, bytes):
                raise TemporalVersionError("the configured serializer must return bytes")
            if write.operation is not VersionOperation.RETRACT:
                if not payload:
                    raise TemporalVersionError("temporal entity payload cannot be empty")
                if entity_type == "Claim":
                    try:
                        decoded_entity = Claim.from_dict(json.loads(payload.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError, TemporalVersionError) as exc:
                        raise TemporalVersionError("cannot decode temporal claim payload") from exc
                    if decoded_entity.claim_id != logical_id:
                        raise TemporalVersionError("serialized temporal payload changed its logical ID")
                else:
                    try:
                        decoded_entity = self.entity_serializer.deserialize(payload, entity_type)
                    except Exception as exc:
                        raise TemporalVersionError(
                            "the configured serializer cannot decode its temporal payload"
                        ) from exc
                    expected_type = Node if entity_type == "Node" else Edge
                    if not isinstance(decoded_entity, expected_type) or decoded_entity.get_id != logical_id:
                        raise TemporalVersionError("serialized temporal payload changed its logical ID")

            header = {
                "entity_kind": entity_kind,
                "version_id": version_id,
                "logical_id": logical_id,
                "valid_from_us": valid.start.epoch_microseconds,
                "valid_to_us": None if valid.end is None else valid.end.epoch_microseconds,
                "commit_id": commit_id,
                "commit_ordinal": ordinal,
                "system_time_us": system_time.epoch_microseconds,
                "operation": write.operation.value,
                "supersedes_version_id": supersedes_version_id,
                "reason": write.reason,
            }
            envelope = encode_version_envelope(header, payload)
            prepared.append((version_id, envelope))

        sequence = canonical_json_bytes({
            "commit_id": commit_id,
            "system_time_us": system_time.epoch_microseconds,
        })
        record_digests = [hashlib.sha256(envelope).hexdigest() for _, envelope in prepared]
        descriptor = commit_descriptor_bytes(
            commit_id,
            system_time,
            metadata,
            [version_id for version_id, _ in prepared],
            record_digests,
        )

        if self.store.get_metadata(self._temporal_commit_key(commit_id)) is not None:
            raise TemporalCorruptionError("temporal sequence would overwrite an existing commit")
        if self.store.get_metadata(self._temporal_visible_key(commit_id)) is not None:
            raise TemporalCorruptionError("temporal sequence would overwrite an existing marker")
        for ordinal in range(len(prepared)):
            if self.store.get_metadata(self._temporal_record_key(commit_id, ordinal)) is not None:
                raise TemporalCorruptionError("temporal sequence would overwrite an existing record")

        # The sequence reserves the ID. The visibility marker is always written last.
        self.store.put_metadata(_TEMPORAL_SEQUENCE_KEY, sequence)
        self.store.put_metadata(self._temporal_commit_key(commit_id), descriptor)
        for ordinal, (_, envelope) in enumerate(prepared):
            self.store.put_metadata(self._temporal_record_key(commit_id, ordinal), envelope)
        if index_mode == IndexMaintenanceMode.DEFER.value:
            self._mark_indexes_stale("temporal")
        else:
            if previous_commit_id > 0 and self._temporal_index_state() is None:
                self._mark_indexes_stale("temporal")
            indexed_versions = [self._decode_temporal_version(envelope) for _, envelope in prepared]
            self._write_temporal_indexes(indexed_versions, commit_id)
        self.store.put_metadata(
            self._temporal_visible_key(commit_id), hashlib.sha256(descriptor).digest()
        )
        commit = self.get_temporal_commit(commit_id)
        if commit is None:
            raise TemporalCorruptionError("new temporal commit was not visible after publication")
        return commit

    def _temporal_version_id_exists(self, version_id: str) -> bool:
        last_commit_id, _ = self._temporal_sequence()
        for commit_id in range(1, last_commit_id + 1):
            descriptor_payload = self.store.get_metadata(self._temporal_commit_key(commit_id))
            if descriptor_payload is None:
                continue
            try:
                descriptor = json.loads(descriptor_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(descriptor, dict):
                raise TemporalCorruptionError("invalid temporal commit descriptor")
            if version_id in descriptor.get("version_ids", []):
                return True
        return False

    def get_temporal_commit(
        self, commit_id: int, *, _validate_supersession: bool = True
    ) -> TemporalCommit | None:
        """Return a fully validated visible temporal commit."""
        if isinstance(commit_id, bool) or not isinstance(commit_id, int) or commit_id < 1:
            raise ValueError("commit_id must be a positive integer")
        descriptor_payload = self.store.get_metadata(self._temporal_commit_key(commit_id))
        marker = self.store.get_metadata(self._temporal_visible_key(commit_id))
        if marker is None:
            return None
        if descriptor_payload is None or marker != hashlib.sha256(descriptor_payload).digest():
            raise TemporalCorruptionError("temporal commit marker does not match its descriptor")
        try:
            descriptor = json.loads(descriptor_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TemporalCorruptionError("invalid temporal commit descriptor") from exc
        if not isinstance(descriptor, dict):
            raise TemporalCorruptionError("temporal commit descriptor must be an object")
        if descriptor.get("format_version") != 1 or descriptor.get("commit_id") != commit_id:
            raise TemporalCorruptionError("invalid temporal commit descriptor")
        version_ids = descriptor.get("version_ids")
        record_digests = descriptor.get("record_digests")
        if not isinstance(version_ids, list) or not isinstance(record_digests, list) or len(version_ids) != len(record_digests):
            raise TemporalCorruptionError("invalid temporal commit record catalog")
        if not version_ids:
            raise TemporalCorruptionError("temporal commit contains no versions")
        if not all(isinstance(value, str) for value in version_ids):
            raise TemporalCorruptionError("temporal commit contains an invalid version ID")
        if len(set(version_ids)) != len(version_ids):
            raise TemporalCorruptionError("temporal commit contains duplicate version IDs")
        try:
            normalized_version_ids = [normalize_version_id(value) for value in version_ids]
        except (TypeError, ValueError) as exc:
            raise TemporalCorruptionError("temporal commit contains an invalid version ID") from exc
        if normalized_version_ids != version_ids:
            raise TemporalCorruptionError("temporal commit contains a non-canonical version ID")
        if not all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in record_digests
        ):
            raise TemporalCorruptionError("temporal commit contains an invalid record digest")
        try:
            system_time_us = descriptor["system_time_us"]
            descriptor_metadata = descriptor.get("metadata", {})
            if isinstance(system_time_us, bool) or not isinstance(system_time_us, int):
                raise TypeError
            if not isinstance(descriptor_metadata, dict):
                raise TypeError
            system_time = TemporalInstant(system_time_us)
            metadata = immutable_metadata(descriptor_metadata)
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporalCorruptionError("invalid temporal commit metadata") from exc
        versions = []
        for ordinal, (version_id, expected_digest) in enumerate(zip(version_ids, record_digests)):
            envelope = self.store.get_metadata(self._temporal_record_key(commit_id, ordinal))
            if envelope is None or hashlib.sha256(envelope).hexdigest() != expected_digest:
                raise TemporalCorruptionError("temporal commit record is missing or corrupt")
            version = self._decode_temporal_version(envelope)
            if version.version_id != version_id or version.commit_id != commit_id or version.commit_ordinal != ordinal:
                raise TemporalCorruptionError("temporal commit record does not match its descriptor")
            if version.system_time != system_time:
                raise TemporalCorruptionError("temporal version system time does not match its commit")
            versions.append(version)
        if _validate_supersession:
            for version in versions:
                if version.supersedes_version_id is None:
                    continue
                target = self._get_temporal_version_through(
                    version.supersedes_version_id, commit_id - 1
                )
                if target is None:
                    raise TemporalCorruptionError("temporal version supersedes a missing version")
                if type(target) is not type(version) or target.logical_id != version.logical_id:
                    raise TemporalCorruptionError("temporal supersession kind or logical ID mismatch")
        return TemporalCommit(commit_id, system_time, metadata, tuple(versions))

    def _decode_temporal_version(self, envelope: bytes):
        header, payload = decode_version_envelope(envelope)
        required = {
            "entity_kind", "version_id", "logical_id", "valid_from_us", "valid_to_us",
            "commit_id", "commit_ordinal", "system_time_us", "operation",
        }
        if not required.issubset(header):
            raise TemporalCorruptionError("temporal version header is incomplete")
        try:
            integer_fields = (
                header["valid_from_us"],
                header["commit_id"],
                header["commit_ordinal"],
                header["system_time_us"],
            )
            if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
                raise TypeError
            if header["valid_to_us"] is not None and (
                isinstance(header["valid_to_us"], bool) or not isinstance(header["valid_to_us"], int)
            ):
                raise TypeError
            valid = TemporalInterval.from_values(header["valid_from_us"], header["valid_to_us"])
            common = {
                "version_id": normalize_version_id(header["version_id"]),
                "logical_id": normalize_logical_id(header["logical_id"]),
                "valid": valid,
                "commit_id": header["commit_id"],
                "commit_ordinal": header["commit_ordinal"],
                "system_time": TemporalInstant(header["system_time_us"]),
                "operation": VersionOperation(header["operation"]),
                "payload_hash": header["payload_hash"],
                "supersedes_version_id": (
                    None
                    if header.get("supersedes_version_id") is None
                    else normalize_version_id(header["supersedes_version_id"])
                ),
                "reason": header.get("reason"),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise TemporalCorruptionError("invalid temporal version header values") from exc
        if not isinstance(common["payload_hash"], str):
            raise TemporalCorruptionError("temporal version payload hash is invalid")
        operation = common["operation"]
        if operation is VersionOperation.ASSERT and common["supersedes_version_id"] is not None:
            raise TemporalCorruptionError("temporal assertion unexpectedly supersedes another version")
        if operation is VersionOperation.CORRECT and common["supersedes_version_id"] is None:
            raise TemporalCorruptionError("temporal correction has no superseded version")
        if operation is not VersionOperation.RETRACT and common["reason"] is not None:
            raise TemporalCorruptionError("non-retraction temporal version has a reason")
        if common["reason"] is not None and (
            not isinstance(common["reason"], str) or not common["reason"]
        ):
            raise TemporalCorruptionError("temporal retraction reason is invalid")
        if operation is VersionOperation.RETRACT:
            if payload:
                raise TemporalCorruptionError("temporal retraction unexpectedly contains a payload")
            entity = None
        elif not payload:
            raise TemporalCorruptionError("temporal assertion or correction has no payload")
        elif header["entity_kind"] == "node":
            try:
                entity = self.entity_serializer.deserialize(payload, "Node")
            except Exception as exc:
                raise TemporalCorruptionError("cannot decode temporal node payload") from exc
        elif header["entity_kind"] == "edge":
            try:
                entity = self.entity_serializer.deserialize(payload, "Edge")
            except Exception as exc:
                raise TemporalCorruptionError("cannot decode temporal edge payload") from exc
        elif header["entity_kind"] == "claim":
            try:
                entity = Claim.from_dict(json.loads(payload.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, TemporalVersionError) as exc:
                raise TemporalCorruptionError("cannot decode temporal claim payload") from exc
        else:
            raise TemporalCorruptionError("unknown temporal entity kind")
        if entity is not None:
            entity_id = entity.claim_id if isinstance(entity, Claim) else entity.get_id
            if entity_id != common["logical_id"]:
                raise TemporalCorruptionError("temporal payload ID does not match its logical ID")
        if header["entity_kind"] == "node":
            return NodeVersion(**common, node=entity)
        if header["entity_kind"] == "edge":
            return EdgeVersion(**common, edge=entity)
        if header["entity_kind"] == "claim":
            return ClaimVersion(**common, claim=entity)
        raise TemporalCorruptionError("unknown temporal entity kind")

    def iter_temporal_commits(self, *, through_commit=None):
        """Iterate visible commits in commit order, skipping incomplete commits."""
        last_commit_id, _ = self._temporal_sequence()
        limit = last_commit_id if through_commit is None else min(last_commit_id, through_commit)
        for commit_id in range(1, limit + 1):
            commit = self.get_temporal_commit(commit_id)
            if commit is not None:
                yield commit

    def _get_temporal_version(self, version_id: str):
        last_commit_id, _ = self._temporal_sequence()
        return self._get_temporal_version_through(version_id, last_commit_id)

    def _get_temporal_version_at(self, locator: bytes):
        commit_id, ordinal = self._decode_temporal_locator(locator)
        commit = self.get_temporal_commit(commit_id, _validate_supersession=False)
        if commit is None:
            return None
        if ordinal >= len(commit.versions):
            raise TemporalCorruptionError("temporal index locator ordinal is out of range")
        return commit.versions[ordinal]

    def _get_temporal_version_through(self, version_id: str, through_commit: int):
        version_id = normalize_version_id(version_id)
        for commit_id in range(1, through_commit + 1):
            commit = self.get_temporal_commit(commit_id, _validate_supersession=False)
            if commit is None:
                continue
            for version in commit.versions:
                if version.version_id == version_id:
                    return version
        return None

    def get_node_version(
        self, version_id: str, *, system_time=None, through_commit=None
    ) -> NodeVersion | None:
        """Return a visible node version by UUID using the temporal index."""
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        locators = list(self.store.iter_index_prefix(
            _TEMPORAL_VERSION_INDEX, [normalize_version_id(version_id).encode("ascii")]
        ))
        version = self._get_temporal_version_at(locators[-1]) if locators else None
        if version is not None and version.commit_id > horizon:
            return None
        return version if isinstance(version, NodeVersion) else None

    def get_edge_version(
        self, version_id: str, *, system_time=None, through_commit=None
    ) -> EdgeVersion | None:
        """Return a visible edge version by UUID using the temporal index."""
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        locators = list(self.store.iter_index_prefix(
            _TEMPORAL_VERSION_INDEX, [normalize_version_id(version_id).encode("ascii")]
        ))
        version = self._get_temporal_version_at(locators[-1]) if locators else None
        if version is not None and version.commit_id > horizon:
            return None
        return version if isinstance(version, EdgeVersion) else None

    def get_claim_version(
        self, version_id: str, *, system_time=None, through_commit=None
    ) -> ClaimVersion | None:
        """Return a visible epistemic claim version by UUID."""
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        locators = list(self.store.iter_index_prefix(
            _TEMPORAL_VERSION_INDEX, [normalize_version_id(version_id).encode("ascii")]
        ))
        version = self._get_temporal_version_at(locators[-1]) if locators else None
        if version is not None and version.commit_id > horizon:
            return None
        return version if isinstance(version, ClaimVersion) else None

    def iter_node_versions(self, logical_id=None, *, system_time=None, through_commit=None):
        """Iterate node versions, using the logical-history index when filtered."""
        if logical_id is not None:
            logical_id = normalize_logical_id(logical_id)
            horizon = self._temporal_system_horizon(system_time=system_time, through_commit=through_commit)
            self._ensure_temporal_indexes(horizon)
            end = self._temporal_locator(horizon, (1 << 32) - 1)
            for locator in self.store.iter_range_index(
                _TEMPORAL_LOGICAL_COMMIT_INDEX,
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
                _TEMPORAL_LOGICAL_COMMIT_INDEX,
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
                _TEMPORAL_LOGICAL_COMMIT_INDEX,
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
        if system_time is not None and through_commit is not None:
            raise ValueError("provide either system_time or through_commit, not both")
        last_commit_id, _ = self._temporal_sequence()
        latest_visible = last_commit_id
        while latest_visible > 0 and self.get_temporal_commit(
            latest_visible, _validate_supersession=False
        ) is None:
            latest_visible -= 1
        if through_commit is not None:
            if isinstance(through_commit, bool) or not isinstance(through_commit, int) or through_commit < 0:
                raise ValueError("through_commit must be a non-negative integer")
            return min(latest_visible, through_commit)
        if system_time is None:
            return latest_visible
        requested = normalize_temporal_instant(system_time)
        self._ensure_temporal_indexes(latest_visible)
        horizon = 0
        for encoded_commit_id in self.store.iter_range_index(
            _TEMPORAL_SYSTEM_INDEX,
            [b"commit"],
            None,
            self._temporal_instant_index_value(requested),
            True,
            True,
        ):
            try:
                commit_id = int(encoded_commit_id, 16)
            except (TypeError, ValueError) as exc:
                raise TemporalCorruptionError("invalid temporal system index value") from exc
            if commit_id <= latest_visible and self.get_temporal_commit(
                commit_id, _validate_supersession=False
            ) is not None:
                horizon = max(horizon, commit_id)
        return horizon

    def _temporal_entity_as_of(
        self, kind, logical_id, *, valid_time, system_time=None, through_commit=None
    ):
        logical_id = normalize_logical_id(logical_id)
        instant = normalize_temporal_instant(valid_time)
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        kind_bytes = {"node": b"n", "edge": b"e", "claim": b"c"}[kind]
        upper = self._temporal_instant_index_value(instant)
        locators = self.store.iter_range_index(
            _TEMPORAL_LOGICAL_VALID_INDEX,
            [kind_bytes, logical_id.encode("utf-8")],
            None,
            upper,
            True,
            True,
        )
        winner = None
        for locator in locators:
            version = self._get_temporal_version_at(locator)
            if version is not None and version.commit_id <= horizon and version.valid.contains(instant):
                if winner is None or (version.commit_id, version.commit_ordinal) > (winner.commit_id, winner.commit_ordinal):
                    winner = version
        if winner is None or winner.operation is VersionOperation.RETRACT:
            return None
        return winner

    def get_node_as_of(
        self, logical_id, *, valid_time, system_time=None, through_commit=None
    ) -> NodeVersion | None:
        """Resolve a logical node at valid time and an optional system horizon."""
        return self._temporal_entity_as_of(
            "node",
            logical_id,
            valid_time=valid_time,
            system_time=system_time,
            through_commit=through_commit,
        )

    def get_edge_as_of(
        self, logical_id, *, valid_time, system_time=None, through_commit=None
    ) -> EdgeVersion | None:
        """Resolve a logical edge at valid time and an optional system horizon."""
        return self._temporal_entity_as_of(
            "edge",
            logical_id,
            valid_time=valid_time,
            system_time=system_time,
            through_commit=through_commit,
        )

    def get_claim_as_of(
        self, claim_id, *, valid_time, system_time=None, through_commit=None
    ) -> ClaimVersion | None:
        """Resolve one claim identity at valid time and an optional system horizon."""
        return self._temporal_entity_as_of(
            "claim",
            claim_id,
            valid_time=valid_time,
            system_time=system_time,
            through_commit=through_commit,
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
        """Iterate visible claims matching indexed epistemic dimensions."""
        instant = normalize_temporal_instant(valid_time)
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        if statement_id is not None:
            digest = statement_id.removeprefix("statement:") if isinstance(statement_id, str) else ""
            if (
                not isinstance(statement_id, str)
                or not statement_id.startswith("statement:")
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("statement_id must be a deterministic statement ID")
        effective_object_kind = (
            ClaimObjectKind.ENTITY if object_kind is None and object is not _UNSET else object_kind
        )
        if object is not _UNSET and statement_id is None and subject is not None and predicate is not None:
            statement_id = claim_statement_id(
                subject, predicate, object, object_kind=effective_object_kind
            )
        dimension_filters = {
            "subject": subject,
            "predicate": predicate,
            "agent": agent,
            "world": world,
            "polarity": None if polarity is None else ClaimPolarity(polarity).value,
            "object_kind": (
                None
                if effective_object_kind is None
                else ClaimObjectKind(effective_object_kind).value
            ),
        }
        if source is not _UNSET:
            dimension_filters["source"] = source
        indexed_filter = next(
            ((name, value) for name, value in dimension_filters.items() if value is not None),
            None,
        )
        if statement_id is not None:
            index_name = _TEMPORAL_CLAIM_STATEMENT_INDEX
            parts = [statement_id.encode("ascii")]
        elif indexed_filter is not None:
            name, value = indexed_filter
            index_name = _TEMPORAL_CLAIM_DIMENSION_INDEX
            parts = [name.encode("ascii"), canonical_json_bytes(value)]
        else:
            index_name = _TEMPORAL_CLAIM_CATALOG_INDEX
            parts = [b"claims"]
        logical_ids = set()
        for locator in self.store.iter_range_index(
            index_name,
            parts,
            None,
            self._temporal_instant_index_value(instant),
            True,
            True,
        ):
            candidate = self._get_temporal_version_at(locator)
            if isinstance(candidate, ClaimVersion) and candidate.commit_id <= horizon:
                logical_ids.add(candidate.logical_id)
        for claim_id in sorted(logical_ids):
            version = self.get_claim_as_of(
                claim_id, valid_time=instant, through_commit=horizon
            )
            if version is None or version.claim is None:
                continue
            claim = version.claim
            if statement_id is not None and claim.statement_id != statement_id:
                continue
            if any(
                value is not None and getattr(claim, name) != value
                for name, value in dimension_filters.items()
            ):
                continue
            if source is not _UNSET and claim.source != source:
                continue
            if object is not _UNSET and claim.object != Claim(
                subject=claim.subject,
                predicate=claim.predicate,
                object=object,
                object_kind=effective_object_kind,
                polarity=claim.polarity,
                agent=claim.agent,
                source=claim.source,
                world=claim.world,
            ).object:
                continue
            yield version

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
        polarities = {
            version.claim.polarity
            for version in self.iter_claims_as_of(
                valid_time=valid_time,
                statement_id=claim_statement_id(
                    subject, predicate, object, object_kind=object_kind
                ),
                agent=agent,
                source=source,
                world=world,
                system_time=system_time,
                through_commit=through_commit,
            )
        }
        if polarities == {ClaimPolarity.POSITIVE, ClaimPolarity.NEGATIVE}:
            return ClaimStatus.BOTH
        if ClaimPolarity.POSITIVE in polarities:
            return ClaimStatus.SUPPORTED
        if ClaimPolarity.NEGATIVE in polarities:
            return ClaimStatus.REFUTED
        return ClaimStatus.UNKNOWN

    def iter_nodes_as_of(
        self, *, valid_time, system_time=None, through_commit=None
    ):
        """Iterate node versions visible at one valid and system-time point."""
        instant = normalize_temporal_instant(valid_time)
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        logical_ids = set()
        for locator in self.store.iter_range_index(
            _TEMPORAL_NODE_CATALOG_INDEX,
            [b"nodes"],
            None,
            self._temporal_instant_index_value(instant),
            True,
            True,
        ):
            candidate = self._get_temporal_version_at(locator)
            if isinstance(candidate, NodeVersion) and candidate.commit_id <= horizon:
                logical_ids.add(candidate.logical_id)
        for logical_id in sorted(logical_ids):
            version = self.get_node_as_of(
                logical_id, valid_time=instant, through_commit=horizon
            )
            if version is not None:
                yield version

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
        if direction not in {"out", "in"}:
            raise ValueError("direction must be 'out' or 'in'")
        if direction == "out" and (source is None or target is not None):
            raise ValueError("outgoing traversal requires source and rejects target")
        if direction == "in" and (target is None or source is not None):
            raise ValueError("incoming traversal requires target and rejects source")
        if edge_type is not None and (not isinstance(edge_type, str) or not edge_type):
            raise ValueError("edge_type must be a non-empty string or None")
        horizon = self._temporal_system_horizon(
            system_time=system_time, through_commit=through_commit
        )
        self._ensure_temporal_indexes(horizon)
        node_id = source if direction == "out" else target
        if edge_type is None:
            index_name = (
                _TEMPORAL_EDGE_OUT_CATALOG_INDEX
                if direction == "out"
                else _TEMPORAL_EDGE_IN_CATALOG_INDEX
            )
            parts = [str(node_id).encode("utf-8")]
        else:
            index_name = _TEMPORAL_EDGE_OUT_INDEX if direction == "out" else _TEMPORAL_EDGE_IN_INDEX
            parts = [str(node_id).encode("utf-8"), edge_type.encode("utf-8")]
        upper = self._temporal_instant_index_value(normalize_temporal_instant(valid_time))
        locators = self.store.iter_range_index(
            index_name,
            parts,
            None,
            upper,
            True,
            True,
        )
        logical_ids = set()
        for locator in locators:
            candidate = self._get_temporal_version_at(locator)
            if candidate is not None and candidate.commit_id <= horizon:
                logical_ids.add(candidate.logical_id)
        for logical_id in sorted(logical_ids):
            version = self.get_edge_as_of(
                logical_id, valid_time=valid_time, through_commit=horizon
            )
            if version is None or (
                edge_type is not None and version.edge.properties.get("type") != edge_type
            ):
                continue
            if direction == "out" and str(version.edge.source) != str(source):
                continue
            if direction == "in" and str(version.edge.target) != str(target):
                continue
            yield version

    def rebuild_temporal_indexes(self, *, through_commit=None) -> dict[str, int]:
        """Rebuild all derived temporal indexes from visible canonical history."""
        with self._temporal_write_lock:
            self._mark_indexes_stale("temporal")
            latest_horizon = self._temporal_system_horizon()
            horizon = self._temporal_system_horizon(through_commit=through_commit)
            counts = {"temporal_exact": 0, "temporal_range": 0}
            last_visible = 0
            for commit in self.iter_temporal_commits(through_commit=horizon):
                written = self._write_temporal_indexes(commit.versions, commit.commit_id)
                counts["temporal_exact"] += written["temporal_exact"]
                counts["temporal_range"] += written["temporal_range"]
                last_visible = commit.commit_id
            if last_visible == 0:
                self._persist_temporal_index_state(0)
            if horizon >= latest_horizon:
                self._clear_stale_indexes("temporal")
            return counts

    def query(self, cypher: str, parameters: Optional[dict[str, object]] = None):
        """Execute a supported Cypher query.

        Read queries run directly; queries with ``CREATE``/``SET``/``REMOVE``
        run inside :meth:`transaction` when the backend supports it and fall
        back to best-effort direct writes otherwise.

        Args:
            cypher: Query text in the supported GestaltDB Cypher subset.
            parameters: Optional Cypher parameter values keyed without the
                leading ``$``.

        Returns:
            ``gestaltdb.QueryResult`` containing projected records.

        Examples:
            >>> graph_db.query('MATCH (n:Drug) RETURN n')  # doctest: +SKIP
            >>> graph_db.query('MATCH (a {id: "drug-1"})-[:drug-to-protein]->(b) RETURN a, b')  # doctest: +SKIP
        """
        from .query_engine.cypher import execute

        return execute(self, cypher, parameters=parameters)

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

    @contextmanager
    def transaction(self, **options):
        """Run graph operations in a backend transaction when supported.

        The transaction commits on clean context exit and rolls back if an
        exception leaves the context.
        """
        tx_store = self.store.transaction(**options)
        try:
            tx_graph = GraphDB(tx_store, self.serializer)
            tx_graph.indexed_node_properties = set(self.indexed_node_properties)
            tx_graph.indexed_edge_properties = set(self.indexed_edge_properties)
            tx_graph._store_path = self._store_path
            tx_graph._backend_name = self._backend_name
            tx_graph._serializer_name = self._serializer_name
            tx_graph._manifest = self._manifest
            tx_graph._temporal_write_lock = self._temporal_write_lock
            tx_graph._temporal_transaction_bound = True
            tx_graph._temporal_clock = self._temporal_clock
            yield tx_graph
        except Exception:
            tx_store.rollback()
            raise
        else:
            tx_store.commit()

    def close(self):
        """Close the underlying key-value store.

        Examples:
            >>> graph_db.close()  # doctest: +SKIP
        """
        self.store.close()
        
