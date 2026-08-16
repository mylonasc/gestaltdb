Performance and Benchmarks
==========================

GestaltDB includes benchmark scripts for ingestion, traversal, sampling, RocksDB
tuning, and an optional ArcadeDB comparison. Treat the included local results as
directional examples, not universal claims.

Practical Recommendations
-------------------------

For the library as it exists today, choose ingestion and storage options by the
shape of the workload rather than by a single global default:

- Use ``PyRexStore``/RocksDB for large append-only loads, columnar ingestion, and
  write-heavy workloads where native batch writes and RocksDB tuning matter.
- Use ``PyRexStore(transactional=True)`` only when graph-level RocksDB
  transactions are required. The transaction-capable backend currently cannot use
  PyRex's native columnar writer, so append-only bulk loads should usually keep
  the default RocksDB backend.
- Use ``LevelDBStore`` for simple local graphs, predictable installs through
  ``plyvel``, and workloads where the graph is small enough that Python object
  construction dominates storage I/O.
- Use ``LMDBStore`` when you want LMDB's mature embedded storage model and can
  size ``map_size`` ahead of loading. LMDB is not the optimized path for the
  current Arrow/Polars native columnar ingestion work.
- Prefer ``JSONSerializer`` with Polars or Arrow entity-column ingestion when
  data starts as tabular JSON-compatible columns. This avoids constructing
  ``Node`` and ``Edge`` objects per row and can move payload construction into
  Polars' vectorized JSON encoder.
- Prefer pre-serialized ``node_value`` and ``edge_value`` columns when upstream
  data already has serializer-compatible payload bytes. This isolates the
  backend write path and avoids repeated serialization in GestaltDB.
- Use ``IndexMaintenanceMode.MAINTAIN`` for incremental writes or when indexed
  queries must be valid immediately after each ingestion call.
- Use ``IndexMaintenanceMode.DEFER`` when the write window matters most and you
  can explicitly call ``GraphDB.rebuild_deferred_indexes()`` before indexed
  queries.
- Use ``IndexMaintenanceMode.DEFER_REBUILD`` as the safest bulk-load convenience
  mode: it defers secondary-index writes during ingestion and rebuilds stale
  indexes before returning.

The important deferred-index trade-off is latency placement. Deferring index
maintenance can make the write phase much faster, but a full rebuild still has to
scan stored graph records. If you include an immediate rebuild in the same timed
operation, total wall-clock time may be higher for some graph sizes and index
sets. The current implementation is best viewed as a way to shorten the critical
write window and make index rebuilding explicit and safe, not as a universal
end-to-end speedup.

Install Benchmark Dependencies
------------------------------

.. code-block:: sh

   python -m pip install -e ".[leveldb,rocksdb,fast-ingest]"

Backend Benchmarks
------------------

Use ``benchmarks.py`` for a quick backend comparison on the same append-only
workload.

.. code-block:: sh

   python benchmarks.py --backend leveldb --nodes 20000 --edges 100000 --batch-size 10000 --append-only
   python benchmarks.py --backend rocksdb --nodes 20000 --edges 100000 --batch-size 10000 --append-only
   python benchmarks.py --backend rocksdb --nodes 20000 --edges 100000 --batch-size 10000 --append-only --rocksdb-transactional

Use ``scripts/benchmark_matrix.py`` for larger matrix runs across graph sizes,
backends, core counts, and ingestion modes.

.. code-block:: sh

   uv run python scripts/benchmark_matrix.py \
      --sizes 10000 100000 1000000 \
      --edge-multiplier 1 \
      --cores 1 2 4 \
      --backends leveldb rocksdb \
      --ingestion-modes object arrow polars \
      --chunk-size 100000 \
      --samples 1000 \
      --sample-size 5 \
      --output-dir benchmark_results/matrix_YYYYMMDD

The matrix writes CSV and JSONL outputs and reopens the database before traversal
workloads so traversal does not only measure Python-side object state.

To compare default RocksDB with transaction-capable RocksDB on the same 5
edges/node shape used by the local result tables below:

.. code-block:: sh

   python scripts/benchmark_matrix.py \
      --backends rocksdb \
      --rocksdb-configs parallel-buffer64mb-bloom10 parallel-buffer64mb-bloom10-transactional \
      --sizes 10000 100000 \
      --edge-multiplier 5 \
      --cores 4 \
      --ingestion-modes arrow polars \
      --serializer pickle \
      --chunk-size 10000 \
      --samples 100 \
      --sample-size 5 \
      --output-dir benchmark_results/transactions_rocksdb_YYYYMMDD

