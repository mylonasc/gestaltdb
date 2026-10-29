Property-Graph Filtering and Indexes
========================================

Filter attributed graphs by **node labels, relationship types, exact property
values, ranges, and Cypher predicates**. Use indexed candidate sets for selective
reads, then build a reusable filtered sampler snapshot when every training hop
must stay within that graph. For sampling policies, see :doc:`typed-sampling`;
for cost and benchmark guidance, see :doc:`performance`.

Choose Where to Filter
--------------------------

.. list-table:: Three different operations—not interchangeable filters
   :header-rows: 1
   :widths: 25 40 35

   * - Stage
     - API
     - Meaning
   * - Select records or seeds
     - ``nodes_by_label_property_range``, ``edges_by_type_property``, Cypher
     - Restricts returned records or starting IDs, not subsequent traversal.
   * - Define the sampled graph
     - ``build_sampler_snapshot(node_filter=..., edge_filter=...)``
     - Build-time callbacks exclude nodes/edges before arrays are persisted.
   * - Restrict a sampling step
     - Engine ``relations``, ``direction``, ``temporal``, layer policies
     - Selects eligible array candidates before the fanout draw. No arbitrary
       property callback runs in the sampling hot path.

Selecting high-score seed nodes alone does **not** prevent a walk from reaching
low-score nodes. Filtering samples after drawing them can reduce effective fanout
and changes the intended sampling process. To sample from an eligible
property-defined graph, filter the snapshot before sampling.

Automatic and Explicit Indexes
------------------------------

- ``Node(labels=[...])`` maintains native label indexes automatically.
- ``Edge(properties={"type": ...})`` maintains relationship-type indexes and
  directional typed adjacency automatically.
- ``create_node_property_index("score")`` and
  ``create_edge_property_index("confidence")`` explicitly build property indexes
  over existing records and maintain them on subsequent writes.
- A configured property also gets composite **label + property** or
  **relationship type + property** index entries. These are not arbitrary
  multi-property composite indexes, and need no separate composite declaration.
- Secondary indexes configured for the mutable graph are distinct from temporal
  history indexes and the sampler's immutable CSR arrays.

Exact and Range Lookup Reference
--------------------------------

.. list-table:: Hydrated lookup helpers
   :header-rows: 1

   * - Scope
     - Exact
     - Range
   * - Nodes
     - ``nodes_by_property(name, value)``
     - ``nodes_by_property_range(name, start, end)``
   * - Labeled nodes
     - ``nodes_by_label_property(label, name, value)``
     - ``nodes_by_label_property_range(label, name, start, end)``
   * - Edges
     - ``edges_by_property(name, value)``
     - ``edges_by_property_range(name, start, end)``
   * - Typed edges
     - ``edges_by_type_property(type, name, value)``
     - ``edges_by_type_property_range(type, name, start, end)``

Ranges default to inclusive bounds. Use ``None`` for an unbounded endpoint;
``include_start=False`` and ``include_end=False`` select exclusive bounds.
Scalar range encodings are type-aware: do not mix unrelated value categories
when defining a range. Immutable GestaltDB temporal scalars support configured
exact/range property indexes; these are different from bitemporal version reads.

When you only need candidate IDs, use the corresponding
``iter_node_ids_by_*`` / ``iter_edge_ids_by_*`` helpers instead of hydrating
``Node`` / ``Edge`` objects. They yield byte IDs; decode with
``graph.key_to_string`` before comparing with external strings. The ``count_*``
variants count indexed IDs without hydrating entities; they are not guaranteed
constant-time metadata lookups. Lookup order is not guaranteed.

Cypher Filtering
----------------

``MATCH`` supports labels, relationship types, inline property maps, and
parameterized ``WHERE`` predicates. Eligible exact/range predicates use configured
indexes to select candidates; residual predicates are then evaluated for
correctness. This is not a promise that every expression, conjunction, or graph
expansion uses an index or intersects every applicable index.

Use label/type + property indexes for selective entry points, then typed
adjacency for traversal. String/regex predicates and computed expressions can
require candidate scans. See :doc:`cypher` for supported syntax and planner
behavior. A successful ``WHERE`` query by itself is not evidence that it used a
property index.

End-to-End: Indexed Cohort to Filtered Sampling
---------------------------------------------------

Install ``gestaltdb[lmdb]`` for this complete example. Only approved drugs,
high-score proteins, and high-confidence binding relationships enter the
snapshot. Edges with excluded endpoints are omitted even if their own properties
pass the edge filter. Retained nodes can be isolated. Snapshot edges must have
the configured relation property (``edge_type_property="type"`` by default);
untyped edges are not included.

