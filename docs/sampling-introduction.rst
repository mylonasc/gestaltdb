Sampling for graph learning
===========================

GestaltDB can draw neighbors from a live graph or build reusable arrays for
training batches. It prepares graph structure and examples; model training stays
in your framework. Start here, then choose a task below. The detailed policy and
format reference remains in :doc:`typed-sampling`.

Choose live reads or a snapshot
--------------------------------

.. list-table:: Pick the source for your workload
   :header-rows: 1

   * - Need
     - Live ``GraphDB`` traversal
     - ``SamplerSnapshot`` and ``SamplerEngine``
   * - Occasional reads of current relationships
     - Typed adjacency, external IDs, open database handle
     - Requires a separate build
   * - Repeated training batches
     - Each draw reads stored adjacency
     - Build once; reuse compact arrays after closing the database
   * - Property restrictions on every hop
     - Selecting starting nodes alone is insufficient
     - Build the eligible graph with node/edge filters
   * - Memory and cost
     - Reservoir storage is bounded by fanout; eligible adjacency is still scanned
     - RAM or memmap; builds, candidate/result buffers and validation still cost memory

A :term:`seed` is a starting entity; an RNG seed controls randomness.
:term:`Fanout <fanout>` is the number of neighbors requested per input node.
With no replacement, fewer eligible neighbors means fewer results, not padding
to the requested count. A snapshot does not track subsequent database writes.

Know which IDs you have
------------------------

- **External IDs** are application identifiers such as ``"drug-a"``. Live
  traversal records carry byte IDs; ``graph.key_to_string`` decodes them.
- **Compact global IDs** identify rows in one exact snapshot. Resolve an external
  ID with ``snapshot.node_int(...)``; never reuse a row number in another snapshot.
- **Batch-local IDs** identify rows in a sampled subgraph. Map them through
  ``batch.node_ids_global`` before resolving external IDs.

Methods ending in ``_external`` accept external IDs but still return compact
batches. Neighbor/layer/walk batches expose ``to_external(snapshot)`` for readable
output. Convert at your application boundary rather than on every training step.

Build and reuse a filtered graph
---------------------------------

This complete example creates a drug with two known targets. Only the high-score
protein enters the snapshot, so every sampled edge stays in the eligible graph.
Starting from the same drug in the unfiltered graph would still reach both
proteins: **filtering seeds is not filtering the traversed graph**.

The example requires a verifiable current snapshot. It evaluates property filters
inside the pinned build rather than selecting a cohort at a different horizon.

.. code-block:: python

   from tempfile import TemporaryDirectory

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.sampling import SamplerEngine

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           graph.put_nodes([
               Node("drug-a", labels=["Drug"], properties={"approved": True}),
               Node("protein-good", labels=["Protein"], properties={"score": 0.9}),
               Node("protein-low", labels=["Protein"], properties={"score": 0.2}),
           ])
           graph.put_edges_bulk([
               Edge("good", "drug-a", "protein-good", properties={"type": "binds"}),
               Edge("low", "drug-a", "protein-low", properties={"type": "binds"}),
           ])
           live = graph.sample_neighbors("drug-a", "binds", sample_size=10)
           assert {graph.key_to_string(row["neighbor_id"]) for row in live} == {
               "protein-good", "protein-low",
           }
           snapshot = graph.build_sampler_snapshot(
               f"{root}/snapshot", read_snapshot=True,
               node_filter=lambda node: (
                   "Drug" in node.labels and node.properties.get("approved") is True
                   or "Protein" in node.labels and node.properties.get("score", 0) >= 0.8
               ),
               edge_filter=lambda edge: edge.properties.get("type") == "binds",
           )
       finally:
           graph.close()

       engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
       neighbors = engine.sample_neighbors_external(
           ["drug-a"], fanout=10, relations=["binds"], direction="out",
       )
       names = neighbors.to_external(engine.snapshot)["neighbor_nodes"]
       assert list(names) == ["protein-good"]
       assert neighbors.offsets.tolist() == [0, 1]
       print(list(names))  # ['protein-good']

``offsets`` delimit each input node's output rows. Here one input has one eligible
neighbor, despite requesting ten. Excluding a node excludes its incident edges;
retained nodes can still be isolated. Build callbacks scan decoded records, not
automatic property-index pushdown. For explicit indexed cohorts and concurrency
qualifications, see :doc:`property-filtering`.

Choose your next task
----------------------

.. list-table:: Focused learning paths
   :header-rows: 1

   * - Goal
     - Guide
   * - Neighbors and message-passing layers
     - :doc:`sampling-neighborhoods`
   * - Node-induced or packed subgraphs
     - :doc:`sampling-subgraphs`
   * - Random walks and embedding inputs
     - :doc:`random-walks`
   * - Historical point/window samples
     - :doc:`sampling-temporal`
   * - Positive rejection and training negatives
     - :doc:`sampling-negatives`
   * - Expand parent relations or node types
     - :ref:`sampling-relation-hierarchies`, :ref:`sampling-node-hierarchies`

Use :doc:`sampling-consistency` for source pinning, RNG streams and immutable
artifact lifetime, :doc:`performance` for build/load/draw measurement, and
:doc:`typed-sampling` for the complete API and format contracts. Snapshot
construction materializes source data; memmap is not an out-of-core build or a
hard memory limit. Check :doc:`api` for exact signatures.
