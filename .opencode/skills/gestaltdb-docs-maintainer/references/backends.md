# Storage Backends & Store Lifecycle

**When to read:** Read this file when you are adding, modifying, or debugging key-value storage backends, persistence options, transactional behavior, or self-describing store manifests (`GraphDB.create` / `GraphDB.open`).

---

## 1. Storage Backend Architecture

All GestaltDB storage backends implement the `KVStore` interface from `gestaltdb.kvstores`.

Key design characteristics:
- Keys and values are raw bytes (`bytes`).
- Backends expose specialized graph methods such as `put_node`, `get_node`, `put_edge`, `get_edge`, `iter_typed_adjacency`, and `iter_index_prefix`.
- Bulk updates are exposed through helpers such as `put_nodes_bulk`, `put_edges_bulk`, `put_typed_adjacency_bulk`, and `put_index_entries_bulk`.
- Logical storage areas are node records, edge records, legacy adjacency, typed adjacency, secondary indexes, and metadata. Concrete key prefixes and physical databases differ by backend.

---

## 2. Available Backends

### LevelDBStore
- **Import:** `from gestaltdb.kvstores import LevelDBStore`
- **Registry name:** `"leveldb"`
- **Engine:** Plyvel / LevelDB with Snappy compression.
- **Best for:** Local development, small to medium graphs, CI testing.
- **Parameters:**
  - `path`: Directory path for LevelDB storage.

### LMDBStore
- **Import:** `from gestaltdb.kvstores import LMDBStore`
- **Registry name:** `"lmdb"`
- **Engine:** Lightning Memory-Mapped Database (LMDB).
- **Best for:** Read-heavy workloads, multi-process readers, memory-mapped reads.
- **Parameters:**
  - `path`: Directory path for LMDB.
  - `map_size`: Maximum virtual memory size in bytes (e.g. `2**30` for 1GB). Must be specified adequately up front.
  - `map_id`: Compatibility option for mapped IDs.
  - `map_keys`: Enables extra key mapping databases.

### PyRexStore
- **Import:** `from gestaltdb.kvstores import PyRexStore`
- **Registry name:** `"pyrex"`
- **Engine:** RocksDB via pyrex C-extensions.
- **Best for:** Bulk data ingestion, high-throughput writes, large production datasets.
- **Parameters:**
  - `path`: Directory path for RocksDB storage.
  - `parallelism`, `max_background_jobs`, `write_buffer_size`, `bloom_bits_per_key`, `disable_wal`: Optional RocksDB tuning settings.
  - `transactional`: Boolean (default `False`). When `False`, uses high-speed direct batch writers and enables native columnar ingestion. When `True`, wraps database in `PyRexTransactionStore` for multi-operation ACID atomicity.
  - `transaction_db_options`: Optional PyRex transaction DB options used only with `transactional=True`.

---

## 3. Self-Describing Store Lifecycle (Manifests)

GestaltDB supports self-describing database directories using `gestaltdb_manifest.json`.

- `GraphDB.create(path, backend=..., serializer=...)`:
  Creates the store directory, records backend configuration, serializer configuration, and indexed property metadata in `gestaltdb_manifest.json`, and returns an open `GraphDB` instance.
- `GraphDB.open(path, backend_options=...)`:
  Reads `gestaltdb_manifest.json`, restores backend, serializer, and property index configurations automatically.
- Managed and migrated stores persist an immutable `database_id`; it remains stable when the directory moves and anchors `ReadViewProvenance` verification.
- `GraphDB.read_view(valid_time=...)` pins immutable temporal reads to one visible commit on every backend.
- `GraphDB.current_read_view()` pins mutable records and indexes. LMDB provides verifiable MVCC provenance; transactional PyRex pins reads when its runtime supports transaction snapshots; LevelDB and default PyRex reject explicit requests.
- Read-only Cypher and non-temporal sampler builds use snapshots automatically where truthful. Pass `read_snapshot=True` to require the guarantee or `False` to retain an unpinned legacy read.

---

## 4. Runnable Examples

### Creating and Reopening a Manifest-Backed Graph
```python
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node

with TemporaryDirectory() as tmpdir:
    store_path = f"{tmpdir}/graph_db"

    # Create self-describing database
    graph = GraphDB.create(
        store_path,
        backend="leveldb",
        serializer="json",
        indexed_node_properties=["kind"],
    )
    try:
        graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"kind": "small_molecule"}))
        graph.put_node(Node(node_id="protein-1", labels=["Protein"], properties={"kind": "receptor"}))
        graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "targets"}))
    finally:
        graph.close()

    # Reopen using manifest
    reopened = GraphDB.open(store_path)
    try:
        node = reopened.get_node(b"drug-1")
        print(f"Loaded: {node.get_id} -> {node.properties['kind']}")
        print(f"Backend: {reopened.manifest['backend']['name']}")
    finally:
        reopened.close()
```

### Direct Instantiation with PyRexStore (RocksDB)
```python
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import PyRexStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    store = PyRexStore(path=f"{tmpdir}/rocks_db", transactional=False)
    graph = GraphDB(store, JSONSerializer())
    try:
        graph.put_node(Node(node_id="user-1", properties={"tier": "premium"}))
        print("Successfully wrote to PyRexStore")
    finally:
        graph.close()
```