Columnar Ingestion Benchmarks
-----------------------------

Columnar ingestion accepts already-serialized node and edge payloads. With
``PyRexStore`` and ``pyrex-rocksdb>=0.4.1``, RocksDB can use PyRex's native
``write_columnar_batch`` path. When Arrow-backed string or binary arrays are
passed, GestaltDB preserves those arrays through chunking and passes value
columns directly to PyRex instead of materializing Python ``bytes`` for every
stored value.

Structured JSON Entity Ingestion
--------------------------------

For ``JSONSerializer`` workloads, use the structured entity helpers to build
node and edge payloads from Polars or Arrow columns. GestaltDB uses Polars
``struct`` JSON encoding to serialize the payload column outside the Python
row loop, then sends the resulting Arrow binary values through the native PyRex
columnar write path when available.

.. code-block:: python

   import polars as pl

   from gestaltdb.graphdb import GraphDB
   from gestaltdb.kvstores import PyRexStore
   from gestaltdb.serializers import JSONSerializer

   graph = GraphDB(PyRexStore(path="graph_rocksdb"), JSONSerializer())

   nodes = pl.DataFrame({
       "node_id": ["drug-1", "protein-1"],
       "labels": [["Drug"], ["Protein"]],
       "kind": ["drug", "protein"],
   })
   graph.ingest_nodes_polars_entities(
       nodes,
       property_columns=["kind"],
   )

   edges = pl.DataFrame({
       "edge_id": ["d1-p1"],
       "source": ["drug-1"],
       "target": ["protein-1"],
       "edge_type": ["binds"],
       "score": [0.9],
   })
   graph.ingest_edges_polars_entities(
       edges,
       property_columns=["score"],
   )

This path is most useful when Python serialization dominates ingestion time.
It avoids constructing ``Node`` and ``Edge`` objects per row for JSON-compatible
payloads. Non-JSON serializers still work through the same methods, but they
fall back to Python object serialization so they do not get the same
serialization speedup.

Arrow inputs can use the same optimized JSON path when Polars is installed:

.. code-block:: python

   import pyarrow as pa

   graph.ingest_nodes_arrow_entities(
       pa.array(["n1", "n2"]),
       labels=pa.array([["Entity"], ["Entity"]]),
       properties={"kind": pa.array(["drug", "protein"])},
   )

   graph.ingest_edges_arrow_entities(
       pa.array(["e1"]),
       pa.array(["n1"]),
       pa.array(["n2"]),
       pa.array(["binds"]),
       properties={"score": pa.array([0.9])},
   )

See ``notebooks/05_columnar_ingestion_benchmark.ipynb`` for a runnable example.
A local run on 10,000 nodes and 50,000 edges with batch size 10,000 produced:

============================== =============== ================
Mode                           Node rate       Edge insert rate
============================== =============== ================
LevelDB object batch           1,110,296/s     167,463 edges/s
RocksDB Arrow columnar native  1,035,690/s     265,050 edges/s
RocksDB Polars columnar native 929,044/s       250,517 edges/s
============================== =============== ================

Larger append-only workloads with pre-serialized columnar payloads are expected
to benefit more than small runs dominated by Python object construction.

End-to-End Ingestion Insights
-----------------------------

``notebooks/06_end_to_end_ingestion_benchmark.ipynb`` measures serialization plus
ingestion. A targeted local run on this branch used 100,000 nodes, 500,000 edges,
batch size 10,000, ``JSONSerializer``, RocksDB native columnar ingestion, WAL
enabled, RocksDB parallelism 4, and a 64 MiB write buffer. Dataset generation and
database setup/cleanup were excluded.

==================================== ===================== ============= ========= ======== =================
Case                                 Isolates              Serialization Ingestion Rebuild  Total
==================================== ===================== ============= ========= ======== =================
LevelDB + Python JSON                baseline backend      0.882 s       3.066 s   0.000 s  3.948 s
RocksDB + Python JSON                RocksDB backend       0.916 s       2.484 s   0.000 s  3.400 s
RocksDB + Polars JSON                serialization path    0.494 s       2.400 s   0.000 s  2.894 s
RocksDB + Polars, maintain indexes   inline indexes        n/a           12.269 s  0.000 s  12.269 s
RocksDB + Polars, defer then rebuild deferred indexes      n/a           1.407 s   12.973 s 14.380 s
==================================== ===================== ============= ========= ======== =================

The same run showed these relative results:

=========================================================== ===============================
Comparison                                                  Result
=========================================================== ===============================
RocksDB vs LevelDB, same Python JSON path                   1.16x faster total
RocksDB vs LevelDB, ingestion/write phase only              1.23x faster write phase
Polars JSON vs Python JSON serialization on RocksDB         1.86x faster serialization
Polars JSON vs Python JSON end-to-end on RocksDB            1.17x faster total
Deferred index write phase vs inline index maintenance      8.72x faster write phase
Deferred index plus rebuild vs inline index maintenance     17.2% higher total time
=========================================================== ===============================

Interpretation:

- RocksDB's native columnar path helped the write phase on the measured workload,
  but the total gain was moderate because serialization was still a large share
  of wall-clock time.
- Polars JSON payload construction reduced serialization time substantially for
  JSON-compatible tabular inputs.
- Deferring index construction shifted work out of the write phase. This is useful
  when ingestion must complete quickly before a later rebuild step, but it was not
  faster end-to-end when the rebuild was performed immediately for this 100k node
  and 500k edge subset with node indexes on ``kind`` and ``group`` plus an edge
  index on ``weight``.
- Benchmark your actual graph shape and index set before promising absolute
  speedups. The fastest mode for append-only writes is not always the fastest mode
  for ingest-plus-query-ready indexes.

Transaction-Capable RocksDB Benchmark
-------------------------------------

``PyRexStore(transactional=True)`` opens RocksDB through PyRex's
``TransactionDB`` support. It provides graph-level transactions through
``GraphDB.transaction()``, and regular direct writes still work through the same
public GestaltDB APIs. The trade-off is that PyRex 0.4.1 exposes native
``write_columnar_batch`` on ``PyRocksDB`` but not on ``TransactionDB``. The
transaction-capable backend therefore falls back to Python ``PyWriteBatch`` paths
for columnar ingestion.

A focused local run on 2026-08-16 used ``pyrex-rocksdb==0.4.1``, 4 cores,
RocksDB parallelism 4, ``max_background_jobs=4``, a 64 MiB write buffer, Bloom
filters, Pickle payloads, chunk size 10,000, and 5 edges per node. Dataset
generation and traversal validation were included in the benchmark script's usual
way; the table below reports only write-phase times.

.. list-table::
   :header-rows: 1

   * - Nodes / edges
     - Mode
     - RocksDB backend
     - Native columnar
     - Node write
     - Edge write
   * - 10,000 / 50,000
     - Arrow
     - default
     - yes
     - 0.041 s
     - 0.185 s
   * - 10,000 / 50,000
     - Arrow
     - transaction-capable
     - no
     - 0.049 s
     - 0.345 s
   * - 10,000 / 50,000
     - Polars
     - default
     - yes
     - 0.046 s
     - 0.187 s
   * - 10,000 / 50,000
     - Polars
     - transaction-capable
     - no
     - 0.050 s
     - 0.343 s
   * - 100,000 / 500,000
     - Arrow
     - default
     - yes
     - 0.457 s
     - 1.886 s
   * - 100,000 / 500,000
     - Arrow
     - transaction-capable
     - no
     - 0.540 s
     - 3.623 s
   * - 100,000 / 500,000
     - Polars
     - default
     - yes
     - 0.429 s
     - 1.828 s
   * - 100,000 / 500,000
     - Polars
     - transaction-capable
     - no
     - 0.545 s
     - 3.670 s

The same environment's quick object-path benchmark with 20,000 nodes and 100,000
append-only edges measured 77,571 edges/s on default RocksDB versus 64,649
edges/s on transaction-capable RocksDB. Treat these results as the expected cost
of opening the backend in transaction-capable mode, not the cost of wrapping a
large write in one user transaction.

Operational guidance:

- Use default ``PyRexStore`` for reloadable bulk loads and columnar append-only
  ingestion.
- Use ``PyRexStore(transactional=True)`` when mutations must be atomic across
  nodes, edges, adjacency, secondary indexes, and metadata.
- If transaction-capable bulk ingestion needs native columnar throughput, PyRex
  should expose ``TransactionDB.write_columnar_batch`` so GestaltDB can keep the
  same fast path in transactional mode.

RocksDB Tuning and Compaction Benchmarks
----------------------------------------

Use ``scripts/tune_rocksdb.py`` for a small RocksDB tuning matrix against a
LevelDB baseline.

.. code-block:: sh

   python scripts/tune_rocksdb.py --nodes 20000 --edges 100000 --batch-size 10000

