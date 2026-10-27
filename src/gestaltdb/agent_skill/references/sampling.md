# Sampling For Library Users

GestaltDB has two sampling layers: live typed traversal for application queries,
and reusable array-native CSR snapshots for high-throughput training batches.
For property-graph cohort selection, read the `indexing` topic too.

## GraphDB Traversal Sampling

Use this for application code and external node IDs:

- `sample_neighbors`
- `sample_typed_paths`
- `sample_typed_subgraph`
- `SamplingHop`
- `SamplingPattern`

## Rules

- Traversal records use the keys `edge_id`, `neighbor_id`, `source_id`, `target_id`, `edge_type`, and `direction`.
- Neighbor, edge, source, and target IDs in traversal records come back as bytes. Decode them with `graph.key_to_string(...)` before comparing with external string IDs.
- `sample_size` larger than the available neighbors returns every neighbor, which makes small assertions deterministic.
- Sampling and lookup order is not guaranteed; compare ID sets instead of ordered lists.

## Snapshot/Engine Sampling

Use `SamplerSnapshot` and `SamplerEngine` for ML training pipelines. These APIs use compact integer IDs internally. Convert back with `snapshot.external_node_id(...)` or `snapshot.global_triple_to_external(...)`.

### Sampling Policy Map

- `sample_nodes` / `sample_edges`: seed draws, node-type/relation filtering, supported degree/weight strategies.
- `sample_neighbors`: batched uniform/weighted neighbors, direction/relation filters, per-seed offsets.
- `sample_layers`: independent fanout, direction, relation, replacement, and strategy per layer.
- `sample_random_walks`: uniform/weighted first-order walks, repeating metapaths, restarts and dead-end policies.
- `iter_random_walks`: lazy batches over all nodes (`start_nodes=None`) or explicit compact seeds, with `walks_per_node`, `batch_size`, optional round shuffling, and `walk_type="random"|"node2vec"`. Extra options go to the selected kernel. Explicit seeds are copied; global shuffling needs O(seed count) storage. Consume/discard batches and retain the snapshot lifetime. The same initial RNG state/options give batch-size-independent concatenated walks only without interleaved RNG users. Reuse `worker_engine` for independent streams; no automatic workers or partitioning.
- `sample_multihop` / `sample_subgraph`: multi-hop topology and edge-seeded batches with positives and negatives.
- `sample_hard_negatives`: endpoint-type/relation constraints, positive rejection, temporal policies.

For DeepWalk training inputs, import `iter_context_pairs` from `gestaltdb.sampling`.
It consumes walk batches and yields bounded center/context ID, distance and weight
arrays. Fixed/dynamic windows, inverse-distance weights and explicit frequency
subsampling are supported. Windows do not cross restart jumps by default and
never join separate walk rows. Embedding training stays in the caller's framework.

`ContextNegativeSampler` (same import module) draws context-node negatives from
explicit visit counts or `from_snapshot(snapshot)` incident degrees, raised to
an exponent (default 0.75). Exclude centers/positive contexts/caller-observed
contexts explicitly. Uniqueness exhaustion raises or explicitly repeats feasible
draws; no eligible support always raises. These are not KG triple negatives.

`WalkPolicy(non_backtracking=True, avoid_recent=k)` with
`engine.sample_policy_walks(seeds, length, policy=...)` adds hard avoidance.
Recent history includes the current node; k=1 excludes self-loops. Exhaustion
explicitly terminates/restarts/relaxes avoidance; other filters never relax.
Stream with `engine.iter_random_walks(walk_type="policy", policy=...)`.

`WalkSchema` adds target type constraints, cyclic relation metapaths, relation
weights, or bounded `WalkTransition` state-machine arcs with per-arc direction,
source/target types and weights. Unknown compact IDs fail closed. State/metapath
progress resets on restart. Overlapping arcs are distinct (arc, edge) choices;
global relation filters always intersect schema restrictions.

`WalkPolicy(chronology=TemporalWalkPolicy(...))` requires temporal snapshots and
enforces nondecreasing/nonincreasing chosen traversal times, optional maximum
gaps and explicit-reference recency weights. Interval-time and valid-start event
selection are distinct; backward interval selection requires an initial clock.
`TemporalWalkBatch` carries masked times, edge-version IDs and copied snapshot
provenance. Restarts reset the clock; ordinary point/window filters alone still
do not imply chronology. See `docs/random-walks.rst` for interval boundaries.

`WalkPolicy(transition_unit="neighbor")` samples distinct neighbors rather than
parallel edges. Weighted neighbor aggregation is explicit (`sum`/`mean`/`max`).
`node2vec=True` enables bias; `weighted_first_step=True` opts into static initial
weights. `bias_adjacency="eligible"` uses static filters/current chronology for
distance adjacency and fails closed for state-machine/metapath schemas; the
default `snapshot` scope preserves legacy unfiltered-distance semantics.

