Storage Backends
================

Choose two independent components: a **backend** persists keys and bytes; a
**serializer** encodes property payloads. Changing either choice for a new
database does not convert an existing directory. Use :doc:`property-graph` for
the entity model and :doc:`serializers` for value types and codec trade-offs.

Backend Selection Summary
-------------------------

.. Graphics debt VIS-01 (#495): independent backend/codec selection diagram;
   retain the capability matrix and text as its accessible static fallback.

.. list-table:: Install only what the application needs
   :header-rows: 1
   :widths: 17 18 35 30

   * - Backend / registry name
     - Install extra
     - When to consider it
     - Main qualification
   * - ``LMDBStore`` / ``lmdb``
     - ``gestaltdb[lmdb]``
     - Embedded transactions and coherent current-graph snapshots
     - Provision sufficient ``map_size``.
   * - ``LevelDBStore`` / ``leveldb``
     - ``gestaltdb[leveldb]``
     - Modest local graphs and straightforward embedded use
     - GestaltDB's multi-database layout has no graph-wide transaction/snapshot.
   * - ``PyRexStore`` / ``pyrex``
     - ``gestaltdb[rocksdb]``
     - RocksDB bulk ingestion and append-heavy workloads
     - Default mode is nontransactional; native columnar writes need the
       appropriate input/runtime path.
   * - ``PyRexStore(transactional=True)`` / ``pyrex`` plus backend option
     - ``gestaltdb[rocksdb]``
     - Explicit transactions and current snapshots on a capable PyRex runtime
     - No native columnar writer on the current TransactionDB path.

Install all built-in backends with ``gestaltdb[backends]``. Add codec extras
independently, for example ``gestaltdb[lmdb,msgpack]`` or
``gestaltdb[leveldb,protobuf]``. Pickle and JSON need no serializer extra. A base
install contains **no embedded backend**.

Parameterless ``GraphDB.create(path)`` retains the ``pyrex`` backend default and
requires the ``rocksdb`` extra. It never silently falls back to another backend.
Selected dependencies are checked before creating/replacing storage. Python must
be within the package's supported range (3.10–3.13); backend wheels and local build
tooling are additional installation constraints (:doc:`installation`).

Create and reopen a self-describing database
--------------------------------------------

**Standalone runnable example.** Requires ``gestaltdb[lmdb]``. Prefer this
manifest-backed path when GestaltDB owns the database directory.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node

   with TemporaryDirectory() as root:
       path = f"{root}/graph"
       with GraphDB.create(
           path, backend="lmdb", serializer="json",
           backend_options={"map_size": 2**30},
           indexed_node_properties=["name"],
       ) as graph:
           graph.put_node(Node("drug-1", labels=["Drug"], properties={"name": "Aspirin"}))

       with GraphDB.open(path) as reopened:
           assert reopened.manifest["backend"]["name"] == "lmdb"
           assert reopened.manifest["serializer"]["name"] == "json"
           assert [n.get_id for n in reopened.nodes_by_property("name", "Aspirin")] == ["drug-1"]

``create`` writes ``gestaltdb_manifest.json`` with backend/serializer choices and
configuration. ``open`` restores those choices and the configured property-index
metadata; it is not a way to pick a different decoder for the same stored bytes.
It requires a manifest. A nonempty target directory is rejected by ``create``
unless ``overwrite=True`` is explicitly supplied; **overwrite replaces storage**,
so never add it just to make a repeated notebook cell succeed.

For persistent applications, replace the temporary example path with an
application-owned directory. Close the old handle before reopening it. A context
manager closes the handle; it is **not** an implicit transaction around everything
inside the block.

.. _backend-selection-pattern:

Direct store construction
--------------------------

Use direct construction when an application explicitly manages its store and
codec. It does not automatically create a filesystem manifest. Reopen with the
same compatible configuration, or explicitly write a manifest with
``graph.save_manifest(path)`` before switching to ``GraphDB.open``.

**Standalone runnable example.** Requires ``gestaltdb[leveldb,msgpack]``.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node
   from gestaltdb.kvstores import LevelDBStore
   from gestaltdb.serializers import MessagePackSerializer

   with TemporaryDirectory() as root:
       graph = GraphDB(LevelDBStore(path=f"{root}/graph"), MessagePackSerializer())
       try:
           graph.put_node(Node("sample-1", properties={"raw": b"abc"}))
           assert graph.get_node("sample-1").properties["raw"] == b"abc"
       finally:
           graph.close()

Backend options and ingestion
------------------------------

LMDB Backend
~~~~~~~~~~~~~

Choose ``map_size`` for the intended database size. The same environment contains
named node/edge/adjacency/index/metadata databases. Native PyRex columnar writing
is not an LMDB capability.

LevelDB Backend
~~~~~~~~~~~~~~~~

The ``plyvel`` dependency may need a compatible wheel or local LevelDB build
tooling. Columnar ingestion uses portable writes rather than PyRex's native writer.

RocksDB Backend
~~~~~~~~~~~~~~~~

``parallelism``, ``max_background_jobs``, ``write_buffer_size`` and
``bloom_bits_per_key`` are backend tuning options. Keep ``disable_wal=False`` for
durable writes; disabling WAL is only appropriate for explicitly
disposable/reloadable experiments, not a safe default.

Select transactional PyRex at creation with
``backend="pyrex", backend_options={"transactional": True}``. The manifest
retains that mode for ``open``. Runtime capabilities remain explicit.

For structured JSON-compatible Arrow/Polars columns, entity-column ingestion can
avoid Python row-wise payload construction on eligible native paths. Serialized
payload ingestion accepts already encoded values. Neither API implies that every
codec/backend gets the same fast path. Columnar edge ingestion is append-only.
See :doc:`api-workflows` and :doc:`performance` for setup and measurements.

Indexes
-------

Labels and relationship types are indexed automatically. Node/edge property
indexes are explicit; their creation also provides composite and range lookup
support. Current index definitions persist in backend metadata.

``MAINTAIN`` keeps configured indexes current during ingestion. ``DEFER`` leaves
affected secondary indexes stale and requires ``rebuild_deferred_indexes()``;
high-level ``DEFER_REBUILD`` performs that rebuild before returning. Moving index
work out of the write window is not a guarantee of lower total ingest time.
See :doc:`property-filtering` for exact/range/ID/count APIs and freshness rules.

All backends enforce a 511-byte **final encoded index key** limit before backend
I/O. URL-safe-base64 components expand input, so an ID/value below 511 raw bytes
can still exceed the final limit. Oversized keys are rejected, not truncated.
.. _backend-index-interface:

Low-level sorted-index primitives are backend interfaces; prefer graph-level
lookups and index services in application code.

Transactions
------------

.. list-table:: Graph-wide mutable-state capabilities
   :header-rows: 1

   * - Mode
     - ``graph.transaction()``
     - ``graph.current_read_view()``
   * - LMDB
     - Supported across named databases
     - Pinned, verifiable :term:`MVCC` state
   * - LevelDB layout
     - Unsupported
     - Unsupported
   * - Plain PyRex
     - Unsupported
     - Unsupported
   * - Transactional PyRex
     - Supported when the runtime exposes transactions
     - Pinned when transaction snapshots are available; verifiable provenance
       requires PyRex >=0.7.0

Current RocksDB extras require ``pyrex-rocksdb>=0.7.1``. The older 0.7.0 boundary
above explains snapshot capability, not permission to install an older dependency.
Explicit unsupported transaction/snapshot requests raise rather than downgrade to
a best-effort guarantee.

**Standalone runnable example.** Requires ``gestaltdb[lmdb]``. Use the yielded
``tx`` handle for every operation in the transaction. Clean exit commits;
an exception leaving the transaction rolls back.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node

   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           with graph.transaction() as tx:
               tx.put_nodes([Node("drug-1", labels=["Drug"]), Node("protein-1")])
           try:
               with graph.transaction() as tx:
                   tx.put_node(Node("discarded", labels=["Drug"]))
                   raise RuntimeError("abort this transaction")
           except RuntimeError:
               pass
           assert graph.get_node("drug-1") is not None
           assert graph.get_node("discarded") is None
           assert [n.get_id for n in graph.nodes_by_label("Drug")] == ["drug-1"]

The context manager on ``GraphDB`` itself only owns handle lifetime. Do not infer
atomic read/modify/write behavior from separate calls, or cross-family atomicity
from a bulk-ingestion call. Use an explicit supported transaction for the required
scope. Individual APIs retain their own documented transaction boundaries.

Mutable Read Snapshots
----------------------

A current read view binds records, adjacency, indexes and metadata to one backend
snapshot. It differs from a live read-only repository and from a historical
temporal read view. **Standalone runnable example**, requiring ``gestaltdb[lmdb]``:

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node

   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           graph.put_node(Node("drug-1", properties={"revision": 1}))
           with graph.current_read_view(require_verifiable=True) as view:
               graph.put_node(Node("drug-1", properties={"revision": 2}))
               assert view.nodes.get("drug-1").properties["revision"] == 1
               assert view.provenance is not None
           assert graph.get_node("drug-1").properties["revision"] == 2

Verifiable provenance includes a snapshot sequence and full-state digest;
capturing it therefore scans the store. ``capture_provenance=False`` avoids that
digest when a caller needs only a pinned read, not a verifiable provenance record.
Do not retain view readers after the view closes.

Unqualified read-only Cypher automatically pins reads on capable backends without
capturing the full-state digest. ``read_snapshot=True`` explicitly requires a
snapshot; ``False`` opts out. High-level non-temporal sampler builds automatically
use **verifiable** snapshots where available; ``read_snapshot=True`` requires that
stronger build guarantee. Unsupported requests fail before building. Memmap
loading of a built sampler artifact is separate from these database snapshots.

Temporal publication and coordination
--------------------------------------

Temporal history is not the same as mutable graph transactions. LMDB and
transactional PyRex can publish version records and visibility markers in one
transaction. LevelDB/plain PyRex publish the marker last; interrupted unmarked
artifacts stay invisible, and recovery is explicit. Cooperating filesystem-backed
temporal writers are serialized across handles/processes with file locks.

Those locks do not turn all ordinary mutable operations into globally serialized
transactions or protect against arbitrary external storage modification. See
:doc:`temporal-versioning` and :doc:`operations` for writer coordination,
orphan recovery, index rebuilding and deprecated ``TimeIndexedEdge`` migration.
Do not delete canonical history to repair a derived index.

Measurements and next steps
---------------------------

Backend choice is workload-dependent. Account for codec work, index maintenance,
ingestion, snapshot construction and steady-state reads separately. Historical
numbers formerly embedded in this page remain in
:doc:`storage-backend-evidence`; they are not fresh v0.17.0 measurements or a
universal backend ranking. Use :doc:`performance` to reproduce relevant workloads.

.. _cleanup:

Cleanup
--------

Close graph handles with a context manager or ``try/finally`` as shown above.
Close transaction/read-view scopes before the parent handle and do not reuse
borrowed readers afterward. Back up and quiesce the database before migration
or recovery; closing a handle is not a migration or backup procedure.
