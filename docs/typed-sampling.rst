.. _typed-traversal-and-sampling:

High-Performance Graph Sampling
===================================

Sampling is a first-class GestaltDB workflow, from application-side typed
traversal to repeated GNN/KG training batches. The live APIs and the array-native
engine serve different workloads; they are not interchangeable implementations
of the same operation.

The :doc:`architecture` guide shows the ingestion-to-training application path
and how explicit reasoning adapters can supply derived graph structure.

Choose a Sampling Layer
-----------------------

.. list-table:: Live graph vs reusable snapshot
   :header-rows: 1
   :widths: 20 40 40

   * -
     - Live ``GraphDB``
     - ``SamplerSnapshot`` + ``SamplerEngine``
   * - Data
     - Current KV-backed graph, maintained typed adjacency
     - Immutable compact NumPy/CSR arrays, built once and reused
   * - IDs and output
     - External IDs; traversal records carry byte IDs
     - Compact integer arrays; ``*_external`` entry points and conversion helpers
   * - Policies
     - Uniform reservoir neighbors, typed paths, materialized subgraphs
     - Node/edge seeds, uniform/weighted neighbors, layers, walks, multihop,
       positive triples and hard negatives
   * - Memory
     - Reservoir sample storage bounded by fanout; eligible adjacency is scanned
     - RAM or memmap loading; results, candidate buffers, and membership data
       still use memory
   * - Property filtering
     - Indexed seed selection or Cypher; not arbitrary per-hop sampling callbacks
     - ``node_filter``/``edge_filter`` at build time; relation/direction/time
       policies over arrays at sampling time

See :doc:`property-filtering` to define eligible nodes and edges **before**
sampling. Filtering the seeds alone does not restrict every sampled hop. See
:doc:`performance` for build/load/sampling costs and benchmark commands; no
fixed speedup or fully out-of-core behavior is promised.

Array-Native API Map
--------------------

.. list-table:: Sampling policies available in the engine
   :header-rows: 1

   * - Method
     - Use
   * - ``sample_nodes`` / ``sample_edges``
     - Seed draws with node-type/relation filters, balanced quotas, temporal availability, and degree/weight strategies where supported
   * - ``sample_neighbors`` / ``sample_neighbors_external``
     - Batched uniform or weighted neighbors, direction/relation filters, offsets
   * - ``sample_layers`` / ``sample_layers_external``
     - Independent fanout, direction, relations, replacement, strategy per layer
   * - ``LayeredSampleBatch.to_blocks`` / ``to_blocks_external``
     - Bipartite message-passing blocks with local ``edge_index`` frames per layer
   * - ``LayeredSampleBatch.to_pyg_blocks``
     - Per-layer PyG-style tensor dictionaries with temporal edge metadata
   * - ``sample_random_walks`` / ``sample_random_walks_external``
     - Uniform/weighted first-order walks, repeating metapaths, restarts, dead-end policies
   * - ``sample_node2vec_walks`` / ``sample_node2vec_walks_external``
     - Second-order biased walks with return/in-out factors and restart/terminate dead ends
   * - ``sample_multihop``
     - Multi-hop fanouts, with causal valid-start policies on temporal snapshots
   * - ``sample_subgraph`` / ``sample_subgraph_external``
     - Edge-seeded local batches with positives, negatives, and sampled topology
   * - ``sample_packed_subgraphs``
     - Independent per-group subgraphs in one batch with component offsets
   * - ``sample_partition_block``
     - Uniform random balanced partition block with exact normalization
   * - ``sample_induced_subgraph``
     - Uniform node draws with all eligible induced edges and a fail-closed edge cap
   * - ``sample_hard_negatives``
     - Endpoint-type/relation constraints, positive rejection, temporal policies

Quick Start Without a Database
------------------------------

If an upstream pipeline already has edge arrays, build a snapshot directly.
This example needs only the base install, not a KV backend extra:

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import (
       ExternalNeighborSamplingSpec, SamplerEngine, SamplerSnapshot,
   )

   with TemporaryDirectory() as tmpdir:
       snapshot = SamplerSnapshot.from_edge_arrays(
           f"{tmpdir}/sampler",
           external_node_ids=["a", "b", "c"],
           external_edge_ids=["ab", "ac"], external_relation_ids=["R"],
           src_int=[0, 0], dst_int=[1, 2], rel_int=[0, 0],
           edge_weights=[1.0, 3.0],
       )
       engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
       neighbors = engine.sample_neighbors_external(
           ["a"], fanout=2, relations=["R"], strategy="weighted",
       )
       assert set(neighbors.to_external(engine.snapshot)["neighbor_nodes"]) == {"b", "c"}
       layers = engine.sample_layers_external(
           ["a"], [ExternalNeighborSamplingSpec(2, relations=["R"])],
       )
       walks = engine.sample_random_walks_external(["a"], 2, metapath=["R"])

