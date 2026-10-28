# Graph Traversal & Array-Native ML Sampling

**When to read:** Read this file when working on graph sampling, GNN / Knowledge Graph training pipelines, `SamplerSnapshot`, `SamplerEngine`, negative sampling, or batch generation.

---

## 1. Dual-Layer Sampling Architecture

GestaltDB contains two complementary sampling layers designed for different stages of the ML / graph processing lifecycle:

| Feature | Layer 1: In-Graph Traversal | Layer 2: Array-Native Snapshot |
| :--- | :--- | :--- |
| **API Entry Point** | `GraphDB` traversal methods | `SamplerSnapshot` + `SamplerEngine` |
| **Storage** | Live key-value store (`KVStore`) | Static immutable `.npy` arrays on disk |
| **Node/Edge IDs** | External IDs (strings/bytes) | Compact contiguous integers (`0..N-1`) |
| **Target Use Case** | Ad-hoc subgraphs, interactive Cypher, light feature extraction | High-throughput GNN mini-batching, KG embeddings |
| **Memory Options** | KV-backed traversal; reservoir storage bounded by fanout | RAM or `memmap` arrays; buffers/membership structures still use RAM |

---

## 2. Layer 1: Traversal Sampling on `GraphDB`

Operates directly over the live graph using typed adjacency indexes:

### Primitives
- `graph.neighbors_by_edge_type(node_id, edge_type, direction="out")`
- `graph.sample_neighbors(node_id, edge_type, sample_size=..., direction="out", rng=...)`
- `graph.sample_typed_paths(seed_ids, pattern, rng=...)`
- `graph.sample_typed_subgraph(seed_ids, pattern, rng=...)`

Live reservoir sampling scans eligible typed adjacency; bounded sample storage
does not imply degree-independent sampling time.

### Pattern Configuration
- `SamplingHop(edge_type="binds", direction="out", sample_size=5)`
- `SamplingPattern([hop1, hop2, ...])`

---

## 3. Layer 2: `SamplerSnapshot` & `SamplerEngine`

Designed to amortize snapshot construction over repeated GNN/KG batches, avoiding
KV reads and entity deserialization during sampling. Array outputs are not
automatically framework-native message-passing blocks.

### Building a Snapshot
Build arrays directly from an active database handle:
- `snapshot = graph.build_sampler_snapshot(output_dir)` (or `SamplerSnapshot.build(graph, output_dir)`)
- High-level non-temporal builds automatically pin a verifiable mutable backend snapshot where supported. Use `read_snapshot=True` to require this or `False` to opt out; direct `SamplerSnapshot.build` remains a lower-level unpinned API.
- `snapshot = graph.build_sampler_snapshot(output_dir, temporal=True, system_time=known_at, time_bucket="day")` captures temporal history through one authenticated system horizon.

This generates binary `.npy` endpoint, edge, relation, lookup, and CSR adjacency
arrays plus metadata. Builds scan/materialize source data; they are not a
streaming out-of-core pipeline.

For the existing inferred-session consumer, use explicit
`reasoning.adapters.authenticated_inferred_sampler` build/load APIs when source
admission is required. They require exact caller generation AND session-state
pins, canonical explicit node/edge predicate selection, and a new artifact path.
Hydration independently reconstructs every artifact, not just metadata; it is
expensive admission, not closure proof. The legacy `build_inferred_snapshot`
partial mapping checks remain unverified. See `docs/reasoning-authenticated-inferred.rst`.

### Property-Graph Filtering Before Sampling

- Select record/seed cohorts through explicit property indexes and label/type composite exact/range helpers (see `indexing.md`). ID iterators avoid hydration during candidate lookup.
- Filtering only seeds does not constrain each sampled hop. Use `graph.build_sampler_snapshot(..., node_filter=..., edge_filter=...)` to define the eligible graph, and persist weights with `edge_weight_property`.
- Mutable-snapshot callbacks receive decoded nodes/edges during a full source scan; excluded endpoints remove incident edges. Callback membership checks are not automatic index pushdown.
- Runtime arrays accept relation/direction/type/temporal policies, not arbitrary property callbacks. `node_type_property` defaults to `kind`, not labels.
- Cohorts selected before a build are not automatically pinned to the build horizon. Quiesce writers or evaluate property predicates in a pinned build.
- See `docs/property-filtering.rst` for the complete workflow and `docs/performance.rst` for benchmark scope and memory trade-offs.

Format v2 is atomically published with a completion manifest and a SHA-256/dtype/shape record for every array. The loader validates those records, CSR bounds, aligned lengths, interval invariants, and provenance before exposing RAM or memmap arrays. Format-v1 snapshots remain loadable without these guarantees.

New v2 builds use `relation_adjacency_encoding="sparse-rows-v1"`: relation and temporal directories persist sorted populated `row_keys` plus offsets. Call `edge_range(key)`; `indptr` no longer addresses every possible node/relation pair. RAM/memmap loads keep the sparse layout and still accept legacy dense snapshots. Old loaders reject the new artifact catalogs. Dense node-only adjacency is unchanged; positive membership uses the separate sorted index described below.

