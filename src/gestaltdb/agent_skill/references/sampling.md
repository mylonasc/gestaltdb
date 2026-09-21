# Sampling For Library Users

GestaltDB has two sampling layers.

## GraphDB Traversal Sampling

Use this for application code and external node IDs:

- `sample_neighbors`
- `sample_typed_paths`
- `sample_typed_subgraph`
- `SamplingHop`
- `SamplingPattern`

## Snapshot/Engine Sampling

Use `SamplerSnapshot` and `SamplerEngine` for ML training pipelines. These APIs use compact integer IDs internally. Convert back with `snapshot.external_node_id(...)` or `snapshot.global_triple_to_external(...)`.

Build temporal history through one authenticated source view with `graph.build_sampler_snapshot(path, temporal=True, system_time=known_at, time_bucket="day")`. Temporal format-v2 snapshots align edge-version IDs, validity arrays, open-end masks, and commit/system provenance with compact edge rows. Their endpoint/relation indexes are ordered by valid start. Loading validates the completion manifest, checksums, schemas, intervals, CSR bounds, and provenance in RAM and memmap modes. Legacy format-v1 snapshots remain readable without temporal or integrity guarantees.

Pass `temporal=TemporalContext.as_of(...)` or `TemporalContext.during(...)` to engine neighbor, multihop, and subgraph sampling. Exact half-open filtering runs before fanout. Windows support `window_policy="overlap"` or `"contained"`; multihop/subgraph traversal also supports `causal_policy="nondecreasing"` and `"nonincreasing"` valid-start ordering. Temporal results expose aligned `edge_version_ids`, `valid_from_us`, `valid_to_us`, and `valid_to_open` arrays. These options require a temporal format-v2 snapshot.

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