For a database-backed filtered/weighted snapshot, follow the end-to-end example
in :doc:`property-filtering`. Load the engine once and reuse it across batches;
sampling does not require an open database and does not see later graph writes.

Deterministic multi-worker sampling
-----------------------------------

Data-loader workers run in separate processes and cannot share ``Generator``
objects. Split one parent seed into independent per-worker seeds, then rebuild
one engine per worker from its integer seed:

.. code-block:: python

   from tempfile import TemporaryDirectory

   from gestaltdb.sampling import SamplerEngine, SamplerSnapshot, split_seed

   with TemporaryDirectory() as tmpdir:
       probe = SamplerSnapshot.from_edge_arrays(
           f"{tmpdir}/sampler",
           external_node_ids=["a", "b", "c"],
           external_edge_ids=["ab", "ac"], external_relation_ids=["R"],
           src_int=[0, 0], dst_int=[1, 2], rel_int=[0, 0],
       )
       worker_seeds = split_seed(7, num_workers=4)
       first = SamplerEngine.load(probe.path, seed=worker_seeds[0])
       second = SamplerEngine.load(probe.path, seed=worker_seeds[0])
       assert first.seed == second.seed == worker_seeds[0]

Sibling streams are statistically independent while the whole split
reproduces from ``(seed, num_workers)``. ``seed=None`` still splits
independently but draws fresh entropy, so pass an explicit seed for
deterministic training. ``engine.worker_engine(worker_id, num_workers=...)``
is shorthand for the same construction on an already-loaded engine.

Compact, Local, and External IDs
--------------------------------

``SampledNeighbors`` returns global compact ``neighbor_nodes`` and
``edge_indices``, with ``offsets`` separating each seed's samples.
``SampledSubgraphBatch`` instead uses **batch-local** IDs in ``senders``,
``receivers``, ``positives``, and ``negatives``. Map local rows through
``node_ids_global`` before resolving them against the snapshot. ``*_external``
entry points accept application IDs but still return compact result objects.
Convert neighbor/layer/walk results with ``to_external(engine.snapshot)`` at the
application boundary, not on every hot-path operation.

Live Typed Traversal
--------------------

Typed traversal uses ``edge.properties["type"]``. When an edge has a type,
GestaltDB stores typed adjacency records for efficient directional scans.

Create a Typed Graph
--------------------

.. code-block:: python

   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.kvstores import LMDBStore
   from gestaltdb.serializers import PickleSerializer

   graph_db = GraphDB(LMDBStore(path="typed_graph_lmdb"), PickleSerializer())

   for node_id, kind in [
       ("drug-1", "drug"),
       ("protein-1", "protein"),
       ("protein-2", "protein"),
       ("disease-1", "disease"),
   ]:
       graph_db.put_node(Node(node_id=node_id, properties={"kind": kind}))

   graph_db.put_edges_bulk([
       Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "drug-to-protein"}),
       Edge(edge_id="d1-p2", source="drug-1", target="protein-2", properties={"type": "drug-to-protein"}),
       Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "protein-to-disease"}),
   ])

Query Typed Neighbors
---------------------

.. code-block:: python

   proteins = graph_db.neighbors_by_edge_type(
       "drug-1",
       "drug-to-protein",
       direction="out",
   )
   print(proteins)

Query Typed Edges
-----------------

.. code-block:: python

   edge_ids = graph_db.edges_by_edge_type(
       "drug-1",
       "drug-to-protein",
       direction="out",
   )

Sample Neighbors
----------------

``sample_neighbors`` uses reservoir sampling, so memory is bounded by
``sample_size`` instead of by node degree. This bounds sample storage, not scan
time: live reservoir sampling still visits the eligible typed adjacency.

.. code-block:: python

   import random

   sample = graph_db.sample_neighbors(
       "drug-1",
       "drug-to-protein",
       direction="out",
       sample_size=1,
       rng=random.Random(7),
   )

Live Seed Sampling
------------------

``GraphDB.sample_nodes`` draws live node IDs with reservoir sampling, so memory
is bounded by ``sample_size`` rather than graph size. Eligible keys are still
scanned: pass ``label`` to restrict the scan to one native-label index, and
``node_filter`` to decode and filter candidates. There is no property-index
pushdown; use indexed snapshot cohorts or Cypher filters when scan time
matters. The draw is deterministic for a given key order and seeded ``rng``.

.. code-block:: python

   import random

   seeds = graph_db.sample_nodes(
       32,
       label="Drug",
       node_filter=lambda node: node.properties.get("score", 0) >= 0.5,
       rng=random.Random(7),
   )

