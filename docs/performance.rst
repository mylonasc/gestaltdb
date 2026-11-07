Performance and Benchmarks
==========================

Performance depends on graph shape, payload size, indexes, backend, serializer,
cache state and query selectivity. Measure the complete application workflow,
not just its fastest stage. Historical storage measurements and their limits are
in :doc:`storage-backend-evidence`; reasoning comparisons are in
:doc:`ontology-benchmarks` and :doc:`reasoning-frontier-benchmarks`.

Start with the complete task
----------------------------

.. list-table:: Keep setup, work and output costs visible
   :header-rows: 1

   * - Workload component
     - Include in measurement
     - Application guide
   * - Ingestion
     - Table/payload conversion, record writes and required index rebuilds
     - :doc:`ingestion`
   * - Querying
     - Index selection, adjacency expansion, decoding, projection and result count
     - :doc:`data-access`, :doc:`cypher-tutorial`
   * - Snapshot construction
     - Source acquisition/authentication, filtering, array writes and validation
     - :doc:`sampling-introduction`
   * - Snapshot loading and repeated draws
     - Cold/warm loading separately from steady-state sampling and ID conversion
     - :doc:`sampling-neighborhoods`
   * - Reasoning
     - Extraction, conversion, joins/fixpoint, witness/support retention and maintenance
     - :doc:`reasoning-workflows`
   * - Source/state authentication
     - Snapshot hashing or historical catalog/evidence verification and re-admission
     - :doc:`reasoning-current-admission`, :doc:`reasoning-range-admission`
   * - Publication
     - Guard acquisition, recomputation/revalidation, target checks and durable writes
     - :doc:`ontology-publication`, :doc:`reasoning-bound-range-publication`
   * - Exchange and visualization
     - Full NetworkX materialization, mapping, selected payload, HTML/SVG generation and browser layout/export
     - :doc:`networkx`, :doc:`visualization`

An improvement in one row is not automatically an end-to-end improvement. Include
required freshness/index checks and count successful outputs before comparing
timings. A query's ``LIMIT`` bounds returned rows, not necessarily all source reads
or intermediate work. Changing transaction, snapshot or evidence guarantees is
a different workload, not a faster equivalent implementation.

.. _sampling-and-filtering-performance:

Sampling and filtering
----------------------

Live traversal reads stored adjacency and returns external IDs. Snapshot sampling
uses compact arrays and avoids repeated payload decoding. Building a snapshot
still scans and converts source records; amortize that cost over repeated draws.
See :doc:`typed-sampling` and :doc:`property-filtering` for complete examples.

.. list-table:: Costs to measure separately
   :header-rows: 1

   * - Stage
     - Main cost
   * - Cohort lookup
     - Index selectivity and decoded result count
   * - Snapshot build
     - Source reads, filtering, conversion and array writes
   * - Snapshot load
     - Array loading or memory mapping and metadata validation
   * - Sampling
     - Degree, eligible candidates, fanout, replacement and weights
   * - External-ID conversion
     - Mapping compact result rows back to application identifiers

Filtering seeds alone does not constrain later hops. Build the eligible graph
with ``node_filter`` and ``edge_filter`` when every hop must satisfy a property
condition. Build callbacks scan decoded records; they do not automatically push
property predicates into storage indexes. Memory mapping avoids eagerly loading
all arrays, not all allocations or operating-system page-cache use.

For random walks, use the ordinary kernels when no extended policy is needed.
Typed, finite-state, temporal and avoidance policies perform additional candidate
selection. Compare identical policies and output counts; see :doc:`random-walks`.
No runtime transition-distribution cache or alternative native walk executor is
provided.

Storage and ingestion
----------------------

Choose a backend by its required semantics before comparing throughput. See
:doc:`storage-backends` for transaction and snapshot capabilities and
:doc:`serializers` for independent payload choices.

- Default ``PyRexStore`` supports the native columnar writer for reloadable bulk
  loads and append-only ingestion. Transactional PyRex provides graph-level
  atomicity but does not expose the same native columnar write path.