Use ``scripts/benchmark_rocksdb_compaction.py`` for a repeated-overwrite workload
that creates compaction pressure.

.. code-block:: sh

   uv run python scripts/benchmark_rocksdb_compaction.py \
      --configs leveldb rocksdb-p1-bg1-smallbuf rocksdb-p4-bg4-smallbuf rocksdb-p8-bg8-smallbuf rocksdb-p4-bg4-largebuf \
      --keys 250000 \
      --passes 6 \
      --batch-size 5000 \
      --value-size 1024 \
      --output-dir benchmark_results/compaction_pressure_YYYYMMDD

A local run on 2026-06-25 produced:

================================= =========== ===================== =================== =============
Configuration                     Backend     Initial write rate    Overwrite avg rate  Final SSTs
================================= =========== ===================== =================== =============
LevelDB                           LevelDB     329,433 writes/s      114,663 writes/s    30
RocksDB p1/bg1 small buffer       RocksDB     694,105 writes/s      262,405 writes/s    14
RocksDB p4/bg4 small buffer       RocksDB     1,008,871 writes/s    749,948 writes/s    47
RocksDB p8/bg8 small buffer       RocksDB     987,248 writes/s      772,436 writes/s    17
RocksDB p4/bg4 large buffer       RocksDB     1,088,475 writes/s    1,132,815 writes/s  7
================================= =========== ===================== =================== =============

This workload favors RocksDB because it creates overlapping sorted runs that can
benefit from background compaction parallelism. It should not be generalized to
all graph workloads.

ArcadeDB Comparison Benchmarks
------------------------------

Use ``scripts/benchmark_arcadedb_vs_gestaltdb.py`` to compare GestaltDB with the
optional embedded ArcadeDB package. ArcadeDB is not required for normal GestaltDB
use.

Run a GestaltDB-only smoke test:

.. code-block:: sh

   uv run python scripts/benchmark_arcadedb_vs_gestaltdb.py \
      --engines gestaltdb \
      --nodes 10000 \
      --edges 50000 \
      --iterations 25 \
      --output-dir benchmark_results/arcadedb_vs_gestaltdb_YYYYMMDD

Include embedded ArcadeDB with ``uv --with``:

.. code-block:: sh

   uv run --with arcadedb-embedded python scripts/benchmark_arcadedb_vs_gestaltdb.py \
      --engines gestaltdb gestaltdb-tx arcadedb \
      --workloads columnar_ingest star_traversal bfs_depth typed_path rocksdb_compaction \
      --nodes 100000 \
      --edges 500000 \
      --batch-size 100000 \
      --iterations 100 \
      --repetitions 10 \
      --output-dir benchmark_results/arcadedb_vs_gestaltdb_YYYYMMDD

The script writes raw rows and summary files grouped by engine and workload. If
``arcadedb-embedded`` is not installed, ArcadeDB rows are marked skipped and
GestaltDB rows still run.

Local results from 2026-08-16 used 100,000 nodes, 500,000 edges, batch size
100,000, 100 query iterations, one repetition, ``pyrex-rocksdb==0.4.1``, and
``arcadedb-embedded==26.8.1``. ``gestaltdb-tx`` means
``PyRexStore(transactional=True)``.

.. list-table:: Older ArcadeDB-suite results
   :header-rows: 1

   * - Workload
     - GestaltDB/RocksDB total
     - GestaltDB/RocksDB tx total
     - ArcadeDB total
     - Fastest
   * - columnar_ingest
     - 3.131 s
     - 6.257 s
     - 5.407 s
     - GestaltDB/RocksDB
   * - star_traversal
     - 51.907 s
     - 57.456 s
     - 4.403 s
     - ArcadeDB
   * - bfs_depth
     - 6.844 s
     - 9.604 s
     - 5.138 s
     - ArcadeDB
   * - typed_path
     - 6.574 s
     - 9.380 s
     - 4.625 s
     - ArcadeDB
   * - rocksdb_compaction
     - 0.438 s
     - 0.545 s
     - not applicable
     - GestaltDB/RocksDB

Interpret these results by workload. RocksDB/GestaltDB remains strongest on
append-only columnar ingestion and compaction-sensitive raw writes. ArcadeDB is
stronger in this older suite's traversal shapes, especially ``star_traversal``:
that workload repeatedly materializes all 500,000 outgoing edges from one hub
node, which is not representative of sparse point traversals.

External Graph Database Benchmarks
----------------------------------