Object-Based Sampling Patterns
------------------------------

Use ``SamplingHop`` and ``SamplingPattern`` for validated, documented sampling
configuration objects.

.. code-block:: python

   import random

   from gestaltdb.sampling import SamplingHop, SamplingPattern

   pattern = SamplingPattern([
       SamplingHop("drug-to-protein", direction="out", sample_size=2),
       SamplingHop("protein-to-disease", direction="out", sample_size=1),
   ])

   paths = graph_db.sample_typed_paths(
       seed_ids=["drug-1"],
       pattern=pattern,
       rng=random.Random(3),
   )

Dictionary-Based Sampling Patterns
----------------------------------

Existing dictionary configurations are still supported.

.. code-block:: python

   pattern = [
       {"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2},
       {"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1},
   ]

   paths = graph_db.sample_typed_paths(["drug-1"], pattern)

Cypher Sampling Procedure
-------------------------

The Cypher API exposes multi-hop typed path sampling through a GestaltDB-specific
procedure call. This delegates to ``GraphDB.sample_typed_paths`` and returns one
``path`` value per sampled path.

.. code-block:: python

   result = graph_db.query(
       'CALL pg.sample_typed_paths(["drug-1"], '
       '[{"edge_type": "drug-to-protein", "direction": "out", "sample_size": 2}, '
       '{"edge_type": "protein-to-disease", "direction": "out", "sample_size": 1}]) '
       'YIELD path RETURN path'
   )

   for record in result:
       print(record["path"])

Sample a Materialized Subgraph
------------------------------

.. code-block:: python

   subgraph = graph_db.sample_typed_subgraph(
       seed_ids=["drug-1"],
       pattern=pattern,
   )

   print(subgraph["nodes"].keys())
   print(subgraph["edges"].keys())
   print(subgraph["paths"])

Rebuild Typed Adjacency
-----------------------

If edge records already exist but typed adjacency indexes are missing, rebuild
them from stored edges.

.. code-block:: python

   rebuilt = graph_db.rebuild_typed_adjacency()
   print(f"rebuilt {rebuilt} typed adjacency records")

Array-Native Sampling Policies
------------------------------

``SamplerSnapshot`` and ``SamplerEngine`` provide seed, weighted-neighbor,
layered, and first-order random-walk sampling without hydrating graph entities.
For graph-backed builds, set ``edge_weight_property="confidence"``; for compact
arrays, pass ``edge_weights`` to ``from_edge_arrays`` or ``from_arrays``.
Weights must be finite and non-negative. Missing weights default to one;
weighted selection excludes zero-weight candidates and rejects non-empty
candidate sets whose weights are all zero. Without replacement, the requested
count is capped at the number of eligible candidates.

First-class importance-bias policies derive those weights. ``uniform``
reproduces the ``1.0`` default; ``degree`` upweights hub-incident edges by
endpoint-degree sum at unit mean; ``recency`` applies exponential decay from
edge valid-starts over a positive finite half-life on temporal snapshots and
fails closed on edges starting after the reference time; ``confidence``
normalizes caller-supplied non-negative scores with positive total mass.
Persist derived weights with ``snapshot.set_edge_weights(weights,
source="policy:degree")`` — the rewrite is atomic, compact IDs are untouched,
and ``edge_weight_source`` records provenance. Draws stay ``uniform`` unless a
call passes ``strategy="weighted"``; with replacement, fanout caps, and
zero-weight exclusion are unchanged, and per-type/per-relation quotas are
opt-in through ``balanced=True`` (see below).

.. code-block:: python

   from gestaltdb.sampling import SamplerSnapshot, derive_edge_weights

   with TemporaryDirectory() as tmpdir:
       importance = SamplerSnapshot.from_edge_arrays(
           f"{tmpdir}/snapshot",
           external_node_ids=["a", "b", "c"],
           external_edge_ids=["ab", "ac"],
           external_relation_ids=["R"],
           src_int=[0, 0], dst_int=[1, 2], rel_int=[0, 0],
       )
       weights = derive_edge_weights([0, 0], [1, 2], policy="degree")
       summary = importance.set_edge_weights(weights, source="policy:degree")
       assert summary["edge_weight_source"] == "policy:degree"

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import NeighborSamplingSpec, SamplerEngine, SamplerSnapshot

   with TemporaryDirectory() as tmpdir:
       snapshot = SamplerSnapshot.from_edge_arrays(
           f"{tmpdir}/snapshot",
           external_node_ids=["a", "b", "c"],
           external_edge_ids=["ab", "ac"],
           external_relation_ids=["R"],
           src_int=[0, 0], dst_int=[1, 2], rel_int=[0, 0],
           edge_weights=[1.0, 3.0],
       )
       engine = SamplerEngine(snapshot, seed=7)
       seeds = engine.sample_nodes(2)
       edges = engine.sample_edges(1, strategy="weighted")
       neighbors = engine.sample_neighbors_external(["a"], 1, strategy="weighted")
       layers = engine.sample_layers([0], [NeighborSamplingSpec(2, strategy="weighted")])
       walks = engine.sample_random_walks_external(["a"], 3, metapath=["R"])
       print(neighbors.to_external(snapshot))

``sample_nodes`` supports uniform, degree-based, and caller-weighted selection,
with an optional node-type filter. ``sample_edges`` supports uniform or weighted
selection with an optional relation filter. These are filtered draws by default;
pass ``balanced=True`` for deterministic equal quotas across node types or
relation IDs. Layer specifications independently select fanout, direction,
relations, replacement, strategy, and balanced allocation.
``sample_node2vec_walks`` samples node2vec-style second-order walks. From the
second transition on, candidates are weighted ``1/p`` for returning to the
previous node, ``1`` at distance one, and ``1/q`` beyond, measured on the
undirected incident adjacency; the first transition is uniform and static
snapshot edge weights multiply the bias. Distances ignore relation and
temporal filters by design.

Streaming walk corpora (RWS-01)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For context-pair generation and the embedding pipeline extensions, see
:doc:`random-walks`.

``engine.iter_random_walks`` yields ordinary ``RandomWalkBatch`` objects without
building the complete corpus. Each round visits every seed position once;
``walks_per_node`` controls the number of rounds. Omit ``start_nodes`` to visit
all compact snapshot nodes, or pass a compact-ID cohort (duplicates are retained).
Resolve external cohorts with ``snapshot.node_ints(...)``.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import SamplerEngine, SamplerSnapshot

   with TemporaryDirectory() as root:
       snapshot = SamplerSnapshot.from_edge_arrays(
           root + "/snapshot", external_node_ids=["a", "b"],
           external_edge_ids=["ab"], external_relation_ids=["R"],
           src_int=[0], dst_int=[1], rel_int=[0],
       )
       engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
       count = 0
       for batch in engine.iter_random_walks(
           walk_length=4, walks_per_node=3, batch_size=2,
           direction="any", shuffle=True,
       ):
           count += len(batch.lengths)
           # Feed valid node prefixes to an external embedding trainer.
           for nodes, length in zip(batch.node_ids, batch.lengths):
               sequence = nodes[:int(length) + 1]
       assert count == 6

Use ``walk_type="node2vec"`` with ``return_factor``/``inout_factor`` for biased
walks; otherwise extra keywords go to ``sample_random_walks`` unchanged,
including weights, metapaths, restarts, and temporal filters. Padding, restart
markers, and transition-count lengths retain the existing batch conventions.
Arguments are validated when the iterator is created, even for zero rounds or
empty cohorts. Generation is lazy; zero rounds or empty cohorts yield no batches.

Unshuffled all-node iteration allocates only batch-sized seed buffers. Explicit
cohorts are copied once, and ``shuffle=True`` allocates an O(seed count) global
permutation for each round; it is not constant-memory shuffling. Output storage
is O(batch size times walk length), in addition to the existing kernel candidate
buffers. Consume/discard batches rather than collecting the iterator into a list.
Keep the snapshot alive throughout iteration, including with memory mapping.

Replay requires the same initial engine RNG state, snapshot, seed cohort, options,
and consumption order. Changing only batch size preserves concatenated walks,
including shuffled rounds; interleaving other operations on that RNG voids this
guarantee. Existing ``worker_engine(worker_id, num_workers=...)`` provides
independent worker streams. Iteration does not launch workers, partition seed
cohorts automatically, or promise invariance across worker counts. Embedding
training, context-pair generation, and embedding-objective negatives remain
external to this API.

Layer exports
~~~~~~~~~~~~~

``LayeredSampleBatch`` preserves each layer's ``SampledNeighbors`` block and
deduplicates the next frontier. ``to_blocks(snapshot)`` additionally repackages
each layer as a framework-neutral bipartite message-passing block: unique
sorted ``src_nodes`` (sampled neighbors) and ``dst_nodes`` (layer inputs) plus
an ``edge_index`` with one column per sampled edge occurrence, aligned
``edge_ids``, and ``relation_ids``. Blocks are oriented by message flow
(neighbor to input); graph-edge orientation is not encoded, so consult the
``NeighborSamplingSpec`` values passed to ``sample_layers`` when it matters.
``to_blocks_external(snapshot)`` maps the ID arrays without touching
``edge_index`` positions. ``to_pyg_blocks(snapshot)`` converts each block to
``torch.long`` tensors (temporal version IDs stay string-array metadata,
matching ``SampledSubgraphBatch.to_pyg``); the dictionaries convert directly
into ``torch_geometric`` bipartite blocks without a ``torch_geometric``
dependency here.

Balanced quotas
~~~~~~~~~~~~~~~

``sample_nodes(..., balanced=True)`` splits the requested count equally across
the distinct compact node types present in the candidate set. ``sample_edges``
and ``sample_neighbors`` do the same across relation IDs, per candidate for
edges and per input node for neighbors. Groups are visited in sorted compact-ID
order; when the count does not divide evenly, earlier groups receive one extra
slot. With ``replace=False``, a group smaller than its share returns fewer rows
and unused quota is not borrowed from other groups, so the result can be below
the requested count. With ``replace=True`` every quota is filled, possibly with
duplicate rows. Balanced weighted draws skip groups with no positive weight; if
every group has zero weight, the usual all-zero-weight error is raised.

``balanced`` is also available on ``NeighborSamplingSpec`` and
``ExternalNeighborSamplingSpec`` so layered sampling can request the same
contract per layer.

.. code-block:: python

   balanced_neighbors = engine.sample_neighbors([0], 9, balanced=True)
   balanced_nodes = engine.sample_nodes(30, balanced=True)
   balanced_edges = engine.sample_edges(9, balanced=True)
   layer = NeighborSamplingSpec(fanout=9, balanced=True)

Relation sub-property hierarchies
---------------------------------

Samplers match literal relation IDs. When edge types form a hierarchy
(``Ocean_Freight`` as a sub-property of ``Transport_Edge``), define it once
and expand parent names to their transitive sub-properties before sampling:

.. code-block:: python

   from gestaltdb.sampling import RelationHierarchy

   hierarchy = RelationHierarchy({
       "Ocean_Freight": ["Transport_Edge"],
       "Rail": ["Transport_Edge"],
       "Refrigerated_Freight": ["Ocean_Freight"],
   })
   assert hierarchy.expand(["Transport_Edge"]) == (
       "Transport_Edge", "Ocean_Freight", "Refrigerated_Freight", "Rail")

Hierarchies are immutable and validated at construction: empty names,
self-parenting, duplicate child entries, and parent/child cycles all fail
closed. Live typed traversal accepts the hierarchy per call, applying it to
every hop while keeping the requested fanout as the total across expanded
relations:

.. code-block:: python

   paths = graph.sample_typed_paths(
       ["warehouse"],
       [{"edge_type": "Transport_Edge", "sample_size": 10}],
       relation_hierarchy=hierarchy,
   )
   neighbors = graph.sample_neighbors(
       "warehouse", "Transport_Edge", sample_size=10,
       relation_hierarchy=hierarchy,
   )

Without ``relation_hierarchy``, live traversal keeps literal relation matching.
Then resolve through the snapshot so every array-native engine method composes
without new parameters:

.. code-block:: python

   expanded = snapshot.expand_relations(["Transport_Edge"], hierarchy)
   neighbors = engine.sample_neighbors([0], fanout=10, relations=expanded)

Expanded names absent from the snapshot fail closed (typo safety), while
hierarchy-undefined names expand to themselves. Fanout caps apply to the
union of expanded relations, not per sub-relation. Node label sub-types and
relationship sub-properties are available for opt-in Cypher matching through
``LabelHierarchy``/``RelationHierarchy`` and
``GraphDB.query(..., label_hierarchy=..., relation_hierarchy=...)`` (see
:doc:`cypher`). Sampler node-type expansion over compact snapshot IDs is
described below; ontology-to-hierarchy construction is described in
:doc:`ontology`.

Node type hierarchies
~~~~~~~~~~~~~~~~~~~~~

Snapshots built with semantic ``node_type_values`` record the index-to-name
mapping. Expand a parent node type to its transitive sub-types before passing
compact IDs to APIs that accept ``node_types``:

.. code-block:: python

   from gestaltdb import LabelHierarchy

   node_types = LabelHierarchy({
       "Ocean_Node": ["Transport_Node"],
       "Refrigerated_Node": ["Ocean_Node"],
       "Air_Node": ["Transport_Node"],
   })
   expanded = snapshot.expand_node_types(["Transport_Node"], node_types)
   sampled = engine.sample_nodes(32, node_types=expanded)

Values absent from the snapshot fail closed (typo safety), and snapshots built
only from raw ``node_type_ids`` cannot be expanded because compact IDs have no
recorded names. Hierarchy-undefined names expand to themselves. Expansion
changes which nodes are eligible for selection; returned rows keep their
original arrays and literal compact type IDs.


Walks support direction/relation filters, repeating metapaths, weighted steps,
random restart, and terminate/restart dead-end policies. Their fixed-width node,
edge, and relation arrays use ``-1`` for padding; restart transitions also have
``-1`` edge/relation IDs. Metapath position advances only on graph-edge steps.
``to_external(snapshot)`` converts these gaps to ``None``.

Resolve external IDs with ``node_int(s)``, ``edge_int(s)``, and
``relation_int(s)``. Plural methods preserve input order and report missing IDs
together. Persisted sorted lookup arrays support memmap binary search. Duplicate
logical edge IDs in temporal history are intentionally ambiguous: use compact
edge rows rather than ``edge_int``/``sample_subgraph_external`` in that case.

Layer and walk APIs accept ``temporal`` and ``window_policy`` filters, applied
before selection at every step; these filters do not impose causal ordering
between steps. Use the existing multihop/subgraph APIs for causal policies.
Seed-node and seed-edge draws also accept ``temporal`` and ``window_policy``:
node draws filter node availability, while edge draws filter edge-version
validity. ``"overlap"`` keeps intervals intersecting the window;
``"contained"`` keeps intervals fully inside it and therefore excludes
open-ended intervals. Omitting ``temporal`` on traversal APIs still selects
from all snapshot rows.

New weights and lookup arrays are authenticated by the v2 artifact catalog.
Older v2 snapshots without them still load with unit weights and derived
lookups, without weakening their existing checksum guarantees.

Temporal Sampler Snapshots
--------------------------

Format-v2 snapshots can preserve the edge history visible at one system-time
horizon. Each compact edge row has an edge-version ID, half-open valid interval,
open-end mask, commit ID, and system time. The temporal outgoing and incoming
CSR indexes are ordered by endpoint, relation, and valid-time start.

.. code-block:: python

   snapshot = graph_db.build_sampler_snapshot(
       "snapshots/history",
       temporal=True,
       system_time="2026-01-01T00:00:00Z",
       time_bucket="day",
   )

   assert snapshot.temporal
   print(snapshot.edge_version_ids)
   print(snapshot.valid_from_us, snapshot.valid_to_us, snapshot.valid_to_open)

The build captures exactly one authenticated ``GraphReadView``. Corrections and
retractions are resolved at its system horizon, including splitting a surviving
version into multiple edge rows when only part of its validity is retracted.
``temporal_candidates(node, relation, valid_time, direction=...)`` uses the
start-time ordering to return a candidate superset. ``SamplerEngine`` then
applies exact half-open validity filtering before fanout and random selection:

.. code-block:: python

   from gestaltdb.sampling import SamplerEngine
   from gestaltdb.temporal import TemporalContext

   engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
   sampled = engine.sample_neighbors(
       [alice_id],
       fanout=20,
       direction="out",
       relations=[works_for_id],
       temporal=TemporalContext.as_of(cutoff),
   )

Point selection honors exclusive valid-to boundaries. For finite windows,
``window_policy="overlap"`` selects edge intervals with a non-empty
intersection, while ``window_policy="contained"`` requires the complete edge
interval to lie inside the window (open-ended edges therefore cannot be
contained). ``sample_multihop`` and ``sample_subgraph`` accept the same options.
Set ``causal_policy`` to ``"nondecreasing"`` or ``"nonincreasing"`` to compare
edge valid-start instants hop by hop along each sampled path. Temporal, relation,
direction, and causal filters all run before fanout selection.

Seed draws use the same temporal contract before selection:

.. code-block:: python

   seed_nodes = engine.sample_nodes(
       32, temporal=TemporalContext.as_of(cutoff)
   )
   seed_edges = engine.sample_edges(
       32, temporal=TemporalContext.during(window_start, window_end)
   )

Node seeds filter on node availability history; edge seeds filter on
edge-version validity rows. Both accept ``"overlap"`` and ``"contained"``.

Temporal ``SampledNeighbors`` and ``SampledSubgraphBatch`` values expose
``edge_version_ids``, ``valid_from_us``, ``valid_to_us``, and
``valid_to_open`` aligned with their sampled edge arrays. Supplying a temporal
filter or causal policy to a non-temporal/legacy snapshot raises ``ValueError``.
PyG dictionaries and DGL graphs retain string ``edge_version_ids`` as Python
metadata because their edge-feature stores only accept numeric tensors.

Bounded Induced Subgraphs
-------------------------

``sample_partition_block`` draws a Cluster-GCN-style random balanced
partition block: nodes are randomly permuted, split into contiguous chunks of
nearly equal size, and one chunk is chosen uniformly. Partitions are uniform,
not cut-optimized — no METIS-style cut quality is claimed. With
``normalize=True`` nodes carry weight ``num_partitions`` and non-loop edges
carry the exact reciprocal of the realized pair-selection probability;
self-loops carry the node weight.

``sample_packed_subgraphs`` samples one independent subgraph per seed-edge
group — each exactly as ``sample_subgraph`` would sample it alone — and
repackages them into a single ``SampledSubgraphBatch``. ``graph_node_offsets``
and ``graph_edge_offsets`` mark each component's rows; local IDs are rebased
to the concatenated frame, so global IDs may repeat across components while
each slice stays self-contained. Positive rows follow seed-group order and
negative diagnostics are summed.

``sample_induced_subgraph`` draws nodes uniformly without replacement and
returns every eligible edge induced by that node set. Relation and temporal
filters apply before the ``max_edges`` cap; exceeding the cap raises instead of
truncating or silently resampling. Positives and negatives are empty, so the
batch is a framework-neutral topology block for GraphSAINT-style training.

.. code-block:: python

   batch = engine.sample_induced_subgraph(
       256,
       max_edges=50_000,
       relations=[works_for_id],
       temporal=TemporalContext.as_of(cutoff),
   )
   assert batch.positives.size == 0
   assert batch.n_nodes == 256

Node selection is uniform over the snapshot; there is no degree correction or
partition sampling in this method. The cap is a fail-closed memory bound, not a
statistical normalization.

Pass ``normalize=True`` to attach GraphSAINT-style unbiased weights instead:
``node_weights`` are ``num_nodes_snapshot / num_nodes`` per node and
non-loop ``edge_weights`` are ``N (N - 1) / (k (k - 1))`` per edge, the exact
reciprocals of the without-replacement inclusion probabilities. Self-loops
are node-inclusion events and carry the node weight. Relation and temporal
filters restrict which edges are returned but do not change the draw
probabilities, so filtered batches keep the same weights. Weights are
``None`` by default and flow through ``to_numpy`` and the framework exports
when present.

Time-Aware Hard Negatives
-------------------------

Temporal snapshots also contain authenticated positive-triple history and node
availability indexes. ``is_positive`` can test all known history, one example
instant, or a query window. Hard-negative sampling applies endpoint type and
relation constraints first, temporal candidate availability second, and
positive-history rejection last:

.. code-block:: python

   from gestaltdb.sampling import HardNegativeConfig

   config = HardNegativeConfig(
       negatives_per_positive=8,
       temporal_positive_policy="at_positive_time",
       temporal_candidate_window_days=90,
       exhaustion_policy="raise",
   )
   batch = engine.sample_subgraph(
       seed_edges,
       fanouts=[15, 10],
       negative_config=config,
       temporal=TemporalContext.as_of(cutoff),
   )

``temporal_positive_policy`` is ``"any_time"`` (the compatibility default),
``"at_positive_time"``, or ``"window"``. Window rejection uses
``temporal_positive_window_policy="overlap"`` or ``"contained"``. A candidate
window is trailing: candidates and relation neighborhoods must have been
available during the configured number of days ending at the example time.
Without a candidate window they must be available at the example time. Future
candidates are excluded unless ``allow_future_candidates=True``.

``in_batch=True`` tries other positive heads or tails with the same relation
before random or context candidates. The current positive endpoint is skipped;
normal type, availability, duplicate, and positive rejection still apply. If
the in-batch pool is exhausted, the configured source and random fallback run as
before. ``accepted_in_batch`` counts accepted peers in
``return_diagnostics``.

``candidate_distribution="degree"`` applies only to the random fallback and
weights candidates by ``incident_degree ** degree_exponent`` (default
``0.75``). In-batch and context candidates are still tried first. A graph with
no incident edges falls back to uniform sampling; non-finite weights fail closed
instead of silently degrading.

``sample_hard_negatives`` accepts aligned ``positive_times_us`` and optionally
returns deterministic rejection counters with ``return_diagnostics=True``.
Direct calls on temporal snapshots must supply ``positive_times_us`` or a
``temporal`` context unless ``allow_future_candidates=True``; this prevents an
ambiguous default call from drawing nodes that only become available later.
``SampledSubgraphBatch`` carries ``positive_time_us``, ``negative_time_us``, and
``negative_diagnostics``. ``exhaustion_policy="raise"`` fails when unique
negatives are exhausted; ``"repeat"`` deterministically reuses an accepted
negative while preserving temporal-positive rejection. RAM and memmap engines
produce the same seeded result.

V2 publication is atomic: arrays and canonical metadata are written in a sibling
staging directory, checksummed, and followed by ``completion.json`` before the
directory is renamed into place. Loading in RAM or memmap mode validates the
completion record, artifact catalog, SHA-256 checksums, dtypes, shapes, aligned
lengths, CSR bounds, interval invariants, and source-provenance token. Existing
format-v1 snapshots still load, but have no integrity or temporal guarantees.
Rebuild v1 snapshots from their source database to migrate to v2; snapshots are
immutable and have no in-place upgrade path. Unknown format versions and corrupt
v2 artifacts fail closed.

Memory-mappable positive membership
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

New snapshots identify exact positive membership with
``positive_membership_encoding="lexicographic-triples-v1"`` in the authenticated
v2 envelope. Non-temporal builds persist ``positive_membership_triples.npy``:
sorted unique int64 ``(source, relation, target)`` rows. Temporal snapshots reuse
their already-persisted ``positive_history_triples``; row numbers identify the
same interval groups as before, without a second key array or a Python group
dictionary. ``positive_triples`` remains the edge-aligned array and may contain
duplicate triples; it is not replaced or reordered.

``SamplerEngine`` borrows the sorted keys directly from its snapshot in RAM or
memmap mode. It no longer builds Python packed-code sets or temporal lookup
dictionaries. Binary search compares exact coordinates, not hashes or packed
uint64 values. This avoids packing overflow and preserves exact rejection,
temporal gaps, half-open boundaries, overlap/contained window policies, seeded
RNG sequences and exhaustion behavior. Lookup is O(log U) for U unique triples,
rather than expected O(1) hash-set lookup, so reduced Python object memory may
cost latency. The static key payload is 24 * U bytes; this is not an RSS bound.

The loader authenticates the added artifact, schema and exact sorted unique
membership against canonical edge triples. This validation still allocates
edge/unique-sized working arrays and reads the artifacts. Engine construction
does not repeat that membership build for new snapshots. Legacy temporal
snapshots reuse history keys too, except that pre-history-index v2 compatibility
loading must first derive those arrays as before. Legacy non-temporal v1/v2
snapshots without the encoding marker build a sorted array once in RAM at
engine construction, even in memmap mode; rebuild them to a fresh path to
persist the new index. Supported non-temporal v2 rewrites also persist it.

Unknown membership encodings and missing/malformed indexes fail closed. Older
loaders reject new non-temporal artifact catalogs. The membership feature adds
no temporal artifacts beyond existing history arrays, but new builds also use
sparse relation directories (below), which older loaders reject. No implicit on-load rewrite
occurs. No partitioned snapshot or universal zero-copy/constant-memory guarantee
is introduced.

Membership keys share snapshot ownership/lifetime: retain the engine/snapshot
while using them, and do not mutate arrays or replace their files under active
readers. Memmaps are opened read-only. Candidate/result buffers, temporal
interval checks, weight validation, metadata, OS page cache and legacy admission
allocations remain; removing the membership sets does not remove these costs.

Sparse relation directories
~~~~~~~~~~~~~~~~~~~~~~~~~~~

New builds identify their directory encoding with
``relation_adjacency_encoding="sparse-rows-v1"`` inside the authenticated v2
envelope. ``relation_out``, ``relation_in`` and, for temporal snapshots,
``temporal_out`` / ``temporal_in`` persist sorted int64 ``*_row_keys`` arrays
containing only populated ``node * num_relations + relation`` keys. Their
``indptr`` arrays address these populated rows, not every possible key.
Use ``adjacency.edge_range(key)`` instead of indexing ``indptr[key]``; absent
keys return empty slices. Dense node-only adjacency is unchanged.

A directory with P populated pairs uses ``8 * (2 * P + 1)`` payload bytes
for row keys and offsets, instead of ``8 * (N * R + 1)`` dense offset bytes.
Edge-index payloads are unchanged. This is most useful when P is much smaller
than N * R; sparse rows can cost more on densely populated relation grids.
Lookup changes from direct indexing to binary search over populated keys.
Within each row, stable edge order and temporal valid-start/edge-row tie order
are preserved, retaining seeded sampling behavior.

RAM and memmap loading validate row-key uniqueness/order/range, nonempty rows,
offsets, exact membership, checksums and schemas. No dense relation directory
is reconstructed for sparse artifacts. Validation still allocates edge-sized
working arrays; construction still materializes the graph. Candidate buffers
and page-cache costs remain; positive membership is handled separately above.

The loader still accepts dense v1/v2 snapshots without an encoding marker.
Unknown encodings fail closed. Older GestaltDB loaders reject new snapshots
because their authenticated artifact catalogs contain unfamiliar row-key
arrays; they must be upgraded before reading them. No implicit on-load rewrite
occurs. Rebuild to a fresh path to migrate; existing supported non-temporal v2
append/compact/weight rewrites also publish sparse directories. Existing
temporal in-place rewrite restrictions remain unchanged.