Temporal snapshots expose `edge_version_ids`, `valid_from_us`, `valid_to_us`, `valid_to_open`, `edge_system_time_us`, and `edge_commit_ids`, all aligned with compact edge rows. `temporal_out` and `temporal_in` group rows by endpoint/relation and valid start. `SamplerEngine` uses those indexes and then applies exact half-open point/window predicates before random selection. Pass `temporal=TemporalContext.as_of(...)` or `TemporalContext.during(...)`; windows support `window_policy="overlap"` and `"contained"`, and multihop/subgraph calls support `causal_policy="none"`, `"nondecreasing"`, or `"nonincreasing"`. Temporal neighbor and batch results carry aligned edge-version and validity arrays.

Temporal v2 snapshots also authenticate grouped positive-triple interval histories and node-availability intervals. `HardNegativeConfig.temporal_positive_policy` accepts `"any_time"`, `"at_positive_time"`, or `"window"`; window membership supports `temporal_positive_window_policy="overlap"` or `"contained"`. `temporal_candidate_window_days` selects a trailing availability window ending at each example time. Future candidates remain unavailable unless `allow_future_candidates=True`. Exhaustion is deterministic: `exhaustion_policy="raise"` fails or `"repeat"` reuses an accepted negative. Direct temporal sampling requires aligned `positive_times_us` or a `temporal` context unless future candidates are explicitly allowed, and accepts `return_diagnostics=True`; temporal subgraph batches expose `positive_time_us`, `negative_time_us`, and `negative_diagnostics`.

### Loading into Engine
- `SamplerEngine.load(snapshot_path, mode="ram", seed=42)`: Eagerly loads all arrays into process RAM.
- `SamplerEngine.load(snapshot_path, mode="memmap", seed=42)`: Memory-maps persisted arrays. New static membership uses sorted `positive_membership_triples`; temporal membership reuses sorted history keys, without Python code sets/group dictionaries. Legacy static snapshots build sorted RAM keys once. Candidate/result buffers, validation, metadata and page cache still consume memory; benchmark full-process memory and worker behavior.

New v2 snapshots mark membership as `positive_membership_encoding="lexicographic-triples-v1"`. Static key artifacts are authenticated sorted unique int64 triples; temporal history groups retain their old identities and interval policies. Engine membership uses exact O(log U) search, not hashing or uint64 packing. Keys borrow snapshot lifetime; do not mutate arrays or replace files under active engines. New static artifacts require an upgraded loader; legacy snapshots still load with the documented compatibility path.

### Sampling Primitives
- `engine.sample_nodes(512, node_types=[type_id])`
- `engine.sample_edges(128, relations=[rel_id], strategy="weighted")`
- `engine.sample_neighbors(seed_nodes, fanout=15, direction="out", relations=[rel_id])`
- `engine.sample_neighbors(seed_nodes, fanout=15, strategy="weighted")`
- `engine.sample_layers(seed_nodes, [NeighborSamplingSpec(15, relations=[rel_id]), ...])`
- `engine.sample_random_walks(seed_nodes, 20, metapath=[rel_a, rel_b])`
- `engine.sample_multihop(seed_nodes, fanouts=[15, 10], direction="out")`
- `engine.sample_subgraph(seed_edges, fanouts=[10, 5], negative_config=HardNegativeConfig(...))`

`SamplerSnapshot.from_edge_arrays(..., edge_weights=weights)` persists edge
weights directly. For graph-backed snapshots, use
`graph.build_sampler_snapshot(..., edge_weight_property="confidence")`. Missing
weights default to `1.0`. Weighted sampling excludes zero-weight candidates and
rejects candidate sets in which every weight is zero.

`sample_random_walks` returns fixed-width compact node, edge, and relation arrays.
Unused positions are `-1`. Random restarts and dead-end restarts are explicit
transitions with edge and relation IDs set to `-1`, so consumers can distinguish
teleports from graph edges. Metapath relations repeat cyclically and advance only
when an edge is traversed.

`engine.iter_random_walks(start_nodes=None, walk_length=40, walks_per_node=1,
batch_size=1024, shuffle=False, walk_type="random", **walk_options)` streams
ordinary walk batches in seed rounds (RWS-01). `None` visits all nodes; explicit
compact cohorts retain duplicates and are copied once. Use `walk_type="node2vec"`
to forward options to the biased kernel. Validation is eager, generation lazy.
Unshuffled all-node seed buffers are batch-sized; shuffling needs an O(seed count)
permutation. Output memory is O(batch size × walk length), plus kernel candidate
buffers. Consume/discard batches and keep the snapshot alive. Given the same
initial RNG state and no interleaved RNG operations, changing batch size preserves
concatenated walks. Reuse `worker_engine` for independent streams; no automatic
parallelism, cohort partitioning, or worker-count invariance is implied.