### Filter Once, Sample Repeatedly

Pass `node_filter`/`edge_filter` to `graph.build_sampler_snapshot` to define an
eligible property graph before sampling; for example,
`edge_filter=lambda e: e.properties.get("confidence", 0) >= 0.9`.
Callbacks receive decoded objects during a graph scan, not during array sampling
and not through automatic index pushdown. Mutable-snapshot edges with filtered
endpoints are omitted. Filtering only seed IDs does not restrict every hop.
Use `relations`/`direction` and temporal policies at draw time; arbitrary property
callbacks are not sampler-engine arguments. `node_type_property` defaults to
`kind`, not native labels; type-filtered seed draws use encoded type IDs.

Load one engine with `mode="ram"` or `mode="memmap"` and reuse it across batches;
draws need no open KV database. Keep compact IDs in hot paths and convert at
application boundaries. Memmap avoids eager array loading, not all memory use:
candidate/result arrays and positive-membership structures allocate memory.
Builds scan/materialize source data; snapshots do not reflect later graph writes.
Live reservoir sampling bounds sample storage by fanout but still scans adjacency.

High-level non-temporal builds use authenticated mutable-state snapshots automatically when available. Pass `read_snapshot=True` to require coherent source records plus verifiable provenance; LevelDB, default PyRex, and PyRex runtimes without a sequence identity fail that explicit request closed. Pass `False` only when an unpinned legacy build is acceptable.

Build temporal history through one authenticated source view with `graph.build_sampler_snapshot(path, temporal=True, system_time=known_at, time_bucket="day")`. Temporal format-v2 snapshots align edge-version IDs, validity arrays, open-end masks, and commit/system provenance with compact edge rows. Their endpoint/relation indexes are ordered by valid start. Loading validates the completion manifest, checksums, schemas, intervals, CSR bounds, and provenance in RAM and memmap modes. Legacy format-v1 snapshots remain readable without temporal or integrity guarantees.

New static snapshots persist sorted exact positive-membership triples; temporal engines reuse sorted history keys. Membership stays mapped in `mode="memmap"` instead of becoming Python sets/dictionaries, with O(log U) lookup. Legacy static snapshots build sorted RAM keys once; rebuild to persist them. Loading/authentication, weights, candidate buffers and page cache still allocate. Retain engine/snapshot ownership and never mutate or replace files under live readers.

Pass `temporal=TemporalContext.as_of(...)` or `TemporalContext.during(...)` to engine neighbor, multihop, and subgraph sampling. Exact half-open filtering runs before fanout. Windows support `window_policy="overlap"` or `"contained"`; multihop/subgraph traversal also supports `causal_policy="nondecreasing"` and `"nonincreasing"` valid-start ordering. Temporal results expose aligned `edge_version_ids`, `valid_from_us`, `valid_to_us`, and `valid_to_open` arrays. These options require a temporal format-v2 snapshot.

Temporal snapshots index positive-triple and node-availability intervals for leakage-safe hard negatives. Configure `HardNegativeConfig(temporal_positive_policy="at_positive_time", temporal_candidate_window_days=90)` for example-time rejection and a trailing candidate window. Policies are `"any_time"`, `"at_positive_time"`, and `"window"`; window membership supports overlap or containment. Future candidates require explicit `allow_future_candidates=True`, so direct temporal calls must otherwise provide `positive_times_us` or a `temporal` context. Temporal batches include aligned `positive_time_us`, `negative_time_us`, and deterministic rejection diagnostics. Exhaustion either raises or repeats an accepted negative according to `exhaustion_policy`.
- Resolve application IDs with `snapshot.node_int(...)`, `snapshot.edge_int(...)`, and `snapshot.relation_int(...)`. Their plural variants preserve input order.
- Methods ending in `_external` accept external node, edge, and relation IDs while retaining compact array-native results.
- Use `to_external(snapshot)` on neighbor, layered, and random-walk batches. Walk padding and restart edge/relation IDs become `None`.
- Temporal history may contain multiple rows for one logical edge ID; external edge lookup then raises an ambiguity error. Use compact edge rows instead.
- Layer and walk calls accept `temporal` and `window_policy`, but do not impose causal ordering across steps. Seed-node/edge draws cover the full snapshot, not a selected time. Use multihop/subgraph APIs for causal policies.

- `SampledSubgraphBatch.senders`, `receivers`, `positives`, and `negatives` use batch-local IDs; map through `node_ids_global` before resolving against the snapshot. Layer results are neighbor blocks, not framework-native bipartite blocks.