.. code-block:: python

   from tempfile import TemporaryDirectory

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.sampling import SamplerEngine

   with TemporaryDirectory() as tmpdir:
       graph = GraphDB.create(f"{tmpdir}/graph", backend="lmdb", serializer="json")
       try:
           graph.create_node_property_index("approved")
           graph.create_node_property_index("score")
           graph.create_edge_property_index("confidence")
           graph.put_nodes([
               Node("drug-1", labels=["Drug"], properties={"approved": True}),
               Node("drug-2", labels=["Drug"], properties={"approved": False}),
               Node("protein-1", labels=["Protein"], properties={"score": 0.9}),
               Node("protein-2", labels=["Protein"], properties={"score": 0.95}),
               Node("protein-3", labels=["Protein"], properties={"score": 0.5}),
           ])
           graph.put_edges_bulk([
               Edge("e1", "drug-1", "protein-1", properties={"type": "binds", "confidence": 0.95}),
               Edge("e2", "drug-1", "protein-2", properties={"type": "binds", "confidence": 0.4}),
               Edge("e3", "drug-1", "protein-3", properties={"type": "binds", "confidence": 0.99}),
               Edge("e4", "drug-2", "protein-2", properties={"type": "binds", "confidence": 0.99}),
           ])
           # ID-only index scans avoid hydrating entities just to select a cohort.
           drug_ids = {
               graph.key_to_string(key)
               for key in graph.iter_node_ids_by_label_property("Drug", "approved", True)
           }
           protein_ids = {
               graph.key_to_string(key)
               for key in graph.iter_node_ids_by_label_property_range("Protein", "score", 0.8, None)
           }
           edge_ids = {
               graph.key_to_string(key)
               for key in graph.iter_edge_ids_by_type_property_range("binds", "confidence", 0.9, None)
           }
           cohort = drug_ids | protein_ids
           result = graph.query(
               'MATCH (d:Drug {approved: true})-[r:binds]->(p:Protein) '
               'WHERE p.score >= $score AND r.confidence >= $confidence '
               'RETURN d.id AS drug, p.id AS protein',
               {"score": 0.8, "confidence": 0.9},
           )
           assert result.records == [{"drug": "drug-1", "protein": "protein-1"}]

           snapshot = graph.build_sampler_snapshot(
               f"{tmpdir}/sampler",
               node_filter=lambda node: node.get_id in cohort,
               edge_filter=lambda edge: edge.get_id in edge_ids,
               edge_weight_property="confidence",
               read_snapshot=True,
           )
       finally:
           graph.close()

       engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
       sampled = engine.sample_neighbors_external(
           ["drug-1"], fanout=10, relations=["binds"], strategy="weighted",
       )
       assert sampled.to_external(engine.snapshot)["edge_ids"].tolist() == ["e1"]
       assert set(engine.snapshot.external_node_ids.tolist()) == cohort

Build-Time and Runtime Boundaries
---------------------------------

``node_filter`` and ``edge_filter`` receive decoded graph objects during snapshot
construction. The builder still scans source nodes and edges; passing indexed
ID sets to these callbacks does **not** turn the build into an index-only or
predicate-pushdown plan. Callback work and cohort sets consume time and memory.
If compact edge arrays are already available upstream,
``SamplerSnapshot.from_edge_arrays`` avoids graph-object hydration altogether.

The example has no concurrent writers. If cohort IDs are selected before a
snapshot build, they are not automatically pinned to its later source horizon.
Quiesce writers for the full selection/build workflow, or express the property
conditions directly in callbacks within a pinned build. ``read_snapshot=True``
requires a verifiable coherent mutable source snapshot (supported by LMDB and
by transactional PyRex with ``pyrex-rocksdb>=0.7.0``);
unsupported backends fail that explicit request rather than silently weakening
it. The default uses this guarantee automatically where available.

Snapshots do not track later graph writes. Rebuild to change the eligible graph
or its persisted weights. At runtime, use compact relation/type filters and
temporal policies supported by :doc:`typed-sampling`, not property callbacks.
``node_type_property`` defaults to the ``kind`` property, not native node labels;
``sample_nodes(node_types=...)`` uses encoded type IDs from snapshot metadata.

The example describes **mutable-graph snapshots**. For temporal history,
valid/system horizons, node availability, and leakage-aware negatives, use
:doc:`temporal-versioning` and the temporal sections of :doc:`typed-sampling`;
do not substitute current-property cohorts for historical property evaluation.

Index Maintenance and Limits
-----------------------------

- Index configuration persists across database reopens.
- Deferred ingestion marks affected secondary indexes stale. Use
  ``rebuild_deferred_indexes()`` before index-backed reads, or ingest with
  ``IndexMaintenanceMode.DEFER_REBUILD`` to rebuild before returning.
- Encoded secondary/range keys have a **511-byte limit** across backends.
  Oversized indexed IDs/values fail instead of being silently truncated.
- Indexes trade write work and storage for selective reads. Arbitrary
  multi-property predicates may still require residual filtering. Benchmark
  selectivity and graph shape as well as total node/edge counts.
