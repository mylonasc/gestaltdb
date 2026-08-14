Storage Backends
================

PyGraphDB separates graph logic from storage. ``GraphDB`` receives a key-value
store instance and a serializer instance.

LMDB Backend
------------

Use ``LMDBStore`` for a mature embedded backend with named sub-databases.

.. code-block:: python

   from pygraphdb.graphdb import GraphDB
   from pygraphdb.kvstores import LMDBStore
   from pygraphdb.serializers import PickleSerializer

   store = LMDBStore(path="graph_lmdb", map_size=2**30)
   graph_db = GraphDB(store, PickleSerializer())

LMDB keeps separate databases for nodes, edges, adjacency, typed adjacency, and
sorted indexes. Increase ``map_size`` when loading large graphs.

LevelDB Backend
---------------

Use ``LevelDBStore`` when you want LevelDB through ``plyvel``.

.. code-block:: python

   from pygraphdb.graphdb import GraphDB
   from pygraphdb.kvstores import LevelDBStore
   from pygraphdb.serializers import PickleSerializer

   store = LevelDBStore(path="graph_leveldb")
   graph_db = GraphDB(store, PickleSerializer())

``plyvel`` requires compatible CPython wheels or local LevelDB build tooling. If
installation fails on Python 3.14 or a free-threaded interpreter, create a Python
3.12 environment and install ``pygraphdb[leveldb]`` there.

RocksDB Backend
---------------

Use ``PyRexStore`` for RocksDB through the optional ``pyrex-rocksdb`` package.
This backend uses one physical RocksDB database with prefixed keys and exposes
several RocksDB tuning knobs.

.. code-block:: python

   from pygraphdb.graphdb import GraphDB
   from pygraphdb.kvstores import PyRexStore
   from pygraphdb.serializers import PickleSerializer

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

When installed with ``pyrex-rocksdb>=0.3.0a0``, ``PyRexStore`` can use PyRex's
native ``write_columnar_batch`` API through ``GraphDB.ingest_nodes_arrow`` and
``GraphDB.ingest_edges_arrow``. Serialized ``node_value`` and ``edge_value``
columns backed by Arrow binary arrays are passed directly to PyRex's native
batch writer where possible. The native path still constructs RocksDB keys in
PyGraphDB, but avoids Python value materialization for Arrow-backed value
columns.

For JSON-compatible payloads, ``GraphDB.ingest_nodes_polars_entities`` and
``GraphDB.ingest_edges_polars_entities`` can build serialized payload columns
from structured Polars columns before using the same columnar write path:

.. code-block:: python

   import polars as pl

   from pygraphdb.graphdb import GraphDB
   from pygraphdb.kvstores import PyRexStore
   from pygraphdb.serializers import JSONSerializer

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

Backend Index Interface
~~~~~~~~~~~~~~~~~~~~~~~

Backend implementations expose lower-level sorted index methods such as
``put_index_entry``, ``delete_index_entry``, ``iter_index_prefix``, and range
index equivalents. Most users should prefer the ``GraphDB`` helpers above.

Backend Selection Pattern
-------------------------

.. code-block:: python

   from pathlib import Path

   from pygraphdb.graphdb import GraphDB
   from pygraphdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
   from pygraphdb.serializers import PickleSerializer

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
