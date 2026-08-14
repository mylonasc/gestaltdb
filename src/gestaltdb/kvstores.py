from typing import Optional, Dict, List, Union
import base64
import os
import struct


_TYPED_ADJ_SEP = b"\x1f"


def _to_bytes(value) -> bytes:
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


def _index_part(value) -> bytes:
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


def _index_key(index_name, key_parts, value=b"") -> bytes:
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
    return b"I" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts)


def _index_prefix(index_name, key_parts) -> bytes:
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
    return b"I" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts) + _TYPED_ADJ_SEP


def _range_index_key(index_name, key_parts, range_value: bytes, value=b"") -> bytes:
    """Build a sorted range index key."""
    parts = [_index_part(index_name)]
    parts.extend(_index_part(part) for part in key_parts)
    parts.append(range_value)
    parts.append(_index_part(value))
    return b"R" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts)


def _range_index_prefix(index_name, key_parts) -> bytes:
    """Build the prefix used for sorted range index scans."""
    parts = [_index_part(index_name)]
    parts.extend(_index_part(part) for part in key_parts)
    return b"R" + _TYPED_ADJ_SEP + _TYPED_ADJ_SEP.join(parts) + _TYPED_ADJ_SEP


def _missing_dependency_error(package_name, install_name=None, feature_name=None):
    """Build a consistent optional dependency error.

    Examples:
        >>> "lmdb" in str(_missing_dependency_error("lmdb"))
        True
    """
    install_name = install_name or package_name
    feature_name = feature_name or package_name
    return ImportError(
        f"Missing optional dependency '{package_name}' required for {feature_name}. "
        f"Install it with `python -m pip install {install_name}` or `uv add {install_name}`."
    )


def _pack_long_int(int_val):
    """Pack an integer as little-endian unsigned long bytes.

    Examples:
        >>> _unpack_long_int(_pack_long_int(3))
        3
    """
    return struct.pack('<L', int_val)

