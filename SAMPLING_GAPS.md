---
date: 2026-09-14
commit: b549e5bef4d73ae45e975dad60b08ad56de40613
model: GPT-5.6 Sol
status: baseline-analysis
---

# Sampling Gaps and Implementation Proposal

This note describes the repository at the baseline commit in the front matter.
Some gaps may be addressed by later commits on the implementation branch.

## Current Architecture

GestaltDB has two complementary sampling layers:

- Live `GraphDB` sampling streams typed adjacency from the active KV store and
  works with external IDs. It supports uniform reservoir neighbor sampling,
  typed paths, and hydrated typed subgraphs.
- `SamplerSnapshot` and `SamplerEngine` build immutable compact NumPy arrays and
  CSR indexes for repeated ML workloads. They support uniform neighbor fanout,
  merged multihop sampling, link-centered subgraphs, exact positive membership,
  and hard negatives.

This split is appropriate. Live KV sampling should favor current, occasional
traversals that tolerate sequential scans. Snapshot sampling should handle
repeated workloads that benefit from random access and preprocessing.

## Current Gaps

### Live KV Sampling

- Uniform reservoir sampling reads the complete matching adjacency range, so a
  sample of size `k` from degree `d` costs `O(d)`.
- There is no random node or edge seed sampler, including label-, relation-, or
  property-stratified cohorts.
- Parallel edges weight neighbors by multiplicity; unique-neighbor sampling is
  not available.
- Typed paths can grow as the product of per-hop fanouts and repeatedly scan
  convergent frontier nodes.
- There are no random walks, restart walks, weighted transitions, temporal
  constraints, cycle policies, or global output budgets.
- Property-aware sampling requires whole edge or node hydration because
  properties are not projected into adjacency records.
- The KV API has no count-plus-rank primitive, so exact global uniform sampling
  cannot avoid a full scan without a derived index.

### Snapshot and Engine

- Every multihop layer shares one direction and relation filter; heterogeneous
  per-layer policies are not supported.
- `sample_multihop` merges all layers and loses the bipartite block boundaries
  required by layer-wise GNN execution.
- Neighbor sampling is uniform only. Edge weights and sampling probabilities are
  not represented.
- Callers must provide compact seed IDs; there are no uniform, stratified,
  degree-biased, or user-weighted node and edge seed samplers.
- There are no first-order, restart, metapath, weighted, or node2vec walks.
- There are no temporal arrays or causal neighborhood samplers.
- There are no GraphSAINT, bounded induced k-hop, layer-wise importance, or
  partition samplers.
- Hard negatives lack degree-biased pools, in-batch candidates, relation
  corruption, and reusable type-specific candidate arrays.
- The context-negative candidate cache is keyed too broadly and can reuse a
  candidate list derived for different positive endpoints.
- A node discovered through multiple parents can occur repeatedly in the next
  multihop frontier and be sampled more than once.
- Public methods do not consistently validate compact node, edge, and relation
  IDs; negative NumPy indices may silently select unintended records.

### Snapshot Scalability and Integrity

- Relation-specific CSR pointers require `O(num_nodes * num_relations)` storage,
  even when most node/relation pairs are absent.
- Memmap mode still builds a Python set containing every positive triple code,
  weakening its memory-scaling benefit.
- Snapshot writes are not atomic and loading checks only the format version.
- There are no shape, range, CSR monotonicity, checksum, source-version, or
  staleness checks.
- Snapshot options for storage, reverse relations, and directed behavior are
  partly metadata rather than fully implemented behavior.
- RNG state belongs to one mutable engine and has no worker stream-splitting
  contract.

### Batches, Adapters, and Tests

- Existing optional graph component offsets are not populated.
- Framework adapters are lightweight dictionaries or homogeneous graphs rather
  than true heterogeneous framework-native batches.
- There is no JAX, DLPack, device-side, process-worker, or partitioned sampling
  path.
- Coverage is thin for replacement sampling, incoming/incident traversal,
  self-loops, parallel edges, malformed snapshots, deterministic replay,
  context negatives, successful adapters, and realistic memory/performance
  behavior.

## Recommended Techniques

