from typing import Any, ContextManager, Dict, Iterable, Iterator, List, NoReturn, Optional, Union
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
import struct
from .ingestion import EdgeList, NodeList

_TYPED_ADJ_SEP = b"\x1f"
MAX_PORTABLE_INDEX_KEY_BYTES = 511


@dataclass(frozen=True)
class ReadSnapshotIdentity:
    """Backend identity for one pinned mutable-state read horizon."""

    backend: str
    kind: str
    sequence: int | None
    verifiable: bool


def _checked_index_key(key: bytes) -> bytes:
    if len(key) > MAX_PORTABLE_INDEX_KEY_BYTES:
        raise ValueError(
            f"encoded index key exceeds the portable {MAX_PORTABLE_INDEX_KEY_BYTES}-byte limit"
        )
    return key


def _to_bytes(value: object) -> bytes:
    """Normalize an index component to bytes.

    Args:
        value: Bytes or string-like value.

    Returns:
        Bytes suitable for key encoding.

    Examples:
        >>> _to_bytes("Drug")
        b'Drug'
    """
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _index_part(value: object) -> bytes:
    """Encode one index key component safely.

    Args:
        value: Raw component value.

    Returns:
        URL-safe base64 bytes without padding.

    Examples:
        >>> _index_part("Drug")
        b'RHJ1Zw'
    """
    return base64.urlsafe_b64encode(_to_bytes(value)).rstrip(b"=")


def _index_key(index_name: str, key_parts: Iterable[bytes], value: bytes = b"") -> bytes:
    """Build a sorted index key.

    Args:
        index_name: Logical index name.
        key_parts: Ordered prefix components.
        value: Stored entity ID or payload.

    Returns:
        Encoded key in the shared index namespace.

    Examples:
        >>> _index_key("node_label", [b"Drug"], b"drug-1").startswith(b"I")
        True
    """
    parts = [_index_part(index_name)]
    parts.extend(_index_part(part) for part in key_parts)
    parts.append(_index_part(value))
    return _checked_index_key(b"I" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts))


def _index_prefix(index_name: str, key_parts: Iterable[bytes]) -> bytes:
    """Build the prefix used for sorted index scans.

    Args:
        index_name: Logical index name.
        key_parts: Ordered prefix components.

    Returns:
        Encoded prefix ending with the index separator.

    Examples:
        >>> _index_prefix("node_label", [b"Drug"]).startswith(b"I")
        True
    """
    parts = [_index_part(index_name)]
    parts.extend(_index_part(part) for part in key_parts)
    return _checked_index_key(
        b"I" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts) + _TYPED_ADJ_SEP
    )


def _range_index_key(index_name: str, key_parts: Iterable[bytes], range_value: bytes, value: bytes = b"") -> bytes:
    """Build a sorted range index key."""
    parts = [_index_part(index_name)]
    parts.extend(_index_part(part) for part in key_parts)
    parts.append(range_value)
    parts.append(_index_part(value))
    return _checked_index_key(b"R" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts))


def _range_index_prefix(index_name: str, key_parts: Iterable[bytes]) -> bytes:
    """Build the prefix used for sorted range index scans."""
    parts = [_index_part(index_name)]
    parts.extend(_index_part(part) for part in key_parts)
    return _checked_index_key(
        b"R" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts) + _TYPED_ADJ_SEP
    )


def _validate_range_scan_options(reverse: bool, limit: int | None) -> None:
    if not isinstance(reverse, bool):
        raise TypeError("reverse must be a boolean")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
        raise ValueError("limit must be a positive integer")


def _missing_dependency_error(package_name: str, *, extra_name: str, feature_name: str | None = None) -> ImportError:
    """Build a consistent optional dependency error.

    Examples:
        >>> "lmdb" in str(_missing_dependency_error("lmdb", extra_name="lmdb"))
        True
    """
    feature_name = feature_name or package_name
    return ImportError(
        f"Missing optional dependency '{package_name}' required for {feature_name}. "
        f"Install it with `python -m pip install 'gestaltdb[{extra_name}]'` "
        f"or `uv add 'gestaltdb[{extra_name}]'`."
    )


def _pack_long_int(int_val: int) -> bytes:
    """Pack an integer as little-endian unsigned long bytes.

    Examples:
        >>> _unpack_long_int(_pack_long_int(3))
        3
    """
    return struct.pack('<L', int_val)

def _unpack_long_int(int_val: bytes) -> int:
    """Unpack little-endian unsigned long bytes.

    Examples:
        >>> _unpack_long_int(_pack_long_int(3))
        3
    """
    return struct.unpack('<L', int_val)[0]


def _typed_adjacency_key(direction: str, node_id: bytes, edge_type: str, edge_id: bytes = b"") -> bytes:
    """Build a typed adjacency key.

    Examples:
        >>> _typed_adjacency_key("out", b"drug-1", "drug-to-protein", b"e1")
        b'out\\x1fdrug-1\\x1fdrug-to-protein\\x1fe1'
    """
    edge_type_bytes = edge_type.encode("utf-8")
    return _TYPED_ADJ_SEP.join([direction.encode("utf-8"), node_id, edge_type_bytes, edge_id])


def _typed_adjacency_prefix(direction: str, node_id: bytes, edge_type: str) -> bytes:
    """Build the key prefix for a typed adjacency scan.

    Examples:
        >>> _typed_adjacency_prefix("out", b"drug-1", "drug-to-protein").startswith(b"out")
        True
    """
    return _typed_adjacency_key(direction, node_id, edge_type)