def _unpack_long_int(int_val):
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

    # The basic K/V methods:
    def put(self, key: bytes, value: bytes):
        """Store a raw key/value pair."""
        raise NotImplementedError

    def get(self, key: bytes) -> bytes:
        """Return a raw value by key."""
        raise NotImplementedError

    def delete(self, key: bytes):
        """Delete a raw key/value pair."""
        raise NotImplementedError

    def range_iter(self, start_key: bytes, end_key: bytes):
        """Iterate over keys from start_key to end_key (inclusive)."""
        raise NotImplementedError

    def close(self):
        """Close any resources owned by the store."""
        raise NotImplementedError

    def put_metadata(self, key: bytes, value: bytes):
        """Store a metadata key/value pair."""
        raise NotImplementedError

    def get_metadata(self, key: bytes) -> bytes:
        """Return a metadata value by key."""
        raise NotImplementedError

    def delete_metadata(self, key: bytes):
        """Delete a metadata key/value pair."""
        raise NotImplementedError

    # The specialized node/edge methods:
    def put_node(self, node_id: str, value: bytes):
        """Store a node (serialized)."""
        raise NotImplementedError

    def get_node(self, node_id: str) -> bytes:
        """Retrieve a node by ID."""
        raise NotImplementedError

    def delete_node(self, node_id: str):
        """Delete a node."""
        raise NotImplementedError

    def put_edge(self, edge_id: str, value: bytes):
        """Store an edge (serialized)."""
        raise NotImplementedError

    def get_edge(self, edge_id: str) -> bytes:
        """Retrieve an edge."""
        raise NotImplementedError

    def delete_edge(self, edge_id: str):
        """Delete an edge."""
        raise NotImplementedError

    def put_nodes_bulk(self, keys_and_values: dict[str, bytes]):
        """
        Store multiple node (serialized) values in a single batch/transaction if possible.
        """
        raise NotImplementedError

    def get_nodes_bulk(self, node_ids: list[str]) -> dict[str, bytes]:
        """Retrieve multiple serialized nodes by key."""
        raise NotImplementedError

    def put_edges_bulk(self, keys_and_values: dict[str, bytes]):
        """Store multiple serialized edges."""
        raise NotImplementedError
    
    def get_edges_bulk(self, edge_ids: list[str]) -> dict[str, bytes]:
        """Retrieve multiple serialized edges by key."""
        raise NotImplementedError

    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Store typed adjacency records for an edge."""
        raise NotImplementedError

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]):
        """Store typed adjacency records for multiple edges."""
        for source_id, target_id, edge_type, edge_id in records:
            self.put_typed_adjacency(source_id, target_id, edge_type, edge_id)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Delete typed adjacency records for an edge."""
        raise NotImplementedError

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out"):
        """Yield typed adjacency records for a node and edge type."""
        raise NotImplementedError

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Store one sorted index entry.

        Args:
            index_name: Logical index name, such as ``"node_label"``.
            key_parts: Ordered components used as the scan prefix.
            value: Entity ID or payload returned by prefix scans.

        Examples:
            >>> store.put_index_entry("node_label", [b"Drug"], b"drug-1")  # doctest: +SKIP
        """
        raise NotImplementedError

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]):
        """Store many sorted index entries.

        Args:
            entries: Tuples of ``(index_name, key_parts, value)``.

        Examples:
            >>> store.put_index_entries_bulk([("node_label", [b"Drug"], b"drug-1")])  # doctest: +SKIP
        """
        for index_name, key_parts, value in entries:
            self.put_index_entry(index_name, key_parts, value)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Delete one sorted index entry.

        Args:
            index_name: Logical index name.
            key_parts: Ordered components used when the entry was written.
            value: Entity ID or payload used when the entry was written.

        Examples:
            >>> store.delete_index_entry("node_label", [b"Drug"], b"drug-1")  # doctest: +SKIP
        """
        raise NotImplementedError

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]):
        """Yield values whose index key starts with ``key_parts``.

        Args:
            index_name: Logical index name.
            key_parts: Ordered prefix components.

        Yields:
            Values associated with matching index entries.

        Examples:
            >>> list(store.iter_index_prefix("node_label", [b"Drug"]))  # doctest: +SKIP
            [b'drug-1']
        """
        raise NotImplementedError

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Store one sorted range index entry."""
        raise NotImplementedError

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]):
        """Store many sorted range index entries."""
        for index_name, key_parts, range_value, value in entries:
            self.put_range_index_entry(index_name, key_parts, range_value, value)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Delete one sorted range index entry."""
        raise NotImplementedError

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True):
        """Yield values whose range index key falls between start and end values."""
        raise NotImplementedError

    def ingest_nodes_columnar(self, node_list, *, native: bool = True):
        """Store columnar nodes with caller-provided serialized values."""
        self.put_nodes_bulk(dict(zip(node_list.node_ids, node_list.node_values)))

    def ingest_edges_columnar(self, edge_list, *, append_only: bool = True, native: bool = True):
        """Store columnar typed edges with caller-provided serialized values."""
        if not append_only:
            raise NotImplementedError("columnar edge ingestion currently requires append_only=True")
        self.put_edges_bulk(dict(zip(edge_list.edge_ids, edge_list.edge_values)))
        self.put_typed_adjacency_bulk(
            list(zip(edge_list.sources, edge_list.targets, edge_list.edge_types, edge_list.edge_ids))
        )
        self.put_index_entries_bulk([
            ("edge_type", [edge_type.encode("utf-8")], edge_id)
            for edge_type, edge_id in zip(edge_list.edge_types, edge_list.edge_ids)
        ])

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
        >>> store = LMDBStore(path="/tmp/example_graph_lmdb")  # doctest: +SKIP
    """

    def __init__(self, path='graph_lmdb', map_size=10_485_760, map_id = True, map_keys = False):
        """
        Creates/opens an LMDB environment with three named sub-databases:
          - b'nodes' for node data
          - b'edges' for edge data
          - b'adj'   for adjacency lists
        """
        try:
            import lmdb
        except ImportError as exc:
            raise _missing_dependency_error("lmdb", feature_name="LMDBStore") from exc

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
    def put(self, key: bytes, value: bytes):
        """Placeholder generic put; graph code uses specialized methods."""
        # For LMDB, we'd need to decide which db to put to. This might remain unused.
        pass

    def get(self, key: bytes) -> bytes:
        """Placeholder generic get; graph code uses specialized methods."""
        # Not used directly.
        return None

    def delete(self, key: bytes):
        """Placeholder generic delete; graph code uses specialized methods."""
        pass

    def range_iter(self, start_key: bytes, end_key: bytes):
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

    def close(self):
        """Close the LMDB environment."""
        self.env.close()

    def put_metadata(self, key: bytes, value: bytes):
        """Store a metadata key/value pair."""
        with self.env.begin(write=True, db=self.metadata_db) as txn:
            txn.put(key, value)

    def get_metadata(self, key: bytes) -> bytes:
        """Return a metadata value by key, or ``None``."""
        with self.env.begin(write=False, db=self.metadata_db) as txn:
            return txn.get(key)

    def delete_metadata(self, key: bytes):
        """Delete a metadata key/value pair."""
        with self.env.begin(write=True, db=self.metadata_db) as txn:
            txn.delete(key)

    # -- Specialized node methods
    def put_node(self, node_id: bytes, value: bytes):
        """Store a serialized node by byte key."""
        with self.env.begin(write=True, db=self.nodes_db) as txn:
            txn.put(node_id, value)

    def get_node(self, node_id: bytes) -> bytes:
        """Return serialized node bytes by key, or ``None``."""
        with self.env.begin(write=False, db=self.nodes_db) as txn:
            return txn.get(node_id)

    def delete_node(self, node_id: bytes):
        """Delete a node by byte key."""
        with self.env.begin(write=True, db=self.nodes_db) as txn:
            txn.delete(node_id)

    # -- Specialized edge methods
    def put_edge(self, edge_id: bytes, value: bytes):
        """Store a serialized edge by byte key."""
        with self.env.begin(write=True, db=self.edges_db) as txn:
            txn.put(edge_id, value)

    def get_edge(self, edge_id: str) -> bytes:
        """Return serialized edge bytes by key, or ``None``."""
        with self.env.begin(write=False, db=self.edges_db) as txn:
            return txn.get(edge_id)

    def delete_edge(self, edge_id: str):
        """Delete an edge by byte key."""
        with self.env.begin(write=True, db=self.edges_db) as txn:
            txn.delete(edge_id)

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]):
        """Write a batch of nodes in a single transaction."""
        with self.env.begin(write=True, db=self.nodes_db) as txn:
            for node_id, val in keys_and_values.items():
                txn.put(node_id, val)
    
    def get_nodes_bulk(self, node_ids: list[bytes]) -> dict[bytes, bytes]:
        """Retrieve multiple nodes in one read transaction."""
        results = {}
        with self.env.begin(write=False, db=self.nodes_db) as txn:
            with txn.cursor() as c:
            # for node_id in node_ids:
                data = c.getmulti(node_ids)
                if data is not None:
                    results.update({k : v for k, v in data})

        return results
    
    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]):
        """Store many serialized edges in one transaction."""
        with self.env.begin(write=True, db=self.edges_db) as txn:
            for edge_id, val in keys_and_values.items():
                txn.put(edge_id, val)
    
    def get_edges_bulk(self, edge_ids: list[bytes]) -> dict[bytes, bytes]:
        """Return serialized edges for the requested keys."""
        results = {}
        with self.env.begin(write=False, db=self.edges_db) as txn:
            for edge_id in edge_ids:
                data = txn.get(edge_id)
                if data is not None:
                    results[edge_id] = data
        return results

    def get_edge_keys_generator(self, num_edges = None, key_offset = None):
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
    
    def get_node_keys_generator(self, num_nodes = None, key_offset = None):
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

    # -------------------------------------------------------------------------
    # Bulk Write: Adjacency
    # -------------------------------------------------------------------------
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

    # ----- Typed Adjacency Methods -----
    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Store forward and reverse typed adjacency records."""
        with self.env.begin(write=True, db=self.typed_adj_db) as txn:
            txn.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
            txn.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]):
        """Store many typed adjacency records in one transaction."""
        with self.env.begin(write=True, db=self.typed_adj_db) as txn:
            for source_id, target_id, edge_type, edge_id in records:
                txn.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
                txn.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Delete forward and reverse typed adjacency records."""
        with self.env.begin(write=True, db=self.typed_adj_db) as txn:
            txn.delete(_typed_adjacency_key("out", source_id, edge_type, edge_id))
            txn.delete(_typed_adjacency_key("in", target_id, edge_type, edge_id))

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out"):
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

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Store one sorted index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.put(_index_key(index_name, key_parts, value), value)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]):
        """Store many sorted index entries in one transaction."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            for index_name, key_parts, value in entries:
                txn.put(_index_key(index_name, key_parts, value), value)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Delete one sorted index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.delete(_index_key(index_name, key_parts, value))

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]):
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

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Store one sorted range index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]):
        """Store many sorted range index entries in one transaction."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            for index_name, key_parts, range_value, value in entries:
                txn.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Delete one sorted range index entry."""
        with self.env.begin(write=True, db=self.index_db) as txn:
            txn.delete(_range_index_key(index_name, key_parts, range_value, value))

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True):
        """Yield values whose range index key falls between start and end values."""
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        with self.env.begin(write=False, db=self.index_db) as txn:
            cursor = txn.cursor()
            if not cursor.set_range(start_key):
                return
            for key, value in cursor:
                if not key.startswith(prefix):
                    break
                range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
                if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                    continue
                if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                    break
                yield value