## `sample_neighbors` Example

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([
            Node("alice", labels=["User"], properties={"name": "Alice"}),
            Node("bob", labels=["User"], properties={"name": "Bob"}),
            Node("carol", labels=["User"], properties={"name": "Carol"}),
        ])
        graph.put_edges_bulk([
            Edge("e1", "alice", "bob", properties={"type": "FOLLOWS"}),
            Edge("e2", "alice", "carol", properties={"type": "FOLLOWS"}),
        ])
        result = graph.sample_neighbors("alice", "FOLLOWS", sample_size=10)
        assert {graph.key_to_string(record["neighbor_id"]) for record in result} == {"bob", "carol"}
        assert all(record["edge_type"] == "FOLLOWS" for record in result)
    finally:
        graph.close()
```

## Array-Native Weighted, Layered, and Walk Example

No backend extra is needed when constructing directly from compact arrays:

```python
from tempfile import TemporaryDirectory
from gestaltdb.sampling import ExternalNeighborSamplingSpec, SamplerEngine, SamplerSnapshot

with TemporaryDirectory() as tmpdir:
    snapshot = SamplerSnapshot.from_edge_arrays(
        f"{tmpdir}/sampler",
        external_node_ids=["a", "b", "c"],
        external_edge_ids=["ab", "ac"], external_relation_ids=["R"],
        src_int=[0, 0], dst_int=[1, 2], rel_int=[0, 0],
        edge_weights=[1.0, 3.0],
    )
    engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
    sampled = engine.sample_neighbors_external(["a"], 2, relations=["R"], strategy="weighted")
    assert set(sampled.to_external(engine.snapshot)["neighbor_nodes"]) == {"b", "c"}
    layers = engine.sample_layers_external(["a"], [ExternalNeighborSamplingSpec(2, relations=["R"])])
    walks = engine.sample_random_walks_external(["a"], 2, metapath=["R"])
    assert layers.layers[0].edge_indices.size == 2
    assert walks.lengths.tolist() == [1]
```

## Example

```python
import random
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([Node("drug"), Node("protein"), Node("disease")])
        graph.put_edges_bulk([
            Edge("e1", "drug", "protein", properties={"type": "TARGETS"}),
            Edge("e2", "protein", "disease", properties={"type": "ASSOCIATED_WITH"}),
        ])
        pattern = SamplingPattern([
            SamplingHop("TARGETS", direction="out", sample_size=2),
            SamplingHop("ASSOCIATED_WITH", direction="out", sample_size=2),
        ])
        paths = graph.sample_typed_paths(["drug"], pattern, rng=random.Random(7))
        subgraph = graph.sample_typed_subgraph(["drug"], pattern)
        assert paths
        assert set(subgraph["nodes"]) == {b"drug", b"protein", b"disease"}
    finally:
        graph.close()
```

## Filtered Property-Graph Snapshot Example

This quiescent LevelDB example selects a protein cohort through an index and
filters the entire snapshot, not just its starting seeds. For a required
verifiable mutable source snapshot, use a supported backend such as LMDB with
`read_snapshot=True`; do not assume LevelDB provides that guarantee.

```python
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.sampling import SamplerEngine

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(f"{tmpdir}/graph", backend="leveldb", serializer="json")
    try:
        graph.put_nodes([
            Node("drug", labels=["Drug"], properties={"approved": True}),
            Node("high", labels=["Protein"], properties={"score": 0.9}),
            Node("low", labels=["Protein"], properties={"score": 0.4}),
        ])
        graph.put_edges_bulk([
            Edge("strong", "drug", "high", properties={"type": "binds", "confidence": 0.95}),
            Edge("excluded", "drug", "low", properties={"type": "binds", "confidence": 0.99}),
        ])
        graph.create_node_property_index("score")
        proteins = {
            graph.key_to_string(key)
            for key in graph.iter_node_ids_by_label_property_range("Protein", "score", 0.8, None)
        }
        snapshot = graph.build_sampler_snapshot(
            f"{tmpdir}/sampler",
            node_filter=lambda n: n.get_id in proteins or (
                "Drug" in n.labels and n.properties.get("approved") is True
            ),
            edge_filter=lambda e: e.properties.get("confidence", 0) >= 0.9,
            edge_weight_property="confidence",
        )
    finally:
        graph.close()
    engine = SamplerEngine.load(snapshot.path, mode="memmap", seed=7)
    sampled = engine.sample_neighbors_external(["drug"], 10, relations=["binds"], strategy="weighted")
    assert sampled.to_external(engine.snapshot)["neighbor_nodes"].tolist() == ["high"]
```
