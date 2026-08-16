Storage Backends
================

GestaltDB separates graph logic from storage. ``GraphDB`` receives a key-value
store instance and a serializer instance.

Backend Selection Summary
-------------------------

The current backend trade-offs are:

.. list-table::
   :header-rows: 1

   * - Backend
     - Best fit
     - Main trade-offs
   * - ``LMDBStore``
     - Mature embedded storage
     - Requires choosing a sufficient ``map_size``
   * - ``LevelDBStore``
     - Simple local graphs
     - No native Arrow/Polars columnar fast path
   * - ``PyRexStore``
     - Large writes and ingestion
     - Optional dependency, RocksDB tuning knobs, opt-in transactions

Recommendations for the library as-is:

- Start with ``LevelDBStore`` for small applications, examples, and local testing
  when you do not need RocksDB's native columnar ingestion path.
- Use ``PyRexStore`` for bulk loads and append-heavy workloads, especially with
  ``GraphDB.ingest_arrow`` or ``GraphDB.ingest_polars``.
- Use ``LMDBStore`` when LMDB's storage model is desirable and the database size
  is predictable enough to configure ``map_size`` safely.
- Keep ``disable_wal=False`` for durable RocksDB writes. Consider
  ``disable_wal=True`` only for disposable benchmark runs or reloadable bulk-load
  experiments.
- Benchmark with your actual serializer, index definitions, and graph shape.
  Backend differences can be hidden by Python object creation, JSON
  serialization, or index maintenance.

LMDB Backend
------------

Use ``LMDBStore`` for a mature embedded backend with named sub-databases.

.. code-block:: python

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import LMDBStore
   from gestaltdb.serializers import PickleSerializer

   store = LMDBStore(path="graph_lmdb", map_size=2**30)
   graph_db = GraphDB(store, PickleSerializer())

LMDB keeps separate databases for nodes, edges, adjacency, typed adjacency, and
sorted indexes. Increase ``map_size`` when loading large graphs.

LMDB is a good fit when you want a mature embedded backend and can provision the
maximum database size ahead of time. It is not currently the optimized backend
for native Arrow/Polars columnar ingestion; those paths are designed around
``PyRexStore`` when PyRex exposes ``write_columnar_batch``.

LevelDB Backend
---------------

Use ``LevelDBStore`` when you want LevelDB through ``plyvel``.

.. code-block:: python

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import LevelDBStore
   from gestaltdb.serializers import PickleSerializer

   store = LevelDBStore(path="graph_leveldb")
   graph_db = GraphDB(store, PickleSerializer())

``plyvel`` requires compatible CPython wheels or local LevelDB build tooling. If
installation fails on Python 3.14 or a free-threaded interpreter, create a Python
3.12 environment and install ``gestaltdb[leveldb]`` there.

LevelDB is the pragmatic default for modest local workloads. It supports the same
``GraphDB`` API and index semantics, but columnar ingestion falls back to Python
bulk writes rather than RocksDB's native columnar batch writer. In local
benchmarks, LevelDB can be close to RocksDB when serialization dominates, but it
does not get the same write-phase advantage on larger native columnar loads.

RocksDB Backend
---------------

Use ``PyRexStore`` for RocksDB through the optional ``pyrex-rocksdb`` package.
This backend uses one physical RocksDB database with prefixed keys and exposes
several RocksDB tuning knobs.

.. code-block:: python

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import PyRexStore
   from gestaltdb.serializers import PickleSerializer

   store = PyRexStore(
       path="graph_rocksdb",
       parallelism=4,
       max_background_jobs=4,
       write_buffer_size=64 * 1024 * 1024,
       bloom_bits_per_key=10,
   )
   graph_db = GraphDB(store, PickleSerializer())

``disable_wal=True`` can be useful for bulk-loading experiments, but it weakens
durability and should not be used as a safe default.

When installed with ``pyrex-rocksdb>=0.4.1``, ``PyRexStore`` can use PyRex's
native ``write_columnar_batch`` API through ``GraphDB.ingest_nodes_arrow`` and
``GraphDB.ingest_edges_arrow``. Serialized ``node_value`` and ``edge_value``
columns backed by Arrow binary arrays are passed directly to PyRex's native
batch writer where possible. The native path still constructs RocksDB keys in
GestaltDB, but avoids Python value materialization for Arrow-backed value
columns.

For JSON-compatible payloads, ``GraphDB.ingest_nodes_polars_entities`` and
``GraphDB.ingest_edges_polars_entities`` can build serialized payload columns
from structured Polars columns before using the same columnar write path:

.. code-block:: python

   import polars as pl

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import PyRexStore
   from gestaltdb.serializers import JSONSerializer

   graph_db = GraphDB(PyRexStore(path="graph_rocksdb"), JSONSerializer())
   graph_db.ingest_nodes_polars_entities(
       pl.DataFrame({
           "node_id": ["n1"],
           "labels": [["Entity"]],
           "kind": ["drug"],
       }),
       property_columns=["kind"],
   )

Other serializers use the same entity methods as a convenience API, but fall
back to Python row-wise serialization. Columnar edge ingestion remains
append-only.

RocksDB is the backend to choose when ingestion throughput matters. A targeted
100,000-node and 500,000-edge local benchmark using JSON payloads showed RocksDB
with native columnar ingestion at 3.400 seconds total versus 3.948 seconds for
LevelDB on the same Python JSON serialization path, a 1.16x total speedup and a
1.23x write-phase speedup. The same run showed larger gains from avoiding Python
JSON serialization with Polars, so pair RocksDB with Polars/Arrow entity-column
ingestion when your input data is already tabular.

Indexes
-------

All backends implement sorted index primitives used by labels, relationship type
catalogs, property lookups, and range scans. The high-level indexes maintained by
``GraphDB`` are:

- label indexes for ``Node.labels`` and ``GraphDB.nodes_by_label``
- relationship type indexes for ``edge.properties["type"]`` and ``GraphDB.edges_by_type``
- explicit node and edge property indexes
- composite label/property and type/property indexes
- scalar range indexes for indexed string and numeric properties

Property indexes are intentionally explicit. Register them only for predicates
you expect to use frequently:

.. code-block:: python

   graph_db.create_node_property_index("name")
   graph_db.create_edge_property_index("score")

   graph_db.nodes_by_property("name", "Aspirin")
   graph_db.edges_by_property_range("score", 0.8, None)

Cypher uses these indexes when possible for label/property scans and typed
relationship predicates. Index definitions are persisted in backend metadata, so
reopened databases continue maintaining the configured property indexes.

Columnar ingestion keeps label, relationship type, property, composite, and range
indexes current for configured indexed properties.

Columnar ingestion also accepts ``index_mode`` to control when secondary indexes
are updated:

``IndexMaintenanceMode.MAINTAIN``
   Updates label, type, property, composite, and range indexes during ingestion.
   Use this for incremental writes or when index-backed queries must be valid as
   soon as ingestion returns.

``IndexMaintenanceMode.DEFER``
   Writes canonical node and edge records plus traversal-critical typed adjacency
   records, marks affected secondary indexes stale, and requires an explicit
   ``GraphDB.rebuild_deferred_indexes()`` before indexed queries. Use this when
   the write window is more important than immediate index readiness.

``IndexMaintenanceMode.DEFER_REBUILD``
   Defers secondary-index writes during ingestion and immediately rebuilds stale
   indexes before returning. This is the high-level default for
   ``GraphDB.ingest_arrow`` and ``GraphDB.ingest_polars``.

On a targeted 100,000-node and 500,000-edge local benchmark with node property
indexes on ``kind`` and ``group`` plus an edge property index on ``weight``,
deferred mode reduced the write phase from 12.269 seconds to 1.407 seconds
(``8.72x`` faster). The immediate rebuild took 12.973 seconds, so total
ingest-plus-rebuild time was 14.380 seconds, which was 17.2% slower than inline
maintenance for that subset. Use deferred indexing to move index work out of the
critical write path; do not assume it always reduces total wall-clock time when a
full rebuild runs immediately.

Backend Index Interface
~~~~~~~~~~~~~~~~~~~~~~~

Backend implementations expose lower-level sorted index methods such as
``put_index_entry``, ``delete_index_entry``, ``iter_index_prefix``, and range
index equivalents. Most users should prefer the ``GraphDB`` helpers above.

Transactions
------------

GestaltDB exposes optional graph-level transactions for backends that can commit
all graph records and indexes atomically:

.. code-block:: python

   with graph_db.transaction() as tx:
       tx.put_node(Node(node_id="n1"))
       tx.put_edge(Edge(edge_id="e1", source="n1", target="n2", properties={"type": "rel"}))

The context commits on clean exit and rolls back if an exception leaves the
block. ``LMDBStore`` supports transactions directly because LMDB transactions
span all named sub-databases in the environment.

``PyRexStore`` supports transactions when opened in transaction-capable mode with
``pyrex-rocksdb>=0.4.1``:

.. code-block:: python

   store = PyRexStore(path="graph_rocksdb", transactional=True)
   graph_db = GraphDB(store, PickleSerializer())

The default ``PyRexStore`` mode remains the fastest non-transactional path and
keeps native columnar ingestion available. Transactional PyRex writes still use
RocksDB ``PyWriteBatch`` internally for bulk methods, but the current PyRex
``TransactionDB`` API does not expose the native columnar writer.

A focused local benchmark on this branch used 100,000 nodes, 500,000 edges,
Pickle payloads, chunk size 10,000, four RocksDB background threads, a 64 MiB
write buffer, and Bloom filters. The transaction-capable RocksDB backend was
opened with ``PyRexStore(transactional=True)`` and used the same public ingestion
API, not one giant user transaction.

=========================================== ========== ========== =================
RocksDB backend                             Node write Edge write Native columnar
=========================================== ========== ========== =================
default, Arrow payloads                     0.457 s    1.886 s    yes
transaction-capable, Arrow payloads         0.540 s    3.623 s    no
default, Polars payloads                    0.429 s    1.828 s    yes
transaction-capable, Polars payloads        0.545 s    3.670 s    no
=========================================== ========== ========== =================

Use transaction-capable RocksDB when graph-level atomicity is required. Keep the
default RocksDB backend for append-only bulk loads where native columnar
ingestion is the priority.

The current ``LevelDBStore`` layout uses multiple physical LevelDB databases, so
graph-level transactions are intentionally unsupported there.

Backend Selection Pattern
-------------------------

.. code-block:: python

   from pathlib import Path

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
   from gestaltdb.serializers import PickleSerializer

   def open_graph(path: str, backend: str = "lmdb") -> GraphDB:
       Path(path).parent.mkdir(parents=True, exist_ok=True)
       if backend == "lmdb":
           store = LMDBStore(path=path, map_size=2**30)
       elif backend == "leveldb":
           store = LevelDBStore(path=path)
       elif backend == "rocksdb":
           store = PyRexStore(path=path)
       else:
           raise ValueError(f"unknown backend: {backend}")
       return GraphDB(store, PickleSerializer())

Cleanup
-------

Always close stores when a script or notebook cell is finished with them.

.. code-block:: python

   graph_db = GraphDB(LMDBStore(path="example_lmdb"), PickleSerializer())
   try:
       graph_db.put_node(Node(node_id="n1"))
   finally:
       graph_db.close()