- LMDB requires a suitable ``map_size``. Include transaction sizing, durability
  settings and available address space in measurements.
- LevelDB does not provide a unified verifiable mutable-graph snapshot. Do not
  compare it as an equivalent substitute for workflows requiring that guarantee.
- Property indexes accelerate configured lookups but add write and storage costs.
  Label and relationship-type indexes are maintained automatically.
- ``IndexMaintenanceMode.DEFER`` shifts secondary-index work out of ingestion;
  call ``graph.rebuild_deferred_indexes()`` before index-backed reads.
  ``DEFER_REBUILD`` performs the rebuild before the high-level loader returns.
  Include rebuilding in end-to-end timing: a faster write phase need not mean a
  faster complete load.

GestaltDB's RocksDB extras require ``pyrex-rocksdb>=0.7.1``. Point-read latency
depends on both the binding and the decoding layer. To measure native reads,
serialized store reads and decoded graph reads over identical JSON nodes, run
the repository benchmark:

.. code-block:: sh

   python -m benchmarks.pyrex_point_reads --cpu 2 > reads.json

CPU affinity is optional; choose a CPU available to your process. The benchmark
measures warmed hits, not cold storage or concurrent throughput. Use separate
environments for dependency versions, hold the GestaltDB revision and Python
version constant, alternate process order and compare process medians. Reports
include samples, dependency versions, workload and affinity information.

Reasoning and authentication
------------------------------

Full closure, a selective goal answer and a retained support index have different
output/work contracts. Compare the same program, selected input horizon, proof
mode and completeness requirement. Prepared plans, native sources and Polars
are explicit options, not automatic replacements (:doc:`reasoning-workflows`).

Authenticated mutable snapshots can hash the full state; temporal admission may
scan complete catalogs/history and re-extract evidence. A selective native prefix
scan does not remove those authentication costs. Include source acquisition and
verification, not only native transfer or join-kernel time. Saved-state checks
and publication may revalidate/recompute; a matching digest is not necessarily
a cheap cache hit. Logical row/work limits are not wall-time or RSS guarantees.

Reproduce measurements
-----------------------

Benchmark runners are source-checkout tools, not installed library APIs. Inspect
their command-line options before choosing a workload:

.. code-block:: sh

   uv run python -m benchmarks --help
   uv run python -m benchmarks temporal --nodes 100 --edges 500 --queries 100
   uv run python -m benchmarks.random_walks --help

Install only the optional dependencies required by the selected runner. Small
smoke runs establish that the workload executes; they do not establish a latency
distribution or production-scale performance.

For columnar loading, compare structured entity columns with serializer-compatible
payloads and record whether conversion happens inside the timed region. See
:doc:`ingestion` for runnable Arrow and Polars workflows. Native writer support
does not imply that all serializers have the same conversion path.

Measurement checklist
----------------------

- Record the source/package version, Python and optional dependency versions,
  hardware, operating system, thread settings and durability options.
- Record node/edge counts, degree distribution, labels/types, payload sizes,
  duplicate rates, filters and expected output cardinality.
- Verify answers before timing. Compare the same graph semantics, output and
  transaction/snapshot guarantees, not only similar-looking query strings.
- Separate setup, ingestion, index rebuild, read-view acquisition, evaluation,
  decoding, publication and cleanup. Report total time alongside stage times.
- Repeat in isolated processes. State whether handles are fresh or reused;
  reopening a handle does not make the operating-system cache cold.
- Report full-worker peak RSS separately from Python-traced allocations and
  array payload bytes. None is interchangeable with a hard memory limit.
- Preserve failed, timed-out and bounded runs as failures, not zero-time results.
  A few repetitions do not justify precise tail-latency claims.

Historical reports retain their original source revisions and configurations.
Do not describe an old source measurement as a result for the current wheel, or
apply a write-phase speed ratio to an end-to-end workflow.
