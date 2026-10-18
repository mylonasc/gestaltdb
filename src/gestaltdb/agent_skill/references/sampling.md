# Sampling For Library Users

GestaltDB has two sampling layers.

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

High-level non-temporal builds use authenticated mutable-state snapshots automatically when available. Pass `read_snapshot=True` to require coherent source records plus verifiable provenance; LevelDB, default PyRex, and PyRex runtimes without a sequence identity fail that explicit request closed. Pass `False` only when an unpinned legacy build is acceptable.

Build temporal history through one authenticated source view with `graph.build_sampler_snapshot(path, temporal=True, system_time=known_at, time_bucket="day")`. Temporal format-v2 snapshots align edge-version IDs, validity arrays, open-end masks, and commit/system provenance with compact edge rows. Their endpoint/relation indexes are ordered by valid start. Loading validates the completion manifest, checksums, schemas, intervals, CSR bounds, and provenance in RAM and memmap modes. Legacy format-v1 snapshots remain readable without temporal or integrity guarantees.

Pass `temporal=TemporalContext.as_of(...)` or `TemporalContext.during(...)` to engine neighbor, multihop, and subgraph sampling. Exact half-open filtering runs before fanout. Windows support `window_policy="overlap"` or `"contained"`; multihop/subgraph traversal also supports `causal_policy="nondecreasing"` and `"nonincreasing"` valid-start ordering. Temporal results expose aligned `edge_version_ids`, `valid_from_us`, `valid_to_us`, and `valid_to_open` arrays. These options require a temporal format-v2 snapshot.

Temporal snapshots index positive-triple and node-availability intervals for leakage-safe hard negatives. Configure `HardNegativeConfig(temporal_positive_policy="at_positive_time", temporal_candidate_window_days=90)` for example-time rejection and a trailing candidate window. Policies are `"any_time"`, `"at_positive_time"`, and `"window"`; window membership supports overlap or containment. Future candidates require explicit `allow_future_candidates=True`, so direct temporal calls must otherwise provide `positive_times_us` or a `temporal` context. Temporal batches include aligned `positive_time_us`, `negative_time_us`, and deterministic rejection diagnostics. Exhaustion either raises or repeats an accepted negative according to `exhaustion_policy`.
- Resolve application IDs with `snapshot.node_int(...)`, `snapshot.edge_int(...)`, and `snapshot.relation_int(...)`. Their plural variants preserve input order.
- Methods ending in `_external` accept external node, edge, and relation IDs while retaining compact array-native results.
- Use `to_external(snapshot)` on neighbor, layered, and random-walk batches. Walk padding and restart edge/relation IDs become `None`.
- Temporal history may contain multiple rows for one logical edge ID; external edge lookup then raises an ambiguity error. Use compact edge rows instead.
- Layer and walk calls accept `temporal` and `window_policy`, but do not impose causal ordering across steps. Seed-node/edge draws cover the full snapshot, not a selected time. Use multihop/subgraph APIs for causal policies.

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
