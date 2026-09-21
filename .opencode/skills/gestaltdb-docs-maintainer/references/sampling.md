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
| **Memory Options** | Key-value cache | RAM mode or lockless OS `memmap` mode |

---

## 2. Layer 1: Traversal Sampling on `GraphDB`

Operates directly over the live graph using typed adjacency indexes:

### Primitives
- `graph.neighbors_by_edge_type(node_id, edge_type, direction="out")`
- `graph.sample_neighbors(node_id, edge_type, sample_size=..., direction="out", rng=...)`
- `graph.sample_typed_paths(seed_node_ids, pattern, rng=...)`
- `graph.sample_typed_subgraph(seed_node_ids, pattern, rng=...)`

### Pattern Configuration
- `SamplingHop(edge_type="binds", direction="out", sample_size=5)`
- `SamplingPattern([hop1, hop2, ...])`

---

## 3. Layer 2: `SamplerSnapshot` & `SamplerEngine`

Engineered for zero-overhead GNN data loaders (PyTorch Geometric, DGL, TensorFlow GNN):

### Building a Snapshot
Build arrays directly from an active database handle:
- `snapshot = graph.build_sampler_snapshot(output_dir)` (or `SamplerSnapshot.build(graph, output_dir)`)
- `snapshot = graph.build_sampler_snapshot(output_dir, temporal=True, system_time=known_at, time_bucket="day")` captures temporal history through one authenticated system horizon.

This generates binary `.npy` CSR-style arrays (`row_ptr`, `col_idx`, `relations`, `edge_ids`, `features`, metadata).

Format v2 is atomically published with a completion manifest and a SHA-256/dtype/shape record for every array. The loader validates those records, CSR bounds, aligned lengths, interval invariants, and provenance before exposing RAM or memmap arrays. Format-v1 snapshots remain loadable without these guarantees.

Temporal snapshots expose `edge_version_ids`, `valid_from_us`, `valid_to_us`, `valid_to_open`, `edge_system_time_us`, and `edge_commit_ids`, all aligned with compact edge rows. `temporal_out` and `temporal_in` group rows by endpoint/relation and valid start. `temporal_candidates(...)` only prunes by start time and returns a superset; exact runtime temporal filtering is not available until TKG-08.

### Loading into Engine
- `SamplerEngine.load(snapshot_path, mode="ram", seed=42)`: Eagerly loads all arrays into process RAM.
- `SamplerEngine.load(snapshot_path, mode="memmap", seed=42)`: Memory-maps arrays via `numpy.memmap`. Ideal for multi-worker PyTorch DataLoader setups and datasets larger than RAM.

### Sampling Primitives
- `engine.sample_neighbors(seed_nodes, fanout=15, direction="out", relations=[rel_id])`
- `engine.sample_multihop(seed_nodes, fanouts=[15, 10], direction="out")`
- `engine.sample_subgraph(seed_edges, fanouts=[10, 5], negative_config=HardNegativeConfig(...))`

### Mapping IDs Back to External Graph
`SampledSubgraphBatch` local arrays index nodes locally within the batch (`senders`, `receivers`, `positives`, `negatives`).
- `batch.node_ids_global`: Maps local batch index to compact global snapshot ID.
- `snapshot.external_node_id(compact_id)`: Maps compact global snapshot ID to original external string ID.
- `snapshot.global_triple_to_external(head, rel, tail)`: Recovers full external triple tuple.

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
        drug_idx = snapshot.external_node_ids.tolist().index("drug-1")
        sample = engine.sample_neighbors([drug_idx], fanout=5, direction="out")

        # Convert sampled compact node IDs back to external IDs
        neighbors = [snapshot.external_node_id(idx) for idx in sample.neighbor_nodes]
        print("Sampled external neighbors:", neighbors)
    finally:
        graph.close()
```