# =========================================
# LevelDB Implementation
# =========================================

class LevelDBStore(KVStore):
    """LevelDB implementation backed by ``plyvel``.

    Args:
        path: Directory that will contain the LevelDB sub-databases.

    Examples:
        >>> store = LevelDBStore(path="/tmp/example_graph_leveldb")  # doctest: +SKIP
    """

    def __init__(self, path='graph_leveldb'):
        """Create or open a LevelDB store. We'll store nodes/edges by prefix."""
        try:
            import plyvel
        except ImportError as exc:
            raise _missing_dependency_error("plyvel", feature_name="LevelDBStore") from exc
        
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
    def get_db_path(self, db_string = 'nodes'):
        """Return the relative path for a named LevelDB database.

        Examples:
            >>> LevelDBStore.get_db_path.__name__
            'get_db_path'
        """
        return self.db_paths[db_string]
    
    def range_iter(self, start_key: bytes, end_key: bytes):
        """Yield records whose keys fall within a range.

        Note:
            This generic iterator is not used by the main graph APIs.
        """
        with self.db.iterator(start=start_key, stop=end_key) as it:
            for k, v in it:
                yield k, v

    def put_metadata(self, key: bytes, value: bytes):
        """Store a metadata key/value pair."""
        self.db_metadata.put(key, value)

    def get_metadata(self, key: bytes) -> bytes:
        """Return a metadata value by key, or ``None``."""
        return self.db_metadata.get(key)

    def delete_metadata(self, key: bytes):
        """Delete a metadata key/value pair."""
        self.db_metadata.delete(key)

    def get_db_iterator(self, which_db = 'nodes'):
        """Yield all records from a named sub-database."""
        with self.db_dict[which_db].iterator() as it:
            for k, v in it:
                yield k, v

    def get_node_keys_iterator(self):
        """Yield node database records."""
        return self.get_db_iterator(which_db='nodes')

    def get_node_keys_generator(self, num_nodes = None, key_offset = None):
        """Yield node keys from the node database."""
        yielded = 0
        with self.db_nodes.iterator(start=key_offset) as it:
            for k, _ in it:
                yield k
                yielded += 1
                if num_nodes is not None and yielded == num_nodes:
                    break

    def get_edge_keys_generator(self, num_edges = None, key_offset = None):
        """Yield edge keys from the edge database."""
        yielded = 0
        with self.db_edges.iterator(start=key_offset) as it:
            for k, _ in it:
                yield k
                yielded += 1
                if num_edges is not None and yielded == num_edges:
                    break


    # -- Specialized methods for nodes
    def put_node(self, node_id: bytes, value: bytes):
        """Store a serialized node by byte key."""
        self.db_nodes.put(node_id,value)

    def get_node(self, node_id: bytes) -> bytes:
        """Return serialized node bytes by key, or ``None``."""
        return self.db_nodes.get(node_id)

    def delete_node(self, node_id: bytes):
        """Delete a node by byte key."""
        self.db_nodes.delete(node_id)

    # -- Specialized methods for edges
    def put_edge(self, edge_id: bytes, value: bytes):
        """Store a serialized edge by byte key."""
        self.db_edges.put(edge_id, value)

    def get_edge(self, edge_id: bytes) -> bytes:
        """Return serialized edge bytes by key, or ``None``."""
        return self.db_edges.get(edge_id)

    def delete_edge(self, edge_id: str):
        """Delete an edge by byte key."""
        self.db_edges.delete(edge_id)

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]):
        """Use a WriteBatch for atomic bulk updates."""
        with self.db_nodes.write_batch() as wb:
            for node_id, val in keys_and_values.items():
                # s = node_id.decode()
                # wb.put(f"N:{s}".encode('utf-8'), val)
                wb.put(node_id, val)

    def get_nodes_bulk(self, node_ids: list[bytes]) -> dict[bytes, bytes]:
        """Return serialized nodes for the requested keys."""
        results = {}
        for node_id in node_ids:
            data = self.db_nodes.get(node_id)
            if data is not None:
                results[node_id] = data
        return results
    
    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]):
        """Store many serialized edges in one write batch."""
        with self.db_edges.write_batch() as wb:
            for edge_id, val in keys_and_values.items():
                wb.put(edge_id, val)
    
    def get_edges_bulk(self, edge_ids: list[bytes]) -> dict[bytes, bytes]:
        """Return serialized edges for the requested keys."""
        results = {}
        for edge_id in edge_ids:
            # s = edge_id.decode()
            # key = f"E:{s}".encode('utf-8')
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
    
    # -------------------------------------------------------------------------
    # Bulk Write: Adjacency
    # -------------------------------------------------------------------------
    def put_adjacency_bulk(self, adj_dict: Dict[str, bytes]) -> None:
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
            # s = node_id.decode()
            # key = f"A:{s}".encode('utf-8')
            data = self.db_adj.get(node_id)
            if data is not None:
                results[node_id] = data
        return results

    # ----- Typed Adjacency Methods -----
    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Store forward and reverse typed adjacency records."""
        with self.db_typed_adj.write_batch() as wb:
            wb.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
            wb.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]):
        """Store many typed adjacency records in one write batch."""
        with self.db_typed_adj.write_batch() as wb:
            for source_id, target_id, edge_type, edge_id in records:
                wb.put(_typed_adjacency_key("out", source_id, edge_type, edge_id), target_id)
                wb.put(_typed_adjacency_key("in", target_id, edge_type, edge_id), source_id)

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Delete forward and reverse typed adjacency records."""
        with self.db_typed_adj.write_batch() as wb:
            wb.delete(_typed_adjacency_key("out", source_id, edge_type, edge_id))
            wb.delete(_typed_adjacency_key("in", target_id, edge_type, edge_id))

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out"):
        """Yield typed adjacency ``(edge_id, neighbor_id)`` pairs."""
        prefix = _typed_adjacency_prefix(direction, node_id, edge_type)
        with self.db_typed_adj.iterator(prefix=prefix) as it:
            for key, neighbor_id in it:
                edge_id = key[len(prefix):]
                yield edge_id, neighbor_id

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Store one sorted index entry."""
        self.db_index.put(_index_key(index_name, key_parts, value), value)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]):
        """Store many sorted index entries in one write batch."""
        with self.db_index.write_batch() as wb:
            for index_name, key_parts, value in entries:
                wb.put(_index_key(index_name, key_parts, value), value)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Delete one sorted index entry."""
        self.db_index.delete(_index_key(index_name, key_parts, value))

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]):
        """Yield values whose index key starts with ``key_parts``."""
        prefix = _index_prefix(index_name, key_parts)
        with self.db_index.iterator(prefix=prefix) as it:
            for _, value in it:
                yield value

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Store one sorted range index entry."""
        self.db_index.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]):
        """Store many sorted range index entries in one write batch."""
        with self.db_index.write_batch() as wb:
            for index_name, key_parts, range_value, value in entries:
                wb.put(_range_index_key(index_name, key_parts, range_value, value), value)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Delete one sorted range index entry."""
        self.db_index.delete(_range_index_key(index_name, key_parts, range_value, value))

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True):
        """Yield values whose range index key falls between start and end values."""
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        with self.db_index.iterator(start=start_key) as it:
            for key, value in it:
                if not key.startswith(prefix):
                    break
                range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
                if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                    continue
                if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                    break
                yield value

    def close(self):
        """Close all LevelDB sub-databases."""
        self.db_metadata.close()
        self.db_index.close()
        self.db_typed_adj.close()
        self.db_adj.close()
        self.db_edges.close()
        self.db_nodes.close()


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
        >>> store = PyRexStore(path="/tmp/example_graph_rocksdb")  # doctest: +SKIP
    """

    _SEP = b"\x1f"

    def __init__(
        self,
        path="graph_rocksdb",
        parallelism=None,
        max_background_jobs=None,
        write_buffer_size=None,
        bloom_bits_per_key=None,
        disable_wal=False,
    ):
        """Open a PyRex/RocksDB store with optional tuning settings."""
        try:
            import pyrex
        except ImportError as exc:
            raise _missing_dependency_error("pyrex", install_name="pyrex-rocksdb", feature_name="PyRexStore") from exc

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
        self.db = pyrex.PyRocksDB(path, options)
        self.write_options = pyrex.WriteOptions()
        self.write_options.disable_wal = disable_wal

    def _key(self, prefix: bytes, key: bytes) -> bytes:
        """Build a prefixed RocksDB key."""
        return prefix + self._SEP + key

    def _typed_key(self, direction: str, node_id: bytes, edge_type: str, edge_id: bytes = b"") -> bytes:
        """Build a typed adjacency key in the shared RocksDB keyspace."""
        return self._SEP.join([b"T", direction.encode("utf-8"), node_id, edge_type.encode("utf-8"), edge_id])

    def has_native_columnar_ingestion(self) -> bool:
        """Return whether this PyRex runtime exposes native columnar writes."""
        return hasattr(self.db, "write_columnar_batch")

    def _write_columnar_batch(self, keys: list[bytes], values: list[bytes]) -> None:
        """Write key/value lists through PyRex's native columnar API."""
        self.db.write_columnar_batch(keys, values, write_options=self.write_options)

    def _iter_prefixed_keys(self, prefix: bytes, key_offset=None, limit=None):
        """Yield unprefixed keys for a prefixed key range."""
        yielded = 0
        start_key = prefix if key_offset is None else prefix + key_offset
        iterator = self.db.new_iterator()
        iterator.seek(start_key)
        while iterator.valid():
            key = iterator.key()
            if not key.startswith(prefix):
                break
            yield key[len(prefix):]
            yielded += 1
            if limit is not None and yielded == limit:
                break
            iterator.next()
        iterator.check_status()

    def put(self, key: bytes, value: bytes):
        """Store a raw key/value pair in the shared RocksDB keyspace."""
        self.db.put(key, value, self.write_options)

    def get(self, key: bytes) -> bytes:
        """Return a raw value by key."""
        return self.db.get(key)

    def delete(self, key: bytes):
        """Delete a raw key/value pair."""
        self.db.delete(key, self.write_options)

    def range_iter(self, start_key: bytes, end_key: bytes):
        """Yield raw records whose keys fall within an inclusive range."""
        iterator = self.db.new_iterator()
        iterator.seek(start_key)
        while iterator.valid():
            key = iterator.key()
            if key > end_key:
                break
            yield key, iterator.value()
            iterator.next()
        iterator.check_status()

    def close(self):
        """Close the RocksDB database."""
        self.db.close()

    def put_metadata(self, key: bytes, value: bytes):
        """Store a metadata key/value pair."""
        self.db.put(self._key(b"M", key), value, self.write_options)

    def get_metadata(self, key: bytes) -> bytes:
        """Return a metadata value by key, or ``None``."""
        return self.db.get(self._key(b"M", key))

    def delete_metadata(self, key: bytes):
        """Delete a metadata key/value pair."""
        self.db.delete(self._key(b"M", key), self.write_options)

    def put_node(self, node_id: bytes, value: bytes):
        """Store a serialized node by byte key."""
        self.db.put(self._key(b"N", node_id), value, self.write_options)

    def get_node(self, node_id: bytes) -> bytes:
        """Return serialized node bytes by key, or ``None``."""
        return self.db.get(self._key(b"N", node_id))

    def delete_node(self, node_id: bytes):
        """Delete a node by byte key."""
        self.db.delete(self._key(b"N", node_id), self.write_options)

    def put_nodes_bulk(self, keys_and_values: dict[bytes, bytes]):
        """Store many serialized nodes in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for node_id, value in keys_and_values.items():
            batch.put(self._key(b"N", node_id), value)
        self.db.write(batch, self.write_options)

    def ingest_nodes_columnar(self, node_list, *, native: bool = True):
        """Store columnar nodes, using native PyRex ingestion when available."""
        if native and self.has_native_columnar_ingestion():
            keys = [self._key(b"N", node_id) for node_id in node_list.node_ids]
            values = node_list.node_values_column if node_list.node_values_column is not None else node_list.node_values
            self._write_columnar_batch(keys, values)
            return
        self.put_nodes_bulk(dict(zip(node_list.node_ids, node_list.node_values)))

    def get_nodes_bulk(self, node_ids: list[bytes]) -> dict[bytes, bytes]:
        """Return serialized nodes for the requested keys."""
        results = {}
        for node_id in node_ids:
            data = self.get_node(node_id)
            if data is not None:
                results[node_id] = data
        return results

    def put_edge(self, edge_id: bytes, value: bytes):
        """Store a serialized edge by byte key."""
        self.db.put(self._key(b"E", edge_id), value, self.write_options)

    def get_edge(self, edge_id: bytes) -> bytes:
        """Return serialized edge bytes by key, or ``None``."""
        return self.db.get(self._key(b"E", edge_id))

    def delete_edge(self, edge_id: bytes):
        """Delete an edge by byte key."""
        self.db.delete(self._key(b"E", edge_id), self.write_options)

    def put_edges_bulk(self, keys_and_values: dict[bytes, bytes]):
        """Store many serialized edges in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for edge_id, value in keys_and_values.items():
            batch.put(self._key(b"E", edge_id), value)
        self.db.write(batch, self.write_options)

    def get_edges_bulk(self, edge_ids: list[bytes]) -> dict[bytes, bytes]:
        """Return serialized edges for the requested keys."""
        results = {}
        for edge_id in edge_ids:
            data = self.get_edge(edge_id)
            if data is not None:
                results[edge_id] = data
        return results

    def get_edge_keys_generator(self, num_edges=None, key_offset=None):
        """Yield edge keys from the shared RocksDB keyspace."""
        yield from self._iter_prefixed_keys(b"E" + self._SEP, key_offset=key_offset, limit=num_edges)

    def get_node_keys_generator(self, num_nodes=None, key_offset=None):
        """Yield node keys from the shared RocksDB keyspace."""
        yield from self._iter_prefixed_keys(b"N" + self._SEP, key_offset=key_offset, limit=num_nodes)

    def put_adjacency(self, node_id: bytes, value: bytes) -> None:
        """Store a serialized adjacency list for a node."""
        self.db.put(self._key(b"A", node_id), value, self.write_options)

    def get_adjacency(self, node_id: bytes) -> Optional[bytes]:
        """Return a serialized adjacency list for a node."""
        return self.db.get(self._key(b"A", node_id))

    def put_adjacency_bulk(self, adj_dict: Dict[bytes, bytes]) -> None:
        """Store many serialized adjacency lists in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for node_id, value in adj_dict.items():
            batch.put(self._key(b"A", node_id), value)
        self.db.write(batch, self.write_options)

    def get_adjacency_bulk(self, node_ids: List[bytes]) -> Dict[bytes, bytes]:
        """Return serialized adjacency lists for the requested nodes."""
        results = {}
        for node_id in node_ids:
            data = self.get_adjacency(node_id)
            if data is not None:
                results[node_id] = data
        return results

    def put_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Store forward and reverse typed adjacency records."""
        self.put_typed_adjacency_bulk([(source_id, target_id, edge_type, edge_id)])

    def put_typed_adjacency_bulk(self, records: list[tuple[bytes, bytes, str, bytes]]):
        """Store many typed adjacency records in one RocksDB write batch."""
        batch = self._pyrex.PyWriteBatch()
        for source_id, target_id, edge_type, edge_id in records:
            batch.put(self._typed_key("out", source_id, edge_type, edge_id), target_id)
            batch.put(self._typed_key("in", target_id, edge_type, edge_id), source_id)
        self.db.write(batch, self.write_options)

    def ingest_edges_columnar(self, edge_list, *, append_only: bool = True, native: bool = True):
        """Store columnar typed edges, using native PyRex ingestion when available."""
        if not append_only:
            raise NotImplementedError("native columnar edge ingestion currently requires append_only=True")
        if native and self.has_native_columnar_ingestion():
            edge_keys = [self._key(b"E", edge_id) for edge_id in edge_list.edge_ids]
            out_keys = [
                self._typed_key("out", source_id, edge_type, edge_id)
                for source_id, edge_type, edge_id in zip(edge_list.sources, edge_list.edge_types, edge_list.edge_ids)
            ]
            in_keys = [
                self._typed_key("in", target_id, edge_type, edge_id)
                for target_id, edge_type, edge_id in zip(edge_list.targets, edge_list.edge_types, edge_list.edge_ids)
            ]
            edge_values = edge_list.edge_values_column if edge_list.edge_values_column is not None else edge_list.edge_values
            targets = edge_list.targets_column if edge_list.targets_column is not None else edge_list.targets
            sources = edge_list.sources_column if edge_list.sources_column is not None else edge_list.sources
            self._write_columnar_batch(edge_keys, edge_values)
            self._write_columnar_batch(out_keys, targets)
            self._write_columnar_batch(in_keys, sources)
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
        self.put_index_entries_bulk([
            ("edge_type", [edge_type.encode("utf-8")], edge_id)
            for edge_type, edge_id in zip(edge_list.edge_types, edge_list.edge_ids)
        ])

    def delete_typed_adjacency(self, source_id: bytes, target_id: bytes, edge_type: str, edge_id: bytes):
        """Delete forward and reverse typed adjacency records."""
        batch = self._pyrex.PyWriteBatch()
        batch.delete(self._typed_key("out", source_id, edge_type, edge_id))
        batch.delete(self._typed_key("in", target_id, edge_type, edge_id))
        self.db.write(batch, self.write_options)

    def iter_typed_adjacency(self, node_id: bytes, edge_type: str, direction: str = "out"):
        """Yield typed adjacency ``(edge_id, neighbor_id)`` pairs."""
        prefix = self._typed_key(direction, node_id, edge_type)
        iterator = self.db.new_iterator()
        iterator.seek(prefix)
        while iterator.valid():
            key = iterator.key()
            if not key.startswith(prefix):
                break
            yield key[len(prefix):], iterator.value()
            iterator.next()
        iterator.check_status()

    def put_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Store one sorted index entry."""
        self.db.put(_index_key(index_name, key_parts, value), value, self.write_options)

    def put_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes]]):
        """Store many sorted index entries in one write batch."""
        batch = self._pyrex.PyWriteBatch()
        for index_name, key_parts, value in entries:
            batch.put(_index_key(index_name, key_parts, value), value)
        self.db.write(batch, self.write_options)

    def delete_index_entry(self, index_name: str, key_parts: list[bytes], value: bytes):
        """Delete one sorted index entry."""
        self.db.delete(_index_key(index_name, key_parts, value), self.write_options)

    def iter_index_prefix(self, index_name: str, key_parts: list[bytes]):
        """Yield values whose index key starts with ``key_parts``."""
        prefix = _index_prefix(index_name, key_parts)
        iterator = self.db.new_iterator()
        iterator.seek(prefix)
        while iterator.valid():
            key = iterator.key()
            if not key.startswith(prefix):
                break
            yield iterator.value()
            iterator.next()
        iterator.check_status()

    def put_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Store one sorted range index entry."""
        self.db.put(_range_index_key(index_name, key_parts, range_value, value), value, self.write_options)

    def put_range_index_entries_bulk(self, entries: list[tuple[str, list[bytes], bytes, bytes]]):
        """Store many sorted range index entries in one write batch."""
        batch = self._pyrex.PyWriteBatch()
        for index_name, key_parts, range_value, value in entries:
            batch.put(_range_index_key(index_name, key_parts, range_value, value), value)
        self.db.write(batch, self.write_options)

    def delete_range_index_entry(self, index_name: str, key_parts: list[bytes], range_value: bytes, value: bytes):
        """Delete one sorted range index entry."""
        self.db.delete(_range_index_key(index_name, key_parts, range_value, value), self.write_options)

    def iter_range_index(self, index_name: str, key_parts: list[bytes], start_value: bytes | None = None, end_value: bytes | None = None, include_start: bool = True, include_end: bool = True):
        """Yield values whose range index key falls between start and end values."""
        prefix = _range_index_prefix(index_name, key_parts)
        start_key = prefix if start_value is None else prefix + start_value
        iterator = self.db.new_iterator()
        iterator.seek(start_key)
        while iterator.valid():
            key = iterator.key()
            if not key.startswith(prefix):
                break
            range_value = key[len(prefix):].split(_TYPED_ADJ_SEP, 1)[0]
            if start_value is not None and (range_value < start_value or (range_value == start_value and not include_start)):
                iterator.next()
                continue
            if end_value is not None and (range_value > end_value or (range_value == end_value and not include_end)):
                break
            yield iterator.value()
            iterator.next()
        iterator.check_status()

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