### Mapping IDs Back to External Graph

`gestaltdb.sampling.iter_context_pairs` (RWS-02) converts a walk batch or stream
to bounded `ContextPairBatch` arrays: center/context IDs, distances and weights.
Fixed/dynamic windows, inverse-distance weights and explicit frequency-based
subsampling are supported. Restart jumps split sequences by default; separate
walk rows never join. Full pipeline contracts: `docs/random-walks.rst`.

`ContextNegativeSampler` supplies corpus-count/degree-power context negatives
with explicit exclusions and uniqueness/exhaustion policies (not KG negatives).
`engine.sample_policy_walks` and streamed `walk_type="policy"` compose hard
avoidance, typed/metapath/FSM `WalkSchema`/`WalkTransition`, interval/event
`TemporalWalkPolicy`, and edge/distinct-neighbor transition units. Legacy kernels
and batch defaults remain unchanged; temporal policy batches add masked times,
version IDs and copied snapshot provenance. RWS-08 cache/native adoption was
measured and deferred; prototypes stay under `benchmarks/`, not runtime APIs.

`SampledSubgraphBatch` local arrays index nodes locally within the batch (`senders`, `receivers`, `positives`, `negatives`).
- `batch.node_ids_global`: Maps local batch index to compact global snapshot ID.
- `snapshot.external_node_id(compact_id)`: Maps compact global snapshot ID to original external string ID.
- `snapshot.global_triple_to_external(head, rel, tail)`: Recovers full external triple tuple.

### Resolving External IDs

- `snapshot.node_int("drug-1")`, `snapshot.edge_int("edge-1")`, and `snapshot.relation_int("binds")` resolve one external ID.
- `node_ints`, `edge_ints`, and `relation_ints` resolve batches while preserving input order and report all missing IDs together.
- `sample_neighbors_external`, `sample_layers_external`, `sample_random_walks_external`, and `sample_subgraph_external` accept external IDs and return the same compact batch types as the hot-path APIs.
- `SampledNeighbors`, `LayeredSampleBatch`, and `RandomWalkBatch` expose `to_external(snapshot)` helpers. Padding and restart edge/relation IDs become `None` rather than indexing the final external ID accidentally.

Lookup indexes persist sorted external-ID arrays and aligned compact IDs. Both
arrays can remain memory mapped, so batch resolution uses vectorized binary
search without a full Python dictionary. Older snapshots derive these indexes
when loaded and should be rebuilt when repeated large-batch resolution matters.

Temporal history may contain several rows for one external edge ID; external
edge lookup rejects this ambiguity instead of silently choosing a version.
Layer and walk calls accept point/window filters but do not impose causal
ordering. Node/edge seed draws select from the entire snapshot, including all
history rows; use time-filtered traversal or subgraph APIs for temporal selection.

---

## 4. Runnable Examples

### Traversal Sampling (Layer 1)
```python
import random
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        graph.put_node(Node("drug-1", properties={"name": "Imatinib"}))
        graph.put_node(Node("protein-1", properties={"name": "ABL1"}))
        graph.put_node(Node("disease-1", properties={"name": "Leukemia"}))
        graph.put_edge(Edge("e1", "drug-1", "protein-1", properties={"type": "targets"}))
        graph.put_edge(Edge("e2", "protein-1", "disease-1", properties={"type": "associated_with"}))

        pattern = SamplingPattern([
            SamplingHop("targets", direction="out", sample_size=5),
            SamplingHop("associated_with", direction="out", sample_size=5),
        ])
        subgraph = graph.sample_typed_subgraph(["drug-1"], pattern)
        print("Sampled node IDs:", list(subgraph["nodes"].keys()))
    finally:
        graph.close()
```

### Snapshot Engine & Negative Sampling (Layer 2)
```python
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.sampling import HardNegativeConfig, SamplerEngine
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([
            Node("drug-1"),
            Node("prot-1"),
            Node("prot-2"),
        ])
        graph.put_edges_bulk([
            Edge("e1", "drug-1", "prot-1", properties={"type": "targets"}),
            Edge("e2", "drug-1", "prot-2", properties={"type": "targets"}),
        ])

        # Build snapshot
        snapshot = graph.build_sampler_snapshot(f"{tmpdir}/snapshot")

        # Load engine in RAM mode
        engine = SamplerEngine.load(snapshot.path, mode="ram", seed=42)

        # Sample 1-hop neighbors
        drug_idx = snapshot.node_int("drug-1")
        sample = engine.sample_neighbors([drug_idx], fanout=5, direction="out")

        # Convert sampled compact node IDs back to external IDs
        neighbors = [snapshot.external_node_id(idx) for idx in sample.neighbor_nodes]
        print("Sampled external neighbors:", neighbors)
    finally:
        graph.close()
```