| Priority | Technique | Benefit | Layer |
| --- | --- | --- | --- |
| 1 | Per-layer heterogeneous neighbor sampling | Independent fanout, direction, and relations per layer | Snapshot |
| 1 | Uniform and stratified seed sampling | Node, edge, type, and relation minibatches | Both |
| 1 | Weighted neighbor sampling | Confidence-, frequency-, or importance-biased traversal | Snapshot |
| 2 | Random and metapath walks | Embeddings, recommendation, and graph exploration | Both |
| 2 | Temporal neighbor sampling | Causal GNN and dynamic graph workloads | Snapshot |
| 2 | Degree-aware and in-batch negatives | Stronger KG and link-prediction training | Snapshot |
| 3 | GraphSAINT-style subgraphs | Well-connected fixed-budget minibatches | Snapshot |
| 3 | Bounded induced k-hop sampling | Stable local receptive fields | Snapshot |
| 4 | Node2vec second-order walks | BFS/DFS-biased structural embeddings | Snapshot |
| 4 | Cluster and layer-wise importance sampling | Deep large-graph GNN training | Snapshot |

## Proposed API Direction

### Seeds

Add `SamplerEngine.sample_nodes` and `SamplerEngine.sample_edges` with uniform,
type/relation-stratified, degree-based, and caller-supplied weights. Live
`GraphDB` equivalents can reservoir-sample global or indexed cohorts while
documenting their linear scan cost.

### Weighted Neighbors

Allow snapshots to persist an optional edge-weight array populated from either
`SamplerSnapshot.from_edge_arrays(edge_weights=...)` or
`SamplerSnapshot.build(edge_weight_property=...)`. Extend `sample_neighbors`
with a weighted strategy and optional sampling-probability output. Start with
NumPy selection; add alias tables only if benchmarks demonstrate repeated
replacement sampling warrants their storage cost.

### Heterogeneous Layers

Introduce an immutable `NeighborSamplingSpec` containing fanout, direction,
relation IDs, replacement policy, and sampling strategy. Add `sample_layers`
and return a `LayeredSampleBatch` whose blocks preserve source nodes,
destination nodes, sampled edges, relations, offsets, and probabilities for
each layer.

### Walks

Add fixed-width walk output for uniform, restart, relation-filtered, metapath,
and weighted walks, with explicit dead-end behavior. Implement node2vec later;
efficient second-order transitions need a neighbor-membership structure not
currently present in either storage layout.

### Temporal Sampling

Optionally persist edge timestamps and temporal CSR indexes sorted by node,
relation, and timestamp. Use binary search to select edges before per-seed
cutoffs, then support uniform, recent, recency-weighted, and fixed-window
strategies with causal timestamp propagation across hops.

### Subgraphs and Negatives

Add bounded induced k-hop sampling before full GraphSAINT. GraphSAINT node,
edge, and walk variants must return sampling probabilities and node/edge
normalization coefficients. Extend hard negatives with precomputed typed pools,
degree-biased and in-batch candidates, while keeping model-scored
self-adversarial selection outside the database core.

## Implementation Order

1. Validate compact IDs, deduplicate multihop frontiers, fix negative cache
   scoping, and make RNG behavior reproducible for worker-specific streams.
2. Implement uniform/stratified node and edge seeds, optional edge weights,
   weighted neighbors, per-layer specifications, and layer-preserving batches.
3. Implement uniform, restart, relation-filtered, and metapath random walks.
4. Replace dense relation CSR directories and RAM-only positive membership with
   sparse/memmappable structures, then harden snapshot persistence.
5. Add temporal arrays and causal sampling.
6. Add bounded induced subgraphs, GraphSAINT normalization, and optional
   externally supplied graph partitions.
7. Consider node2vec, FastGCN, and LADIES only after workload benchmarks justify
   their extra indexes and model-specific output contracts.

## Verification Requirements

- Deterministic replay for identical seeds and worker streams.
- Structural tests for every direction, relation policy, layer, and walk mode.
- Statistical tests with fixed seeds and broad non-flaky bounds for uniform and
  weighted selection.
- Explicit self-loop, parallel-edge, empty-graph, invalid-ID, and dead-end tests.
- Snapshot round-trip, corruption, compatibility, and memmap tests.
- Benchmarks for high-degree latency, samples per second, snapshot size/build
  time, peak RSS, walk steps, negative rejection rate, and multiworker scaling.

## Research Basis

- GraphSAGE: fixed-fanout inductive neighborhood sampling.
- node2vec: second-order BFS/DFS-biased random walks.
- PinSAGE: importance neighborhoods derived from random walks.
- FastGCN and LADIES: layer-wise importance sampling.
- GraphSAINT: normalized graph-level minibatch sampling.
- Cluster-GCN: partition-based minibatches.
- TGAT and TGN: causal temporal neighborhood sampling.
- RotatE: self-adversarial negative sampling for knowledge graphs.