Use ``scripts/benchmark_external_graphdbs.py`` for a unified benchmark against
GestaltDB/RocksDB, Neo4j, Memgraph, and ArcadeDB. The runner starts disposable
Neo4j and Memgraph Docker containers by default, waits for Bolt availability,
loads the same deterministic synthetic graph into each engine, runs the selected
traversal/sampling workload, and writes raw plus summary CSV/JSONL outputs.
ArcadeDB is exercised through its embedded Python API package.

The external benchmark intentionally reports both ingestion and query phases. For
query workloads, each engine loads a fresh database first so traversal results are
measured on the same graph shape. GestaltDB closes and reopens its RocksDB-backed
store before counting and querying, so the traversal phase is not reading only
Python-side objects left alive by ingestion. The summary files include
``actual_nodes``, ``actual_edges``, and ``count_status`` validation columns; rows
should only be compared when ``count_status`` is ``ok``.

The workloads are:

- ``ingest``: load deterministic ``Node`` vertices and typed ``RelA``/``RelB``/
  ``RelC`` directed edges.
- ``neighbors``: for the first ``iterations`` node IDs, count outgoing ``RelA``
  neighbors.
- ``sample_neighbors``: reservoir-sample outgoing ``RelA`` neighbors with a fixed
  sample size and seed.
- ``bfs_depth``: client-side typed BFS from ``n0`` over ``RelA``/``RelB``/
  ``RelC`` up to the configured depth, excluding the seed from the returned
  count.
- ``typed_path``: count exact two-hop ``RelA`` then ``RelB`` traversals from the
  first ``iterations`` seeds, capped by ``path_fanout_limit`` per seed.

Install the optional benchmark dependencies:

.. code-block:: sh

   uv sync --extra fast-ingest --extra external-bench

Run a small smoke benchmark with container-managed Neo4j and Memgraph:

.. code-block:: sh

   uv run python scripts/benchmark_external_graphdbs.py \
      --engines gestaltdb gestaltdb-tx neo4j memgraph arcadedb \
      --nodes 1000 \
      --edges 5000 \
      --batch-size 1000 \
      --iterations 10 \
      --repetitions 1 \
      --output-dir benchmark_results/external_graphdbs_smoke

Run a larger end-to-end comparison:

.. code-block:: sh

   uv run python scripts/benchmark_external_graphdbs.py \
      --engines gestaltdb gestaltdb-tx neo4j memgraph arcadedb \
      --workloads ingest neighbors sample_neighbors bfs_depth typed_path \
      --nodes 100000 \
      --edges 500000 \
      --batch-size 10000 \
      --iterations 100 \
      --repetitions 1 \
      --output-dir benchmark_results/external_graphdbs_YYYYMMDD

Corrected Aligned Large Results
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A local corrected aligned run on 2026-08-16 used 100,000 nodes, 500,000 edges,
batch size 10,000, 100 traversal seeds, sample size 5, depth 3, and one
repetition. Neo4j used ``neo4j:5-community`` through the official Python Bolt
driver. Memgraph used ``memgraph/memgraph:latest`` through the same Bolt driver.
ArcadeDB used ``arcadedb-embedded==26.8.1``. GestaltDB used ``pyrex-rocksdb==0.4.1``
with JSON payloads, Arrow/Polars entity ingestion, deferred secondary-index
maintenance, and a close/reopen before validation and querying. All rows
validated exactly 100,000 nodes and 500,000 edges.

Ingestion phase:

.. list-table::
   :header-rows: 1

   * - Engine
     - Ingest seconds
     - Edge ingest rate
     - Relative to default GestaltDB
   * - GestaltDB/RocksDB
     - 1.70 s
     - 294,727 edges/s
     - 1.00x
   * - GestaltDB/RocksDB transactional
     - 2.85 s
     - 175,728 edges/s
     - 1.68x slower
   * - ArcadeDB embedded
     - 6.57 s
     - 76,055 edges/s
     - 3.88x slower
   * - Memgraph
     - 8.62 s
     - 58,020 edges/s
     - 5.08x slower
   * - Neo4j
     - 13.00 s
     - 38,468 edges/s
     - 7.66x slower

Query phase on the already-loaded graph:

