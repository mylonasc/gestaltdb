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

- Resolve application IDs with `snapshot.node_int(...)`, `snapshot.edge_int(...)`, and `snapshot.relation_int(...)`. Their plural variants preserve input order.
- Methods ending in `_external` accept external node, edge, and relation IDs while retaining compact array-native results.
- Use `to_external(snapshot)` on neighbor, layered, and random-walk batches. Walk padding and restart edge/relation IDs become `None`.

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
