Choosing a backend for the labs
================================

Standalone ontology and Datalog examples require no storage backend at all.
The stored-graph examples use one of three optional backends: LMDB, LevelDB,
or RocksDB through PyRex. Choose one tab and install its extra in the environment
running your code. Each tab then gives a complete, self-contained example that
writes two nodes, queries them, and closes a temporary database. From this
tutorial's repository checkout, use the ``uv sync`` command shown; installed
library users can use the corresponding ``pip install`` command instead.

.. tab-set::

   .. tab-item:: LMDB
      :sync: lmdb

      .. code-block:: console

         uv sync --extra lmdb
         # Installed-library alternative: python -m pip install "gestaltdb[lmdb]"

      .. code-block:: python

         from tempfile import TemporaryDirectory
         from gestaltdb.graphdb import Edge, GraphDB, Node

         with TemporaryDirectory() as directory:
             graph = GraphDB.create(directory, backend="lmdb", serializer="json")
             try:
                 graph.put_nodes([Node("alice", labels=["Employee"]), Node("acme")])
                 graph.put_edge(Edge("job", "alice", "acme", {"type": "worksFor"}))
                 rows = graph.query(
                     "MATCH (a:Employee)-[:worksFor]->(b) RETURN a.id AS employee"
                 ).records
                 assert rows == [{"employee": "alice"}]
             finally:
                 graph.close()

   .. tab-item:: LevelDB
      :sync: leveldb

      .. code-block:: console

         uv sync --extra leveldb
         # Installed-library alternative: python -m pip install "gestaltdb[leveldb]"

      .. code-block:: python

         from tempfile import TemporaryDirectory
         from gestaltdb.graphdb import Edge, GraphDB, Node

         with TemporaryDirectory() as directory:
             graph = GraphDB.create(directory, backend="leveldb", serializer="json")
             try:
                 graph.put_nodes([Node("alice", labels=["Employee"]), Node("acme")])
                 graph.put_edge(Edge("job", "alice", "acme", {"type": "worksFor"}))
                 rows = graph.query(
                     "MATCH (a:Employee)-[:worksFor]->(b) RETURN a.id AS employee"
                 ).records
                 assert rows == [{"employee": "alice"}]
             finally:
                 graph.close()

   .. tab-item:: RocksDB / PyRex
      :sync: pyrex

      .. code-block:: console

         uv sync --extra rocksdb
         # Installed-library alternative: python -m pip install "gestaltdb[rocksdb]"

      .. code-block:: python

         from tempfile import TemporaryDirectory
         from gestaltdb.graphdb import Edge, GraphDB, Node

         with TemporaryDirectory() as directory:
             graph = GraphDB.create(directory, backend="pyrex", serializer="json")
             try:
                 graph.put_nodes([Node("alice", labels=["Employee"]), Node("acme")])
                 graph.put_edge(Edge("job", "alice", "acme", {"type": "worksFor"}))
                 rows = graph.query(
                     "MATCH (a:Employee)-[:worksFor]->(b) RETURN a.id AS employee"
                 ).records
                 assert rows == [{"employee": "alice"}]
             finally:
                 graph.close()

The install extra is called ``rocksdb``, but the registry name in Python is
``pyrex``. Each backend supports the same basic graph writes, ordinary Cypher,
indexes, and temporal-claim API. The examples deliberately use separate
temporary directories, so switching tabs never reuses another backend's files.

Where a backend cannot substitute
----------------------------------

Backend selection does not make consistency guarantees interchangeable.
:doc:`ch01-property-graph-vs-semantic` works with any of the three choices.
:doc:`ch04-storage-grounding` and notebook 3 deliberately use LMDB because
they require an authenticated mutable read snapshot:

.. list-table::
   :header-rows: 1

   * - Backend
     - Basic graph lab
     - Verifiable current-graph extraction
   * - LMDB
     - Supported
     - Supported
   * - LevelDB
     - Supported
     - Unsupported: separate physical databases cannot be pinned atomically
   * - Default PyRex
     - Supported
     - Unsupported: no current snapshot handle
   * - Transactional PyRex
     - Supported
     - Runtime-dependent: requires a verifiable transaction sequence

Do not disable verification merely to make chapter 4 run. Use LMDB for that
lab, or supply a transactional PyRex runtime that supports the exact guarantee.
Temporal ``read_view(valid_time=...)`` reads immutable version history instead
of mutable records and is supported on all three backends. Native columnar
ingestion and graph-level transactions also differ; see the
`library backend guide <https://mylonasc.github.io/gestaltdb/storage-backends.html>`_
for those capabilities and installation troubleshooting.