.. list-table::
   :header-rows: 1

   * - Workload
     - Result count
     - GestaltDB
     - GestaltDB tx
     - ArcadeDB
     - Memgraph
     - Neo4j
   * - neighbors
     - 167
     - 0.00132 s
     - 0.00154 s
     - 0.0420 s
     - 0.0181 s
     - 0.174 s
   * - sample_neighbors
     - 167
     - 0.00140 s
     - 0.00168 s
     - 0.0555 s
     - 0.0210 s
     - 0.188 s
   * - bfs_depth
     - 3
     - 0.000144 s
     - 0.000179 s
     - 0.00671 s
     - 0.00479 s
     - 0.121 s
   * - typed_path
     - 271
     - 0.00307 s
     - 0.00369 s
     - 0.0630 s
     - 0.0293 s
     - 0.230 s

Relative query phase compared with default GestaltDB/RocksDB:

.. list-table::
   :header-rows: 1

   * - Workload
     - GestaltDB tx
     - ArcadeDB
     - Memgraph
     - Neo4j
   * - neighbors
     - 1.16x slower
     - 31.7x slower
     - 13.6x slower
     - 131x slower
   * - sample_neighbors
     - 1.20x slower
     - 39.8x slower
     - 15.1x slower
     - 135x slower
   * - bfs_depth
     - 1.24x slower
     - 46.5x slower
     - 33.2x slower
     - 842x slower
   * - typed_path
     - 1.20x slower
     - 20.5x slower
     - 9.55x slower
     - 75.1x slower

Interpretation:

- GestaltDB is fastest on this synthetic append-only workload because it writes
  directly to an embedded RocksDB key-value layout using columnar batches. The
  benchmark does not pay a client/server protocol cost for ingestion, and the
  typed adjacency records used by traversal are written as sorted key prefixes.
- The transaction-capable GestaltDB/RocksDB backend validated the same graph but
  loaded 1.68x slower than the default RocksDB backend. This is consistent with
  PyRex 0.4.1 exposing native columnar writes on ``PyRocksDB`` but not on
  ``TransactionDB``.
- ArcadeDB embedded also avoids Bolt/network overhead, but its graph batch loader
  maintains a native graph record layout and then builds a vertex ID index. On
  this graph shape it loaded about 3.9x slower than default GestaltDB but faster than the
  Bolt-backed servers.
- Memgraph and Neo4j ingest through batched Cypher over Bolt. That path is a fair
  Python API path, but it includes client/server round trips, Cypher planning and
  execution, node lookup by indexed ``id``, and relationship creation in the
  server. Memgraph was about 1.56x faster than Neo4j for ingestion in this run.
- Query times favor GestaltDB strongly because each traversal is a small number
  of direct embedded typed-adjacency prefix scans. Neo4j and Memgraph execute one
  Bolt query per seed or BFS frontier expansion in this benchmark. That measures
  realistic Python-driver use for many small traversals, but it also amplifies
  protocol and query-execution overhead relative to embedded APIs.
- The BFS workload has a very small reachable set on this deterministic graph
  shape. Its absolute query times are therefore dominated by per-query overhead;
  use larger or denser topologies before generalizing BFS ratios.
- The ``sample_neighbors`` workload uses reservoir sampling for GestaltDB and a
  Python-side reservoir sampler over streamed neighbor rows for the other engines.
  This aligns semantics, but different drivers expose rows with different
  overheads.
- The table reports ``ingest_seconds`` and ``query_seconds``. GestaltDB's
  ``reopen_seconds`` was roughly 0.51 s for default RocksDB and 0.57-0.61 s for
  transaction-capable RocksDB. Count validation was roughly 0.41-0.42 s and
  0.44-0.45 s respectively. Those values are recorded separately in the summary
  file and are not included in ``total_seconds``.

Useful container options:

- Use ``--no-containers`` to connect to already-running Neo4j/Memgraph services.
- Use ``--neo4j-uri``, ``--neo4j-user``, ``--neo4j-password``, and
  ``--memgraph-uri`` to override connection settings.
- Use ``--neo4j-bolt-port`` and ``--memgraph-bolt-port`` if local ports ``7687``
  or ``7688`` are already in use.
- Use ``--keep-containers`` only when debugging container startup or database
  state. The default is to stop containers after each run.

The external runner records skipped rows rather than aborting when Docker, a
Python driver, or an optional backend is unavailable. For fairer large-result
comparisons, run on an otherwise idle host and use the generated summary files
instead of terminal output.

Benchmark Caveats
-----------------

- Local benchmark results depend on graph shape, storage device, CPU settings,
  Python version, backend versions, and warm-up behavior.
- Small graphs can be dominated by Python object construction, serialization, and
  key construction rather than backend I/O.
- Prefer raw CSV/JSONL outputs for comparisons and keep benchmark parameters with
  published results.