class KVStore:
    """Abstract interface for a simple key-value store."""

    supports_transactions = False
    supports_read_snapshots = False
    supports_verifiable_read_snapshots = False

    # The basic K/V methods:
    def put(self, key: bytes, value: bytes) -> None:
        """Store a raw key/value pair."""
        raise NotImplementedError

    def get(self, key: bytes) -> bytes | None:
        """Return a raw value by key."""
        raise NotImplementedError

    def delete(self, key: bytes) -> None:
        """Delete a raw key/value pair."""
        raise NotImplementedError

    def range_iter(self, start_key: bytes, end_key: bytes) -> Iterable[tuple[bytes, bytes]]:
        """Iterate over keys from start_key to end_key (inclusive)."""
        raise NotImplementedError

    def close(self) -> None:
        """Close any resources owned by the store."""
        raise NotImplementedError

    def transaction(self, **options: Any) -> "KVTransaction | LMDBTransactionStore | PyRexTransactionStore":
        """Return a transaction-bound store when supported."""
        raise NotImplementedError("transactions are not supported by this KVStore")

    def read_snapshot(self) -> ContextManager["LMDBReadSnapshotStore | PyRexReadSnapshotStore"]:
        """Return a context manager yielding a unified read-only store view."""
        raise NotImplementedError("unified read snapshots are not supported by this KVStore")

    def put_metadata(self, key: bytes, value: bytes) -> None:
        """Store a metadata key/value pair."""
        raise NotImplementedError

    def get_metadata(self, key: bytes) -> bytes | None:
        """Return a metadata value by key."""
        raise NotImplementedError

    def delete_metadata(self, key: bytes) -> None:
        """Delete a metadata key/value pair."""
        raise NotImplementedError

    # The specialized node/edge methods:
    def put_node(self, node_id: bytes, value: bytes) -> None:
        """Store a node (serialized)."""
        raise NotImplementedError

    def get_node(self, node_id: bytes) -> bytes | None:
        """Retrieve a node by ID."""
        raise NotImplementedError

    def delete_node(self, node_id: bytes) -> None:
        """Delete a node."""
        raise NotImplementedError

    def put_edge(self, edge_id: bytes, value: bytes) -> None:
        """Store an edge (serialized)."""
        raise NotImplementedError

    def get_edge(self, edge_id: bytes) -> bytes | None:
        """Retrieve an edge."""
        raise NotImplementedError

    def delete_edge(self, edge_id: bytes) -> None:
        """Delete an edge."""
        raise NotImplementedError

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """
        Store multiple node (serialized) values in a single batch/transaction if possible.
        """
        raise NotImplementedError

    def get_nodes_bulk(self, node_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Retrieve multiple serialized nodes by key."""
        raise NotImplementedError

    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Store multiple serialized edges."""
        raise NotImplementedError

    def get_edges_bulk(self, edge_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Retrieve multiple serialized edges by key."""
        raise NotImplementedError

    def get_node_keys_generator(self, num_nodes: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        raise NotImplementedError

    def get_edge_keys_generator(self, num_edges: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        raise NotImplementedError

    def put_adjacency(self, node_id: bytes, value: bytes) -> None:
        raise NotImplementedError

    def get_adjacency(self, node_id: bytes) -> bytes | None:
        raise NotImplementedError

    def put_adjacency_bulk(self, adjacencies: dict[bytes, bytes]) -> None:
        raise NotImplementedError

    def delete_adjacency(self, node_id: bytes) -> None:
        """Delete a serialized adjacency list for a node."""
        raise NotImplementedError

    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Store typed adjacency records for an edge."""
        raise NotImplementedError

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]) -> None:
        """Store typed adjacency records for multiple edges."""
        for source_id, target_id, edge_type, edge_id in records:
            self.put_typed_adjacency(source_id, target_id, edge_type, edge_id)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Delete typed adjacency records for an edge."""
        raise NotImplementedError

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> Iterator[tuple[bytes, bytes]]:
        """Yield typed adjacency records for a node and edge type."""
        raise NotImplementedError

    def count_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> int:
        """Count typed adjacency records for a node and edge type."""
        return sum(1 for _ in self.iter_typed_adjacency(node_id, edge_type, direction))

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Store one sorted index entry."""
        raise NotImplementedError

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]) -> None:
        """Store many sorted index entries."""
        for index_name, key_parts, value in entries:
            self.put_index_entry(index_name, key_parts, value)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Delete one sorted index entry."""
        raise NotImplementedError

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]) -> Iterator[bytes]:
        """Yield values whose index key starts with ``key_parts``."""
        raise NotImplementedError

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Store one sorted range index entry."""
        raise NotImplementedError

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]) -> None:
        """Store many sorted range index entries."""
        for index_name, key_parts, range_value, value in entries:
            self.put_range_index_entry(index_name, key_parts, range_value, value)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Delete one sorted range index entry."""
        raise NotImplementedError

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True, *, reverse: bool = False, limit: int | None = None) -> Iterator[bytes]:
        """Yield bounded range-index values, optionally greatest-first."""
        raise NotImplementedError

    def ingest_nodes_columnar(self, node_list: NodeList, *, native: bool = True) -> None:
        """Store columnar nodes with caller-provided serialized values."""
        self.put_nodes_bulk(dict(zip(node_list.node_ids, node_list.node_values)))

    def ingest_edges_columnar(self, edge_list: EdgeList, *, append_only: bool = True, native: bool = True, maintain_indexes: bool = True) -> None:
        """Store columnar typed edges with caller-provided serialized values."""
        if not append_only:
            raise NotImplementedError("columnar edge ingestion currently requires append_only=True")
        self.put_edges_bulk(dict(zip(edge_list.edge_ids, edge_list.edge_values)))
        self.put_typed_adjacency_bulk(
            list(zip(edge_list.sources, edge_list.targets, edge_list.edge_types, edge_list.edge_ids))
        )
        if maintain_indexes:
            self.put_index_entries_bulk([
                ("edge_type", [edge_type.encode("utf-8")], edge_id)
                for edge_type, edge_id in zip(edge_list.edge_types, edge_list.edge_ids)
            ])


class KVTransaction(KVStore):
    """A transaction-bound store with explicit commit/rollback coordination."""

    def rollback(self) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError


# =========================================
# LMDB Implementation
# =========================================
class SimpleKV:
    """Small LMDB-backed helper for metadata key/value access.

    Args:
        db_path: LMDB database handle or name used by transactions.
    """

    def __init__(self, db_path):
        """Initialize the helper with an LMDB database path or handle."""
        self.db_path = db_path
        self.max_key_idx = None
        
    def get_num_keys(self):
        """Return the stored key counter."""
        _b = self.get('num_keys'.encode('utf-8'))
        num_keys = struct.unpack('<L',_b)
        return num_keys
    
    def put_num_keys(self, num_keys):
        """Store the key counter."""
        num_keys_bytes = struct.pack('<L',num_keys)
        _b = self.put('num_keys'.encode('utf-8'), num_keys_bytes)

    def put(self, key : bytes, value : bytes):
        """Write a metadata key/value pair."""
        with self.env.begin(write=True, db=self.db_path) as txn:
            txn.put(key, value)

    def get(self, key):
        """Read a metadata value by key."""
        with self.env.begin(write=False, db=self.db_path) as txn:
            return txn.get(key)
    
    def encode_db_key(self, key):
        """If the key exists, it will return the existing key.
        if the key does not exist, it will add it to the KV store with a new increment, 
        and return that.
        """
        v = self.get(key.encode('utf-8'))
        if v is None:
            num_keys = self.get_num_keys()
            num_keys += 1
            self.put_num_keys(num_keys)
            return num_keys
        
    def decode_db_key(self, key):
        """Return the encoded database key for a user key."""
        v = self.get(key.encode('utf-8'))
        return v
        

class LMDBStore(KVStore):
    """LMDB implementation of the GestaltDB key-value store.

    Examples:
        Work with raw bytes in a disposable store (requires ``lmdb``)::

            >>> from tempfile import TemporaryDirectory
            >>> from gestaltdb.kvstores import LMDBStore
            >>> with TemporaryDirectory() as root:
            ...     store = LMDBStore(path=root + "/db")
            ...     try:
            ...         store.put_node(b"n", b"payload")
            ...         assert store.get_node(b"n") == b"payload"
            ...     finally:
            ...         store.close()

        Most applications use ``GraphDB.create(..., backend="lmdb")`` to add
        serialization, indexes, and graph semantics above this byte interface.
    """

    supports_transactions = True
    supports_read_snapshots = True
    supports_verifiable_read_snapshots = True

    @staticmethod
    def ensure_available() -> Any:
        """Import and return LMDB, or raise installation guidance."""
        try:
            import lmdb
        except ImportError as exc:
            raise _missing_dependency_error("lmdb", extra_name="lmdb", feature_name="LMDBStore") from exc
        return lmdb

    def __init__(self, path: str | os.PathLike[str] = 'graph_lmdb', map_size: int = 10_485_760, map_id: bool = True, map_keys: bool = False) -> None:
        """
        Creates/opens an LMDB environment with three named sub-databases:
          - b'nodes' for node data
          - b'edges' for edge data
          - b'adj'   for adjacency lists
        """
        lmdb = self.ensure_available()

        self.path = path
        max_dbs = 6
        if map_keys:
            max_dbs += 2
        self.env = lmdb.open(path, map_size=map_size, subdir=True, max_dbs=max_dbs)
        self.nodes_db = self.env.open_db(b'nodes')
        self.edges_db = self.env.open_db(b'edges')
        self.adj_db   = self.env.open_db(b'adj')
        self.typed_adj_db = self.env.open_db(b'typed_adj')
        self.index_db = self.env.open_db(b'index')
        self.metadata_db = self.env.open_db(b'metadata')

        if map_keys:
            self.node_key_encdec_db = self.env.open_db(b'node_key_db')
            self.edge_key_encdec_db = self.env.open_db(b'edge_key_db')
            self.node_key_encdec = SimpleKV(b'node_key_db')
            self.edge_key_encdec = SimpleKV(b'edge_key_db')

    # -- Basic methods (not used by GraphDB if we rely on specialized node/edge methods below)
    def put(self, key: bytes, value: bytes) -> None:
        """Placeholder generic put; graph code uses specialized methods."""
        # For LMDB, we'd need to decide which db to put to. This might remain unused.
        pass

    def get(self, key: bytes) -> bytes | None:
        """Placeholder generic get; graph code uses specialized methods."""
        # Not used directly.
        return None

    def delete(self, key: bytes) -> None:
        """Placeholder generic delete; graph code uses specialized methods."""
        pass

    def range_iter(self, start_key: bytes, end_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        """Yield node records whose keys fall within an inclusive range."""
        # For demonstration, let us assume node range queries share a single sub-DB.
        # This might need refining.
        with self.env.begin(write=False, db=self.nodes_db) as txn:
            cursor = txn.cursor()
            if not cursor.set_range(start_key):
                return
            for k, v in cursor:
                if k > end_key:
                    break
                yield k, v

    def close(self) -> None:
        """Close the LMDB environment."""
        self.env.close()

    def transaction(self, write: bool = True, **options: Any) -> "LMDBTransactionStore":
        """Open a transaction spanning all LMDB named databases."""
        return LMDBTransactionStore(self, write=write, **options)

    def current_snapshot_sequence(self) -> int:
        """Return the latest committed LMDB transaction ID."""
        return self.env.info()["last_txnid"]

    @contextmanager
    def read_snapshot(self) -> Iterator["LMDBReadSnapshotStore"]:
        """Pin all named databases to one LMDB MVCC transaction."""
        snapshot = LMDBReadSnapshotStore(self)
        try:
            yield snapshot
        finally:
            snapshot.close()

    def put_metadata(self, key: bytes, value: bytes) -> None:
        """Store a metadata key/value pair."""
        with self.env.begin(write=True, db=self.metadata_db) as txn:
            txn.put(key, value)

    def get_metadata(self, key: bytes) -> bytes | None:
        """Return a metadata value by key, or ``None``."""
        with self.env.begin(write=False, db=self.metadata_db) as txn:
            return txn.get(key)

    def delete_metadata(self, key: bytes) -> None:
        """Delete a metadata key/value pair."""
        with self.env.begin(write=True, db=self.metadata_db) as txn:
            txn.delete(key)

    # -- Specialized node methods
    def put_node(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized node by byte key."""
        with self.env.begin(write=True, db=self.nodes_db) as txn:
            txn.put(node_id, value)

    def get_node(self, node_id: bytes) -> bytes | None:
        """Return serialized node bytes by key, or ``None``."""
        with self.env.begin(write=False, db=self.nodes_db) as txn:
            return txn.get(node_id)

    def delete_node(self, node_id: bytes) -> None:
        """Delete a node by byte key."""
        with self.env.begin(write=True, db=self.nodes_db) as txn:
            txn.delete(node_id)

    # -- Specialized edge methods
    def put_edge(self, edge_id: bytes, value: bytes) -> None:
        """Store a serialized edge by byte key."""
        with self.env.begin(write=True, db=self.edges_db) as txn:
            txn.put(edge_id, value)

    def get_edge(self, edge_id: bytes) -> bytes | None:
        """Return serialized edge bytes by key, or ``None``."""
        with self.env.begin(write=False, db=self.edges_db) as txn:
            return txn.get(edge_id)

    def delete_edge(self, edge_id: bytes) -> None:
        """Delete an edge by byte key."""
        with self.env.begin(write=True, db=self.edges_db) as txn:
            txn.delete(edge_id)

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Write a batch of nodes in a single transaction."""
        with self.env.begin(write=True, db=self.nodes_db) as txn:
            for node_id, val in keys_and_values.items():
                txn.put(node_id, val)
    
    def get_nodes_bulk(self, node_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Retrieve multiple nodes in one read transaction."""
        results = {}
        with self.env.begin(write=False, db=self.nodes_db) as txn:
            with txn.cursor() as c:
                data = c.getmulti(list(node_ids))
                if data is not None:
                    results.update({k : v for k, v in data})

        return results
    
    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Store many serialized edges in one transaction."""
        with self.env.begin(write=True, db=self.edges_db) as txn:
            for edge_id, val in keys_and_values.items():
                txn.put(edge_id, val)
    
    def get_edges_bulk(self, edge_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Return serialized edges for the requested keys."""
        results = {}
        with self.env.begin(write=False, db=self.edges_db) as txn:
            for edge_id in edge_ids:
                data = txn.get(edge_id)
                if data is not None:
                    results[edge_id] = data
        return results

    def get_edge_keys_generator(self, num_edges: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        """Yield edge keys from the edge database."""
        yielded = 0
        with self.env.begin(write=False, db=self.edges_db) as txn:
            with txn.cursor() as c:
                if key_offset is not None:
                    c.set_range(key_offset)
                for k, _ in c:
                    yield k
                    yielded += 1
                    if num_edges is not None and yielded == num_edges:
                        break
    
    def get_node_keys_generator(self, num_nodes: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        """Yield node keys from the node database."""
        yielded = 0
        with self.env.begin(write = False, db = self.nodes_db) as txn:
            with txn.cursor() as c:
                if key_offset is not None:
                    c.set_range(key_offset)
                for k, _ in c:
                    yield k
                    yielded += 1
                    if num_nodes is not None and yielded == num_nodes:
                        break

    # ----- Adjacency Methods -----
    def put_adjacency(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized adjacency list for a node."""
        with self.env.begin(write=True, db=self.adj_db) as txn:
            txn.put(node_id, value)

    def get_adjacency(self, node_id: Union[bytes, str]) -> Optional[bytes]:
        """Return a serialized adjacency list for a node."""
        with self.env.begin(write=False, db=self.adj_db) as txn:
            if isinstance(node_id, bytes):
                return txn.get(node_id)
            else:
                raise Exception('Get adjacency requires the bytes! (serialized data)')

    # -- Bulk Write: Adjacency --
    def put_adjacency_bulk(self, adj_dict: Dict[bytes, bytes]) -> None:
        """
        Insert/update multiple adjacency lists in one transaction.
        :param adj_dict: a dict mapping node_id -> serialized adjacency (list of edges)
        """
        with self.env.begin(write=True, db=self.adj_db) as txn:
            for node_id, val in adj_dict.items():
                txn.put(node_id, val)

    # -------------------------------------------------------------------------
    # Bulk Read: Adjacency
    # -------------------------------------------------------------------------
    def get_adjacency_bulk(self, node_ids: List[bytes]) -> Dict[bytes, bytes]:
        """
        Retrieve multiple adjacency lists in a single read transaction.
        Returns a dict { node_id: serialized adjacency } for all found items.
        """
        results = {}
        with self.env.begin(write=False, db=self.adj_db) as txn:
            for node_id in node_ids:
                data = txn.get(node_id)
                if data is not None:
                    results[node_id] = data
        return results

    def delete_adjacency(self, node_id: bytes) -> None:
        """Delete a serialized adjacency list for a node."""
        with self.env.begin(write=True, db=self.adj_db) as txn:
            txn.delete(node_id)

    # ----- Typed Adjacency Methods -----
    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Store forward and reverse typed adjacency records."""
        with self.env.begin(write=True, db=self.typed_adj_db) as txn:
            txn.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
            txn.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]) -> None:
        """Store many typed adjacency records in one transaction."""
        with self.env.begin(write=True, db=self.typed_adj_db) as txn:
            for source_id, target_id, edge_type, edge_id in records:
                txn.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
                txn.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Delete forward and reverse typed adjacency records."""
        with self.env.begin(write=True, db=self.typed_adj_db) as txn:
            txn.delete(_typed_adjacency_key("out", source_id, edge_type, edge_id))
            txn.delete(_typed_adjacency_key("in", target_id, edge_type, edge_id))

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> Iterator[tuple[bytes, bytes]]:
        """Yield typed adjacency ``(edge_id, neighbor_id)`` pairs."""
        prefix = _typed_adjacency_prefix(direction, node_id, edge_type)
        with self.env.begin(write=False, db=self.typed_adj_db) as txn:
            cursor = txn.cursor()
            if not cursor.set_range(prefix):
                return
            for key, neighbor_id in cursor:
                if not key.startswith(prefix):
                    break
                edge_id = key[len(prefix):]
                yield edge_id, neighbor_id

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Store one sorted index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.put(_index_key(index_name, key_parts, value), value)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]) -> None:
        """Store many sorted index entries in one transaction."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            for index_name, key_parts, value in entries:
                txn.put(_index_key(index_name, key_parts, value), value)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Delete one sorted index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.delete(_index_key(index_name, key_parts, value))

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]) -> Iterator[bytes]:
        """Yield values whose index key starts with ``key_parts``."""
        prefix = _index_prefix(index_name, key_parts)
        with self.env.begin(write=False, db=self.index_db) as txn:
            cursor = txn.cursor()
            if not cursor.set_range(prefix):
                return
            for key, value in cursor:
                if not key.startswith(prefix):
                    break
                yield value

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Store one sorted range index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]) -> None:
        """Store many sorted range index entries in one transaction."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            for index_name, key_parts, range_value, value in entries:
                txn.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Delete one sorted range index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.delete(_range_index_key(index_name, key_parts, range_value, value))

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True, *, reverse: bool = False, limit: int | None = None) -> Iterator[bytes]:
        """Yield values whose range index key falls between start and end values."""
        _validate_range_scan_options(reverse, limit)
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        with self.env.begin(write=False, db=self.index_db) as txn:
            cursor = txn.cursor()
            if reverse:
                end_key = prefix + (b"\xff" if end_value is None else end_value + b"\xff")
                if cursor.set_range(end_key):
                    if not cursor.prev():
                        return
                elif not cursor.last():
                    return
                yielded = 0
                while True:
                    key, value = cursor.item()
                    if not key.startswith(prefix):
                        break
                    range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
                    if end_value is not None and (
                        range_value > end_value or (range_value == end_value and not include_end)
                    ):
                        if not cursor.prev():
                            break
                        continue
                    if start_value is not None and (
                        range_value < start_value or (range_value == start_value and not include_start)
                    ):
                        break
                    yield value
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        break
                    if not cursor.prev():
                        break
                return
            if not cursor.set_range(start_key):
                return
            yielded = 0
            for key, value in cursor:
                if not key.startswith(prefix):
                    break
                range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
                if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                    if reverse:
                        break
                    continue
                if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                    break
                yield value
                yielded += 1
                if limit is not None and yielded >= limit:
                    break


class LMDBTransactionStore(KVStore):
    """Transaction-bound LMDB store using one environment transaction."""

    supports_transactions = True

    def __init__(self, parent: LMDBStore, write: bool = True, **options: Any) -> None:
        self.parent = parent
        self.write = write
        self.txn = parent.env.begin(write=write, **options)
        self._active = True

    def _check_active(self) -> None:
        if not self._active:
            raise RuntimeError("transaction is no longer active")

    def commit(self) -> None:
        self._check_active()
        self.txn.commit()
        self._active = False

    def rollback(self) -> None:
        if self._active:
            self.txn.abort()
            self._active = False

    def close(self) -> None:
        self.rollback()

    def transaction(self, **options: Any) -> NoReturn:
        raise NotImplementedError("nested transactions are not supported")

    def put(self, key: bytes, value: bytes) -> None:
        self._check_active()

    def get(self, key: bytes) -> bytes | None:
        self._check_active()
        return None

    def delete(self, key: bytes) -> None:
        self._check_active()

    def range_iter(self, start_key: bytes, end_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        self._check_active()
        cursor = self.txn.cursor(db=self.parent.nodes_db)
        if not cursor.set_range(start_key):
            return
        for key, value in cursor:
            if key > end_key:
                break
            yield key, value

    def put_metadata(self, key: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.put(key, value, db=self.parent.metadata_db)

    def get_metadata(self, key: bytes) -> bytes | None:
        self._check_active()
        return self.txn.get(key, db=self.parent.metadata_db)

    def delete_metadata(self, key: bytes) -> None:
        self._check_active()
        self.txn.delete(key, db=self.parent.metadata_db)

    def put_node(self, node_id: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.put(node_id, value, db=self.parent.nodes_db)

    def get_node(self, node_id: bytes) -> bytes | None:
        self._check_active()
        return self.txn.get(node_id, db=self.parent.nodes_db)

    def delete_node(self, node_id: bytes) -> None:
        self._check_active()
        self.txn.delete(node_id, db=self.parent.nodes_db)

    def put_edge(self, edge_id: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.put(edge_id, value, db=self.parent.edges_db)

    def get_edge(self, edge_id: bytes) -> bytes | None:
        self._check_active()
        return self.txn.get(edge_id, db=self.parent.edges_db)

    def delete_edge(self, edge_id: bytes) -> None:
        self._check_active()
        self.txn.delete(edge_id, db=self.parent.edges_db)

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        self._check_active()
        for node_id, value in keys_and_values.items():
            self.txn.put(node_id, value, db=self.parent.nodes_db)

    def get_nodes_bulk(self, node_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        self._check_active()
        results = {}
        for node_id in node_ids:
            value = self.txn.get(node_id, db=self.parent.nodes_db)
            if value is not None:
                results[node_id] = value
        return results

    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        self._check_active()
        for edge_id, value in keys_and_values.items():
            self.txn.put(edge_id, value, db=self.parent.edges_db)

    def get_edges_bulk(self, edge_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        self._check_active()
        results = {}
        for edge_id in edge_ids:
            value = self.txn.get(edge_id, db=self.parent.edges_db)
            if value is not None:
                results[edge_id] = value
        return results

    def get_edge_keys_generator(self, num_edges: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        yield from self._key_generator(self.parent.edges_db, num_edges, key_offset)

    def get_node_keys_generator(self, num_nodes: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        yield from self._key_generator(self.parent.nodes_db, num_nodes, key_offset)

    def _key_generator(self, db: Any, limit: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        self._check_active()
        yielded = 0
        cursor = self.txn.cursor(db=db)
        if key_offset is not None:
            if not cursor.set_range(key_offset):
                return
        else:
            if not cursor.first():
                return
        for key, _ in cursor:
            yield key
            yielded += 1
            if limit is not None and yielded == limit:
                break

    def put_adjacency(self, node_id: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.put(node_id, value, db=self.parent.adj_db)

    def get_adjacency(self, node_id: bytes) -> Optional[bytes]:
        self._check_active()
        if not isinstance(node_id, bytes):
            raise Exception('Get adjacency requires the bytes! (serialized data)')
        return self.txn.get(node_id, db=self.parent.adj_db)

    def put_adjacency_bulk(self, adj_dict: Dict[bytes, bytes]) -> None:
        self._check_active()
        for node_id, value in adj_dict.items():
            self.txn.put(node_id, value, db=self.parent.adj_db)

    def get_adjacency_bulk(self, node_ids: List[bytes]) -> Dict[bytes, bytes]:
        self._check_active()
        results = {}
        for node_id in node_ids:
            value = self.txn.get(node_id, db=self.parent.adj_db)
            if value is not None:
                results[node_id] = value
        return results

    def delete_adjacency(self, node_id: bytes) -> None:
        self._check_active()
        self.txn.delete(node_id, db=self.parent.adj_db)

    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        self.put_typed_adjacency_bulk([(source_id, target_id, edge_type, edge_id)])

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]) -> None:
        self._check_active()
        for source_id, target_id, edge_type, edge_id in records:
            self.txn.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id, db=self.parent.typed_adj_db)
            self.txn.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id, db=self.parent.typed_adj_db)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        self._check_active()
        self.txn.delete(_typed_adjacency_key("out", source_id, edge_type, edge_id), db=self.parent.typed_adj_db)
        self.txn.delete(_typed_adjacency_key("in", target_id, edge_type, edge_id), db=self.parent.typed_adj_db)

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> Iterator[tuple[bytes, bytes]]:
        self._check_active()
        prefix = _typed_adjacency_prefix(direction, node_id, edge_type)
        cursor = self.txn.cursor(db=self.parent.typed_adj_db)
        if not cursor.set_range(prefix):
            return
        for key, neighbor_id in cursor:
            if not key.startswith(prefix):
                break
            yield key[len(prefix):], neighbor_id

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        self._check_active()
        self.txn.put(_index_key(index_name, key_parts, value), value, db=self.parent.index_db)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]) -> None:
        self._check_active()
        for index_name, key_parts, value in entries:
            self.txn.put(_index_key(index_name, key_parts, value), value, db=self.parent.index_db)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        self._check_active()
        self.txn.delete(_index_key(index_name, key_parts, value), db=self.parent.index_db)

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]) -> Iterator[bytes]:
        self._check_active()
        prefix = _index_prefix(index_name, key_parts)
        cursor = self.txn.cursor(db=self.parent.index_db)
        if not cursor.set_range(prefix):
            return
        for key, value in cursor:
            if not key.startswith(prefix):
                break
            yield value

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.put(_range_index_key(index_name, key_parts, range_value, value), value, db=self.parent.index_db)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]) -> None:
        self._check_active()
        for index_name, key_parts, range_value, value in entries:
            self.txn.put(_range_index_key(index_name, key_parts, range_value, value), value, db=self.parent.index_db)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.delete(_range_index_key(index_name, key_parts, range_value, value), db=self.parent.index_db)

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True, *, reverse: bool = False, limit: int | None = None) -> Iterator[bytes]:
        self._check_active()
        _validate_range_scan_options(reverse, limit)
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        cursor = self.txn.cursor(db=self.parent.index_db)
        if reverse:
            end_key = prefix + (b"\xff" if end_value is None else end_value + b"\xff")
            if cursor.set_range(end_key):
                if not cursor.prev():
                    return
            elif not cursor.last():
                return
            yielded = 0
            while True:
                key, value = cursor.item()
                if not key.startswith(prefix):
                    break
                range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
                if end_value is not None and (
                    range_value > end_value or (range_value == end_value and not include_end)
                ):
                    if not cursor.prev():
                        break
                    continue
                if start_value is not None and (
                    range_value < start_value or (range_value == start_value and not include_start)
                ):
                    break
                yield value
                yielded += 1
                if limit is not None and yielded >= limit:
                    break
                if not cursor.prev():
                    break
            return
        if not cursor.set_range(start_key):
            return
        yielded = 0
        for key, value in cursor:
            if not key.startswith(prefix):
                break
            range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
            if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                continue
            if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                break
            yield value
            yielded += 1
            if limit is not None and yielded >= limit:
                break


class LMDBReadSnapshotStore(LMDBTransactionStore):
    """Read-only LMDB transaction with a verifiable MVCC identity."""

    supports_transactions = False
    supports_read_snapshots = False
    supports_verifiable_read_snapshots = True

    def __init__(self, parent: LMDBStore) -> None:
        super().__init__(parent, write=False)
        self.snapshot_identity = ReadSnapshotIdentity(
            backend="lmdb",
            kind="mvcc_transaction",
            sequence=self.txn.id(),
            verifiable=True,
        )

    def _read_only(self, *args: object, **kwargs: object) -> NoReturn:
        self._check_active()
        raise RuntimeError("read snapshot is read-only")

    put = delete = put_metadata = delete_metadata = _read_only
    put_node = delete_node = put_nodes_bulk = _read_only
    put_edge = delete_edge = put_edges_bulk = _read_only
    put_adjacency = put_adjacency_bulk = delete_adjacency = _read_only
    put_typed_adjacency = put_typed_adjacency_bulk = delete_typed_adjacency = _read_only
    put_index_entry = put_index_entries_bulk = delete_index_entry = _read_only
    put_range_index_entry = put_range_index_entries_bulk = delete_range_index_entry = _read_only

    def commit(self) -> NoReturn:
        raise RuntimeError("read snapshot cannot be committed")

    def state_sha256(self) -> str:
        """Digest every mutable namespace visible at this MVCC horizon."""
        self._check_active()
        digest = hashlib.sha256()
        databases = (
            (b"nodes", self.parent.nodes_db),
            (b"edges", self.parent.edges_db),
            (b"adj", self.parent.adj_db),
            (b"typed_adj", self.parent.typed_adj_db),
            (b"index", self.parent.index_db),
            (b"metadata", self.parent.metadata_db),
        )
        for namespace, database in databases:
            cursor = self.txn.cursor(db=database)
            for key, value in cursor:
                _update_snapshot_digest(digest, namespace, key, value)
        return digest.hexdigest()


# =========================================
# LevelDB Implementation
# =========================================

class LevelDBStore(KVStore):
    """LevelDB implementation backed by ``plyvel``.

    Args:
        path: Directory that will contain the LevelDB sub-databases.

    Examples:
        Work with raw bytes in a disposable store (requires ``plyvel``)::

            >>> from tempfile import TemporaryDirectory
            >>> from gestaltdb.kvstores import LevelDBStore
            >>> with TemporaryDirectory() as root:
            ...     store = LevelDBStore(path=root + "/db")
            ...     try:
            ...         store.put_node(b"n", b"payload")
            ...         assert store.get_node(b"n") == b"payload"
            ...     finally:
            ...         store.close()

        Most applications use ``GraphDB.create(..., backend="leveldb")``;
        see the storage-backends guide for snapshot/transaction capabilities.
    """

    @staticmethod
    def ensure_available() -> Any:
        """Import and return Plyvel, or raise installation guidance."""
        try:
            import plyvel
        except ImportError as exc:
            raise _missing_dependency_error("plyvel", extra_name="leveldb", feature_name="LevelDBStore") from exc
        return plyvel

    def __init__(self, path: str | os.PathLike[str] = 'graph_leveldb') -> None:
        """Create or open a LevelDB store. We'll store nodes/edges by prefix."""
        plyvel = self.ensure_available()
        
        self.path = path
        self.db_paths = {'nodes' : os.path.join('nodes'), 'edges': os.path.join('edges'), 'adjacency' : os.path.join('adjacency'), 'typed_adjacency': os.path.join('typed_adjacency'), 'index': os.path.join('index'), 'metadata': os.path.join('metadata')}
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        self.db_nodes = plyvel.DB(os.path.join(path, 'nodes'), create_if_missing=True)
        self.db_edges = plyvel.DB(os.path.join(path, 'edges'), create_if_missing=True)
        self.db_adj   = plyvel.DB(os.path.join(path, 'adjacency'), create_if_missing=True)
        self.db_typed_adj = plyvel.DB(os.path.join(path, 'typed_adjacency'), create_if_missing=True)
        self.db_index = plyvel.DB(os.path.join(path, 'index'), create_if_missing=True)
        self.db_metadata = plyvel.DB(os.path.join(path, 'metadata'), create_if_missing=True)
        self.db_dict = {
            'nodes' : self.db_nodes,
            'edges' : self.db_edges,
            'adjacency' : self.db_adj,
            'typed_adjacency': self.db_typed_adj,
            'index': self.db_index,
            'metadata': self.db_metadata,
        }
    # def put(self, key: bytes, value: bytes):
    #     self.db.put(key, value)

    # def get(self, key: bytes) -> bytes:
    #     return self.db.get(key)

    # def delete(self, key: bytes):
    #     self.db.delete(key)
    def get_db_path(self, db_string: str = 'nodes') -> str:
        """Return the relative path for a named LevelDB database.

        Examples:
            >>> LevelDBStore.get_db_path.__name__
            'get_db_path'
        """
        return self.db_paths[db_string]
    
    def range_iter(self, start_key: bytes, end_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        """Yield records whose keys fall within a range.

        Note:
            This generic iterator is not used by the main graph APIs.
        """
        with self.db.iterator(start=start_key, stop=end_key) as it:
            for k, v in it:
                yield k, v

    def put_metadata(self, key: bytes, value: bytes) -> None:
        """Store a metadata key/value pair."""
        self.db_metadata.put(key, value)

    def get_metadata(self, key: bytes) -> bytes | None:
        """Return a metadata value by key, or ``None``."""
        return self.db_metadata.get(key)

    def delete_metadata(self, key: bytes) -> None:
        """Delete a metadata key/value pair."""
        self.db_metadata.delete(key)

    def get_db_iterator(self, which_db: str = 'nodes') -> Iterator[tuple[bytes, bytes]]:
        """Yield all records from a named sub-database."""
        with self.db_dict[which_db].iterator() as it:
            for k, v in it:
                yield k, v

    def get_node_keys_iterator(self) -> Iterator[tuple[bytes, bytes]]:
        """Yield node database records."""
        return self.get_db_iterator(which_db='nodes')

    def get_node_keys_generator(self, num_nodes: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        """Yield node keys from the node database."""
        yielded = 0
        with self.db_nodes.iterator(start=key_offset) as it:
            for k, _ in it:
                yield k
                yielded += 1
                if num_nodes is not None and yielded == num_nodes:
                    break

    def get_edge_keys_generator(self, num_edges: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        """Yield edge keys from the edge database."""
        yielded = 0
        with self.db_edges.iterator(start=key_offset) as it:
            for k, _ in it:
                yield k
                yielded += 1
                if num_edges is not None and yielded == num_edges:
                    break


    # -- Specialized methods for nodes
    def put_node(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized node by byte key."""
        self.db_nodes.put(node_id,value)

    def get_node(self, node_id: bytes) -> bytes | None:
        """Return serialized node bytes by key, or ``None``."""
        return self.db_nodes.get(node_id)

    def delete_node(self, node_id: bytes) -> None:
        """Delete a node by byte key."""
        self.db_nodes.delete(node_id)

    # -- Specialized methods for edges
    def put_edge(self, edge_id: bytes, value: bytes) -> None:
        """Store a serialized edge by byte key."""
        self.db_edges.put(edge_id, value)

    def get_edge(self, edge_id: bytes) -> bytes | None:
        """Return serialized edge bytes by key, or ``None``."""
        return self.db_edges.get(edge_id)

    def delete_edge(self, edge_id: bytes) -> None:
        """Delete an edge by byte key."""
        self.db_edges.delete(edge_id)

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Use a WriteBatch for atomic bulk updates."""
        with self.db_nodes.write_batch() as wb:
            for node_id, val in keys_and_values.items():
                wb.put(node_id, val)

    def get_nodes_bulk(self, node_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Return serialized nodes for the requested keys."""
        results = {}
        for node_id in node_ids:
            data = self.db_nodes.get(node_id)
            if data is not None:
                results[node_id] = data
        return results
    
    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Store many serialized edges in one write batch."""
        with self.db_edges.write_batch() as wb:
            for edge_id, val in keys_and_values.items():
                wb.put(edge_id, val)
    
    def get_edges_bulk(self, edge_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Return serialized edges for the requested keys."""
        results = {}
        for edge_id in edge_ids:
            data = self.db_edges.get(edge_id)
            if data is not None:
                results[edge_id] = data
        return results
    

    # ----- Adjacency Methods -----
    def put_adjacency(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized adjacency list for a node."""
        self.db_adj.put(node_id, value)

    def get_adjacency(self, node_id: bytes) -> Optional[bytes]:
        """Return a serialized adjacency list for a node."""
        return self.db_adj.get(node_id)
    
    # -- Bulk Write: Adjacency --
    def put_adjacency_bulk(self, adj_dict: Dict[bytes, bytes]) -> None:
        """
        Insert/update multiple adjacency lists in one write batch.
        :param adj_dict: a dict mapping node_id -> serialized adjacency
        """
        with self.db_adj.write_batch() as wb:
            for node_id, val in adj_dict.items():
                wb.put(node_id, val)

    # -------------------------------------------------------------------------
    # Bulk Read: Adjacency
    # -------------------------------------------------------------------------
    def get_adjacency_bulk(self, node_ids: List[bytes]) -> Dict[bytes, bytes]:
        """Return serialized adjacency lists for the requested nodes."""
        results = {}
        for node_id in node_ids:
            data = self.db_adj.get(node_id)
            if data is not None:
                results[node_id] = data
        return results

    def delete_adjacency(self, node_id: bytes) -> None:
        """Delete a serialized adjacency list for a node."""
        self.db_adj.delete(node_id)

    # ----- Typed Adjacency Methods -----
    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Store forward and reverse typed adjacency records."""
        with self.db_typed_adj.write_batch() as wb:
            wb.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
            wb.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]) -> None:
        """Store many typed adjacency records in one write batch."""
        with self.db_typed_adj.write_batch() as wb:
            for source_id, target_id, edge_type, edge_id in records:
                wb.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
                wb.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Delete forward and reverse typed adjacency records."""
        with self.db_typed_adj.write_batch() as wb:
            wb.delete(_typed_adjacency_key("out", source_id, edge_type, edge_id))
            wb.delete(_typed_adjacency_key("in", target_id, edge_type, edge_id))

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> Iterator[tuple[bytes, bytes]]:
        """Yield typed adjacency ``(edge_id, neighbor_id)`` pairs."""
        prefix = _typed_adjacency_prefix(direction, node_id, edge_type)
        with self.db_typed_adj.iterator(prefix=prefix) as it:
            for key, neighbor_id in it:
                edge_id = key[len(prefix):]
                yield edge_id, neighbor_id

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Store one sorted index entry."""
        self.db_index.put(_index_key(index_name, key_parts, value), value)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]) -> None:
        """Store many sorted index entries in one write batch."""
        with self.db_index.write_batch() as wb:
            for index_name, key_parts, value in entries:
                wb.put(_index_key(index_name, key_parts, value), value)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Delete one sorted index entry."""
        self.db_index.delete(_index_key(index_name, key_parts, value))

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]) -> Iterator[bytes]:
        """Yield values whose index key starts with ``key_parts``."""
        prefix = _index_prefix(index_name, key_parts)
        with self.db_index.iterator(prefix=prefix) as it:
            for _, value in it:
                yield value

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Store one sorted range index entry."""
        self.db_index.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]) -> None:
        """Store many sorted range index entries in one write batch."""
        with self.db_index.write_batch() as wb:
            for index_name, key_parts, range_value, value in entries:
                wb.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Delete one sorted range index entry."""
        self.db_index.delete(_range_index_key(index_name, key_parts, range_value, value))

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True, *, reverse: bool = False, limit: int | None = None) -> Iterator[bytes]:
        """Yield values whose range index key falls between start and end values."""
        _validate_range_scan_options(reverse, limit)
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        stop_key = prefix + (b"\xff" if end_value is None else end_value + b"\xff")
        iterator_options = (
            {"start": prefix, "stop": stop_key, "include_stop": True, "reverse": True}
            if reverse
            else {"start": start_key}
        )
        with self.db_index.iterator(**iterator_options) as it:
            yielded = 0
            for key, value in it:
                if not key.startswith(prefix):
                    break
                range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
                if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                    if reverse:
                        break
                    continue
                if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                    if reverse:
                        continue
                    break
                yield value
                yielded += 1
                if limit is not None and yielded >= limit:
                    break

    def close(self) -> None:
        """Close all LevelDB sub-databases."""
        self.db_metadata.close()
        self.db_index.close()
        self.db_typed_adj.close()
        self.db_adj.close()
        self.db_edges.close()
        self.db_nodes.close()

    def read_snapshot(self) -> NoReturn:
        raise NotImplementedError(
            "LevelDBStore cannot provide a unified snapshot across its independent "
            "node, edge, adjacency, index, and metadata databases"
        )


class PyRexStore(KVStore):
    """RocksDB implementation backed by ``pyrex-rocksdb``.

    ``PyRexStore`` uses one physical RocksDB database with prefixed keys instead
    of separate databases. This lets node, edge, adjacency, and typed adjacency
    records share RocksDB's write path and makes it possible to benchmark
    RocksDB tuning options against the existing LevelDB backend.

    Args:
        path: Directory for the RocksDB database.
        parallelism: Optional number of RocksDB background threads.
        max_background_jobs: Optional RocksDB background job limit.
        write_buffer_size: Optional write buffer size in bytes.
        bloom_bits_per_key: Optional block-based Bloom filter bits per key.
        disable_wal: Disable RocksDB's write-ahead log for faster but less
            durable ingestion benchmarks.

    Examples:
        Work with raw bytes in a disposable store (requires ``pyrex-rocksdb``)::

            >>> from tempfile import TemporaryDirectory
            >>> from gestaltdb.kvstores import PyRexStore
            >>> with TemporaryDirectory() as root:
            ...     store = PyRexStore(path=root + "/db")
            ...     try:
            ...         store.put_node(b"n", b"payload")
            ...         assert store.get_node(b"n") == b"payload"
            ...     finally:
            ...         store.close()

        ``GraphDB.create(..., backend="pyrex")`` adds graph semantics. Plain
        and transactional PyRex have different ingestion/snapshot capabilities;
        see the storage-backends guide before selecting transaction mode.
    """

    _SEP = b"\x1f"

    @staticmethod
    def ensure_available() -> Any:
        """Import and return PyRex, or raise installation guidance."""
        try:
            import pyrex
        except ImportError as exc:
            raise _missing_dependency_error("pyrex", extra_name="rocksdb", feature_name="PyRexStore") from exc
        return pyrex

    def __init__(
        self,
        path: str | os.PathLike[str] = "graph_rocksdb",
        parallelism: int | None = None,
        max_background_jobs: int | None = None,
        write_buffer_size: int | None = None,
        bloom_bits_per_key: float | None = None,
        disable_wal: bool = False,
        transactional: bool = False,
        transaction_db_options: Any = None,
    ) -> None:
        """Open a PyRex/RocksDB store with optional tuning settings."""
        pyrex = self.ensure_available()

        self.path = path
        options = pyrex.PyOptions()
        options.create_if_missing = True
        if parallelism is not None:
            options.increase_parallelism(parallelism)
        if max_background_jobs is not None:
            options.max_background_jobs = max_background_jobs
        if write_buffer_size is not None:
            options.write_buffer_size = write_buffer_size
            options.cf_write_buffer_size = write_buffer_size
        if bloom_bits_per_key is not None:
            options.use_block_based_bloom_filter(bloom_bits_per_key)

        self._pyrex = pyrex
        self.transactional = transactional
        if transactional:
            if not getattr(pyrex, "has_transactions", False):
                raise ImportError("PyRexStore transactional mode requires pyrex-rocksdb>=0.4.1")
            transaction_db_options = transaction_db_options or pyrex.TransactionDBOptions()
            self.db = pyrex.TransactionDB(path, options, transaction_db_options)
            self.supports_transactions = True
            self.supports_read_snapshots = True
        else:
            self.db = pyrex.PyRocksDB(path, options)
            self.supports_transactions = False
            self.supports_read_snapshots = False
        self.supports_verifiable_read_snapshots = False
        self.write_options = pyrex.WriteOptions()
        self.write_options.disable_wal = disable_wal

    def transaction(self, transaction_options: Any = None, **options: Any) -> "PyRexTransactionStore":
        """Open a RocksDB transaction-bound store."""
        if not self.transactional:
            raise NotImplementedError("PyRexStore transactions require PyRexStore(transactional=True)")
        transaction_options = transaction_options or self._pyrex.TransactionOptions()
        txn = self.db.begin_transaction(self.write_options, transaction_options)
        return PyRexTransactionStore(self, txn)

    def current_snapshot_sequence(self) -> int | None:
        for name in ("latest_sequence_number", "get_latest_sequence_number"):
            value = getattr(self.db, name, None)
            if value is None:
                continue
            value = value() if callable(value) else value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    @contextmanager
    def read_snapshot(self) -> Iterator["PyRexReadSnapshotStore"]:
        """Pin reads to one RocksDB transaction snapshot when available."""
        if not self.transactional:
            raise NotImplementedError(
                "PyRexStore read snapshots require PyRexStore(transactional=True)"
            )
        options = self._pyrex.TransactionOptions()
        option_snapshot = hasattr(options, "set_snapshot")
        if option_snapshot:
            options.set_snapshot = True
        txn = self.db.begin_transaction(self.write_options, options)
        try:
            if not option_snapshot:
                setter = getattr(txn, "set_snapshot", None)
                if setter is None:
                    raise NotImplementedError(
                        "the installed PyRex runtime cannot pin transaction snapshots"
                    )
                setter()
            sequence = _pyrex_snapshot_sequence(txn)
            snapshot = PyRexReadSnapshotStore(self, txn, sequence=sequence)
            try:
                yield snapshot
            finally:
                snapshot.close()
        except Exception:
            if txn.is_active:
                txn.rollback()
            raise

    def _key(self, prefix: bytes, key: bytes) -> bytes:
        """Build a prefixed RocksDB key."""
        return prefix + self._SEP + key

    def _typed_key(self, direction: str, node_id: bytes, edge_type: str, edge_id: bytes = b"") -> bytes:
        """Build a typed adjacency key in the shared RocksDB keyspace."""
        return self._SEP.join([b"T", direction.encode("utf-8"), node_id, edge_type.encode("utf-8"), edge_id])

    def _typed_key_bytes(self, direction_prefix: bytes, node_id: bytes, edge_type: bytes, edge_id: bytes) -> bytes:
        """Build a typed adjacency key when direction and type are already encoded."""
        return direction_prefix + node_id + self._SEP + edge_type + self._SEP + edge_id

    def has_native_columnar_ingestion(self) -> bool:
        """Return whether this PyRex runtime exposes native columnar writes."""
        return hasattr(self.db, "write_columnar_batch")

    def has_native_arrow_scan(self) -> bool:
        """Return whether this PyRex runtime exposes native Arrow scans.

        Requires ``scan_arrow`` plus ``create_snapshot`` on the underlying
        handle. Transactional handles do not expose them in 0.5.0; 0.6.0 adds
        native committed-state scans of the default column family. This probe
        is not authenticated claim-source or named-column-family admission.
        """
        return hasattr(self.db, "scan_arrow") and hasattr(self.db, "create_snapshot")

    def _put_raw(self, key: bytes, value: bytes) -> None:
        self.db.put(key, value, self.write_options)

    def _get_raw(self, key: bytes) -> bytes | None:
        return self.db.get(key)

    def _delete_raw(self, key: bytes) -> None:
        self.db.delete(key, self.write_options)

    def _write_batch(self, batch: Any) -> None:
        self.db.write(batch, self.write_options)

    def _iter_key_values_from(self, start_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        temp_txn = None
        if hasattr(self.db, "new_iterator"):
            iterator = self.db.new_iterator()
        else:
            temp_txn = self.db.begin_transaction(self.write_options)
            iterator = temp_txn.new_iterator()
        try:
            iterator.seek(start_key)
            while iterator.valid():
                yield iterator.key(), iterator.value()
                iterator.next()
            iterator.check_status()
        finally:
            if temp_txn is not None and temp_txn.is_active:
                temp_txn.rollback()

    def _iter_key_values_reverse_from(self, end_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        temp_txn = None
        if hasattr(self.db, "new_iterator"):
            iterator = self.db.new_iterator()
        else:
            temp_txn = self.db.begin_transaction(self.write_options)
            iterator = temp_txn.new_iterator()
        try:
            if hasattr(iterator, "seek_for_prev") and hasattr(iterator, "prev"):
                iterator.seek_for_prev(end_key)
                while iterator.valid():
                    yield iterator.key(), iterator.value()
                    iterator.prev()
                iterator.check_status()
                return
        finally:
            if temp_txn is not None and temp_txn.is_active:
                temp_txn.rollback()
        values = list(self._iter_key_values_from(b""))
        for key, value in reversed(values):
            if key <= end_key:
                yield key, value

    def _write_columnar_batch(self, keys: list[bytes], values: object) -> None:
        """Write key/value lists through PyRex's native columnar API."""
        self.db.write_columnar_batch(keys, values, write_options=self.write_options)

    def _iter_prefixed_keys(self, prefix: bytes, key_offset: bytes | None = None, limit: int | None = None) -> Iterator[bytes]:
        """Yield unprefixed keys for a prefixed key range."""
        yielded = 0
        start_key = prefix if key_offset is None else prefix + key_offset
        for key, _ in self._iter_key_values_from(start_key):
            if not key.startswith(prefix):
                break
            yield key[len(prefix):]
            yielded += 1
            if limit is not None and yielded == limit:
                break

    def put(self, key: bytes, value: bytes) -> None:
        """Store a raw key/value pair in the shared RocksDB keyspace."""
        self._put_raw(key, value)

    def get(self, key: bytes) -> bytes | None:
        """Return a raw value by key."""
        return self._get_raw(key)

    def delete(self, key: bytes) -> None:
        """Delete a raw key/value pair."""
        self._delete_raw(key)

    def range_iter(self, start_key: bytes, end_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        """Yield raw records whose keys fall within an inclusive range."""
        for key, value in self._iter_key_values_from(start_key):
            if key > end_key:
                break
            yield key, value

    def close(self) -> None:
        """Close the RocksDB database."""
        self.db.close()

    def put_metadata(self, key: bytes, value: bytes) -> None:
        """Store a metadata key/value pair."""
        self._put_raw(self._key(b"M", key), value)

    def get_metadata(self, key: bytes) -> bytes | None:
        """Return a metadata value by key, or ``None``."""
        return self._get_raw(self._key(b"M", key))

    def delete_metadata(self, key: bytes) -> None:
        """Delete a metadata key/value pair."""
        self._delete_raw(self._key(b"M", key))

    def put_node(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized node by byte key."""
        self._put_raw(self._key(b"N", node_id), value)

    def get_node(self, node_id: bytes) -> bytes | None:
        """Return serialized node bytes by key, or ``None``."""
        return self._get_raw(self._key(b"N", node_id))

    def delete_node(self, node_id: bytes) -> None:
        """Delete a node by byte key."""
        self._delete_raw(self._key(b"N", node_id))

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Store many serialized nodes in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for node_id, value in keys_and_values.items():
            batch.put(self._key(b"N", node_id), value)
        self._write_batch(batch)

    def ingest_nodes_columnar(self, node_list: NodeList, *, native: bool = True) -> None:
        """Store columnar nodes, using native PyRex ingestion when available."""
        if native and self.has_native_columnar_ingestion():
            keys = [self._key(b"N", node_id) for node_id in node_list.node_ids]
            values = node_list.node_values_column if node_list.node_values_column is not None else node_list.node_values
            self._write_columnar_batch(keys, values)
            return
        self.put_nodes_bulk(dict(zip(node_list.node_ids, node_list.node_values)))

    def get_nodes_bulk(self, node_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Return serialized nodes for the requested keys."""
        results = {}
        for node_id in node_ids:
            data = self.get_node(node_id)
            if data is not None:
                results[node_id] = data
        return results

    def put_edge(self, edge_id: bytes, value: bytes) -> None:
        """Store a serialized edge by byte key."""
        self._put_raw(self._key(b"E", edge_id), value)

    def get_edge(self, edge_id: bytes) -> bytes | None:
        """Return serialized edge bytes by key, or ``None``."""
        return self._get_raw(self._key(b"E", edge_id))

    def delete_edge(self, edge_id: bytes) -> None:
        """Delete an edge by byte key."""
        self._delete_raw(self._key(b"E", edge_id))

    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]) -> None:
        """Store many serialized edges in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for edge_id, value in keys_and_values.items():
            batch.put(self._key(b"E", edge_id), value)
        self._write_batch(batch)

    def get_edges_bulk(self, edge_ids: Iterable[bytes]) -> dict[bytes, bytes]:
        """Return serialized edges for the requested keys."""
        results = {}
        for edge_id in edge_ids:
            data = self.get_edge(edge_id)
            if data is not None:
                results[edge_id] = data
        return results

    def get_edge_keys_generator(self, num_edges: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        """Yield edge keys from the shared RocksDB keyspace."""
        yield from self._iter_prefixed_keys(b"E" + self._SEP, key_offset=key_offset, limit=num_edges)

    def get_node_keys_generator(self, num_nodes: int | None = None, key_offset: bytes | None = None) -> Iterator[bytes]:
        """Yield node keys from the shared RocksDB keyspace."""
        yield from self._iter_prefixed_keys(b"N" + self._SEP, key_offset=key_offset, limit=num_nodes)

    def put_adjacency(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized adjacency list for a node."""
        self._put_raw(self._key(b"A", node_id), value)

    def get_adjacency(self, node_id: bytes) -> Optional[bytes]:
        """Return a serialized adjacency list for a node."""
        return self._get_raw(self._key(b"A", node_id))

    def put_adjacency_bulk(self, adj_dict: Dict[bytes, bytes]) -> None:
        """Store many serialized adjacency lists in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for node_id, value in adj_dict.items():
            batch.put(self._key(b"A", node_id), value)
        self._write_batch(batch)

    def get_adjacency_bulk(self, node_ids: List[bytes]) -> Dict[bytes, bytes]:
        """Return serialized adjacency lists for the requested nodes."""
        results = {}
        for node_id in node_ids:
            data = self.get_adjacency(node_id)
            if data is not None:
                results[node_id] = data
        return results

    def delete_adjacency(self, node_id: bytes) -> None:
        """Delete a serialized adjacency list for a node."""
        self._delete_raw(self._key(b"A", node_id))

    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Store forward and reverse typed adjacency records."""
        self.put_typed_adjacency_bulk([(source_id, target_id, edge_type, edge_id)])

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]) -> None:
        """Store many typed adjacency records in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for source_id, target_id, edge_type, edge_id in records:
            batch.put(self._typed_key("out", source_id, edge_type, edge_id), target_id)
            batch.put(self._typed_key("in", target_id, edge_type, edge_id), source_id)
        self._write_batch(batch)

    def ingest_edges_columnar(self, edge_list: EdgeList, *, append_only: bool = True, native: bool = True, maintain_indexes: bool = True) -> None:
        """Store columnar typed edges, using native PyRex ingestion when available."""
        if not append_only:
            raise NotImplementedError("native columnar edge ingestion currently requires append_only=True")
        if native and self.has_native_columnar_ingestion():
            edge_prefix = b"E" + self._SEP
            out_prefix = b"T" + self._SEP + b"out" + self._SEP
            in_prefix = b"T" + self._SEP + b"in" + self._SEP
            edge_type_bytes = {edge_type: edge_type.encode("utf-8") for edge_type in set(edge_list.edge_types)}
            edge_keys = [edge_prefix + edge_id for edge_id in edge_list.edge_ids]
            out_keys = [
                self._typed_key_bytes(out_prefix, source_id, edge_type_bytes[edge_type], edge_id)
                for source_id, edge_type, edge_id in zip(edge_list.sources, edge_list.edge_types, edge_list.edge_ids)
            ]
            in_keys = [
                self._typed_key_bytes(in_prefix, target_id, edge_type_bytes[edge_type], edge_id)
                for target_id, edge_type, edge_id in zip(edge_list.targets, edge_list.edge_types, edge_list.edge_ids)
            ]
            edge_values = edge_list.edge_values_column if edge_list.edge_values_column is not None else edge_list.edge_values
            targets = edge_list.targets_column if edge_list.targets_column is not None else edge_list.targets
            sources = edge_list.sources_column if edge_list.sources_column is not None else edge_list.sources
            self._write_columnar_batch(edge_keys, edge_values)
            self._write_columnar_batch(out_keys, targets)
            self._write_columnar_batch(in_keys, sources)
            if maintain_indexes:
                edge_ids = edge_list.edge_ids_column if edge_list.edge_ids_column is not None else edge_list.edge_ids
                self._write_columnar_batch(
                    [_index_key("edge_type", [edge_type.encode("utf-8")], edge_id) for edge_type, edge_id in zip(edge_list.edge_types, edge_list.edge_ids)],
                    edge_ids,
                )
            return
        self.put_edges_bulk(dict(zip(edge_list.edge_ids, edge_list.edge_values)))
        self.put_typed_adjacency_bulk(
            list(zip(edge_list.sources, edge_list.targets, edge_list.edge_types, edge_list.edge_ids))
        )
        if maintain_indexes:
            self.put_index_entries_bulk([
                ("edge_type", [edge_type.encode("utf-8")], edge_id)
                for edge_type, edge_id in zip(edge_list.edge_types, edge_list.edge_ids)
            ])

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes) -> None:
        """Delete forward and reverse typed adjacency records."""
        batch = self._pyrex.PyWriteBatch()
        batch.delete(self._typed_key("out", source_id, edge_type, edge_id))
        batch.delete(self._typed_key("in", target_id, edge_type, edge_id))
        self._write_batch(batch)

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> Iterator[tuple[bytes, bytes]]:
        """Yield typed adjacency ``(edge_id, neighbor_id)`` pairs."""
        prefix = self._typed_key(direction, node_id, edge_type)
        for key, value in self._iter_key_values_from(prefix):
            if not key.startswith(prefix):
                break
            yield key[len(prefix):], value

    def count_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out") -> int:
        """Count typed adjacency records without constructing record dictionaries."""
        prefix = self._typed_key(direction, node_id, edge_type)
        total = 0
        for key, _ in self._iter_key_values_from(prefix):
            if not key.startswith(prefix):
                break
            total += 1
        return total

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Store one sorted index entry."""
        self._put_raw(_index_key(index_name, key_parts, value), value)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]) -> None:
        """Store many sorted index entries in one write batch."""
        batch = self._pyrex.PyWriteBatch()
        for index_name, key_parts, value in entries:
            batch.put(_index_key(index_name, key_parts, value), value)
        self._write_batch(batch)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes) -> None:
        """Delete one sorted index entry."""
        self._delete_raw(_index_key(index_name, key_parts, value))

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]) -> Iterator[bytes]:
        """Yield values whose index key starts with ``key_parts``."""
        prefix = _index_prefix(index_name, key_parts)
        for key, value in self._iter_key_values_from(prefix):
            if not key.startswith(prefix):
                break
            yield value

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Store one sorted range index entry."""
        self._put_raw(_range_index_key(index_name, key_parts, range_value, value), value)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]) -> None:
        """Store many sorted range index entries in one write batch."""
        batch = self._pyrex.PyWriteBatch()
        for index_name, key_parts, range_value, value in entries:
            batch.put(_range_index_key(index_name, key_parts, range_value, value), value)
        self._write_batch(batch)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes) -> None:
        """Delete one sorted range index entry."""
        self._delete_raw(_range_index_key(index_name, key_parts, range_value, value))

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True, *, reverse: bool = False, limit: int | None = None) -> Iterator[bytes]:
        """Yield values whose range index key falls between start and end values."""
        _validate_range_scan_options(reverse, limit)
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        end_key = prefix + (b"\xff" if end_value is None else end_value + b"\xff")
        entries = (
            self._iter_key_values_reverse_from(end_key)
            if reverse
            else self._iter_key_values_from(start_key)
        )
        yielded = 0
        for key, value in entries:
            if not key.startswith(prefix):
                if reverse and key > prefix:
                    continue
                break
            range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
            if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                if reverse:
                    break
                continue
            if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                if reverse:
                    continue
                break
            yield value
            yielded += 1
            if limit is not None and yielded >= limit:
                break


class PyRexTransactionStore(PyRexStore):
    """Transaction-bound PyRex/RocksDB store."""

    supports_transactions = True

    def __init__(self, parent: PyRexStore, txn: Any) -> None:
        self.parent = parent
        self.db = parent.db
        self.txn = txn
        self._pyrex = parent._pyrex
        self.write_options = parent.write_options
        self.transactional = True

    def _check_active(self) -> None:
        if not self.txn.is_active:
            raise RuntimeError("transaction is no longer active")

    def commit(self) -> None:
        self._check_active()
        self.txn.commit(self.write_options)

    def rollback(self) -> None:
        if self.txn.is_active:
            self.txn.rollback()

    def close(self) -> None:
        self.rollback()

    def transaction(self, **options: Any) -> NoReturn:  # type: ignore[override]  # Nested calls intentionally reject positional options.
        raise NotImplementedError("nested transactions are not supported")

    def has_native_columnar_ingestion(self) -> bool:
        return False

    def _put_raw(self, key: bytes, value: bytes) -> None:
        self._check_active()
        self.txn.put(key, value)

    def _get_raw(self, key: bytes) -> bytes | None:
        self._check_active()
        return self.txn.get(key)

    def _delete_raw(self, key: bytes) -> None:
        self._check_active()
        self.txn.delete(key)

    def _write_batch(self, batch: Any) -> None:
        self._check_active()
        self.txn.write(batch)

    def _iter_key_values_from(self, start_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        self._check_active()
        iterator = self.txn.new_iterator()
        iterator.seek(start_key)
        while iterator.valid():
            yield iterator.key(), iterator.value()
            iterator.next()
        iterator.check_status()

    def _iter_key_values_reverse_from(self, end_key: bytes) -> Iterator[tuple[bytes, bytes]]:
        self._check_active()
        iterator = self.txn.new_iterator()
        if hasattr(iterator, "seek_for_prev") and hasattr(iterator, "prev"):
            iterator.seek_for_prev(end_key)
            while iterator.valid():
                yield iterator.key(), iterator.value()
                iterator.prev()
            iterator.check_status()
            return
        values = list(self._iter_key_values_from(b""))
        for key, value in reversed(values):
            if key <= end_key:
                yield key, value


def _pyrex_snapshot_sequence(txn: object) -> int | None:
    """Return a bound RocksDB snapshot sequence when exposed by PyRex."""
    for name in (
        "snapshot_sequence_number",
        "get_snapshot_sequence_number",
        "snapshot_sequence",
        "get_snapshot_sequence",
    ):
        value = getattr(txn, name, None)
        if value is None:
            continue
        value = value() if callable(value) else value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _update_snapshot_digest(digest: "hashlib._Hash", namespace: bytes, key: bytes, value: bytes) -> None:
    for part in (namespace, key, value):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)


class PyRexReadSnapshotStore(PyRexTransactionStore):
    """Read-only transaction-bound PyRex store."""

    supports_transactions = False
    supports_read_snapshots = False

    def __init__(self, parent: PyRexStore, txn: Any, *, sequence: int | None) -> None:
        super().__init__(parent, txn)
        self.snapshot_identity = ReadSnapshotIdentity(
            backend="pyrex",
            kind="rocksdb_transaction_snapshot",
            sequence=sequence,
            verifiable=sequence is not None,
        )
        self.supports_verifiable_read_snapshots = sequence is not None

    def _read_only(self, *args: object, **kwargs: object) -> NoReturn:
        self._check_active()
        raise RuntimeError("read snapshot is read-only")

    put = delete = put_metadata = delete_metadata = _read_only
    put_node = delete_node = put_nodes_bulk = _read_only
    put_edge = delete_edge = put_edges_bulk = _read_only
    put_adjacency = put_adjacency_bulk = delete_adjacency = _read_only
    put_typed_adjacency = put_typed_adjacency_bulk = delete_typed_adjacency = _read_only
    put_index_entry = put_index_entries_bulk = delete_index_entry = _read_only
    put_range_index_entry = put_range_index_entries_bulk = delete_range_index_entry = _read_only

    def commit(self) -> NoReturn:
        raise RuntimeError("read snapshot cannot be committed")

    def state_sha256(self) -> str:
        self._check_active()
        digest = hashlib.sha256()
        for key, value in self._iter_key_values_from(b""):
            _update_snapshot_digest(digest, b"rocksdb", key, value)
        return digest.hexdigest()

class SimpleIndexCounterKVStore:
    """This is to help with lowering storage requirements 
    for edge and node keys, by casting them to long ints. 

    It makes use of the struct.pack and struct.unpack functions 
    and a simple counter (also stored in the medatadata) to count the number of  
    keys (and hence the index) already entered. 
    """
    def __init__(self, dbenv = None, db_path = b'nodes'):
        """Initialize an index counter helper.

        Args:
            dbenv: LMDB environment.
            db_path: Named LMDB database for the counter mapping.
        """
        self.db_path = db_path
        self.env = dbenv
        self.kvdb = self.env.open_db(self.db_path)        
        self.max_key_idx = None
        
    def get_num_keys(self):
        """Return the number of keys already assigned."""
        _b = self.get('num_keys'.encode('utf-8'))
        if _b is None:
            return 0
        num_keys = struct.unpack('<L',_b)[0]
        return num_keys
    
    def put_num_keys(self, num_keys):
        """Persist the number of keys already assigned."""
        num_keys_bytes = struct.pack('<L',num_keys)
        _b = self.put('num_keys'.encode('utf-8'), num_keys_bytes)

    def put(self, key : bytes, value : bytes):
        """Store a counter metadata key/value pair."""
        with self.env.begin(write=True, db=self.kvdb) as txn:
            txn.put(key, value)

    def get(self, key):
        """Read a counter metadata value by key."""
        with self.env.begin(write=False, db=self.kvdb) as txn:
            vv = txn.get(key)
            return vv
    
    def encode_db_key(self, key):
        """If the key exists, it will return the existing key.
        if the key does not exist, it will add it to the KV store with a new increment, 
        and return that.
        """
        _enc_key = key.encode('utf-8')
        v = self.get(_enc_key)
        if v is None:
            num_keys = self.get_num_keys()
            num_keys += 1
            k_idx = num_keys
            self.put_num_keys(num_keys)
            self.put(_enc_key, _pack_long_int(num_keys))
            return num_keys
        return _unpack_long_int(v)
    def decode_db_key(self, key):
        """Return the stored encoded key bytes for a user key."""
        v = self.get(key.encode('utf-8'))
