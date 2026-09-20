# GestaltDB Examples

These examples are designed for developers and agents who need working patterns quickly. They use explicit imports from submodules because the package root intentionally exports only selected helpers.

## Create, Query, and Close a Graph

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge(
            edge_id="alice-knows-bob",
            source="alice",
            target="bob",
            properties={"type": "knows", "since": 2024},
        ))

        result = graph.query(
            'MATCH (a:Person {name: "Alice"}) '
            'MATCH (a)-[:knows]->(b) '
            'RETURN a.id AS source, b.name AS target'
        )
        print(result.columns)
        print(result.records)
    finally:
        graph.close()
```

## Use a Self-Describing Store

`GraphDB.create` writes a manifest next to the database. `GraphDB.open` uses that manifest to choose the backend and serializer.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(
        f"{tmpdir}/graph",
        backend="leveldb",
        serializer="json",
        indexed_node_properties=["kind"],
    )
    try:
        graph.put_node(Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}))
        graph.put_node(Node(node_id="protein-1", labels=["Protein"], properties={"kind": "protein"}))
        graph.put_edge(Edge(edge_id="e1", source="drug-1", target="protein-1", properties={"type": "binds"}))
    finally:
        graph.close()

    reopened = GraphDB.open(f"{tmpdir}/graph")
    try:
        print(reopened.get_node(b"drug-1").properties["kind"])
        print(reopened.manifest["serializer"]["name"])
    finally:
        reopened.close()
```

## Choose a Backend and Serializer

```python
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer

graph_lmdb = GraphDB(LMDBStore(path="graph_lmdb", map_size=2**30), PickleSerializer())
graph_leveldb = GraphDB(LevelDBStore(path="graph_leveldb"), PickleSerializer())
graph_rocksdb = GraphDB(PyRexStore(path="graph_rocksdb"), JSONSerializer())

graph_lmdb.close()
graph_leveldb.close()
graph_rocksdb.close()
```

Use `LevelDBStore` for small local examples, `LMDBStore` when a fixed LMDB map is appropriate, and `PyRexStore` for RocksDB-backed bulk ingestion.

## Index and Lookup Properties

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([
            Node(node_id="drug-1", labels=["Drug"], properties={"name": "Aspirin", "score": 0.9}),
            Node(node_id="drug-2", labels=["Drug"], properties={"name": "Ibuprofen", "score": 0.7}),
        ])
        graph.put_edges_bulk([
            Edge(edge_id="e1", source="drug-1", target="drug-2", properties={"type": "similar", "score": 0.8}),
        ])

        graph.create_node_property_index("name")
        graph.create_node_property_index("score")
        graph.create_edge_property_index("score")

        print([node.get_id for node in graph.nodes_by_label("Drug")])
        print([node.get_id for node in graph.nodes_by_property("name", "Aspirin")])
        print([node.get_id for node in graph.nodes_by_property_range("score", 0.8, None)])
        print([edge.get_id for edge in graph.edges_by_type_property_range("similar", "score", 0.75, None)])
    finally:
        graph.close()
```

## Read and Write with Cypher

```python
result = graph.query(
    'MATCH (d:Drug) '
    'WHERE d.score >= $minimum '
    'RETURN d.id AS id, d.name AS name '
    'ORDER BY name',
    parameters={"minimum": 0.8},
)

for record in result:
    print(record["id"], record["name"])
```

Cypher covers indexed scans, typed traversal, read composition, paths,
aggregation, and writes. Writes use backend transactions when available:

```python
result = graph.query(
    'MATCH (p:Person) '
    'WITH p ORDER BY p.age DESC LIMIT 10 '
    'MATCH (p)-[:member_of]->(t:Team) '
    'RETURN p.name AS name, t.name AS team'
)
```

```python
totals = graph.query(
    'MATCH (p:Person) '
    'RETURN p.department AS department, count(*) AS total '
    'ORDER BY total DESC'
)
```

```python
bands = graph.query(
    'MATCH (p:Person) '
    'RETURN p.name AS name, '
    'CASE WHEN p.age >= 35 THEN "senior" ELSE "junior" END AS band, '
    '[t IN p.tags WHERE t STARTS WITH "a" | t] AS tags'
)
```

```python
created = graph.query(
    'MERGE (a:Person {id: "alice"}) ON CREATE SET a.name = "Alice" '
    'MERGE (b:Person {id: "bob"}) ON CREATE SET b.name = "Bob" '
    'MERGE (a)-[r:KNOWS {id: "alice-knows-bob"}]->(b) '
    'ON CREATE SET r.since = 2024 '
    'RETURN a.name AS source, b.name AS target, r.since AS since'
)
assert created.records == [{"source": "Alice", "target": "Bob", "since": 2024}]

paths = graph.query(
    'MATCH p = (a:Person {id: "alice"})-[:KNOWS*1..2]->(friend) '
    'OPTIONAL MATCH (friend)-[:KNOWS]->(other) '
    'RETURN friend.name AS friend, length(p) AS hops, other.name AS other '
    'ORDER BY friend'
)
```

Inspect schema metadata and call the registered sampling procedure:

```python
graph.create_node_property_index("name")
graph.create_edge_property_index("since")
assert graph.query("SHOW INDEXES").columns == ("entityType", "properties")

sampled = graph.query(
    'CALL pg.sample_typed_paths($seeds, $pattern) '
    'YIELD path AS sampled RETURN sampled LIMIT 1',
    parameters={
        "seeds": ["alice"],
        "pattern": [{"edge_type": "KNOWS", "direction": "out", "sample_size": 2}],
    },
)
```

## Traverse and Sample Typed Relationships

```python
import random

from gestaltdb.sampling import SamplingHop, SamplingPattern

neighbors = graph.neighbors_by_edge_type("drug-1", "binds", direction="out")
edges = graph.edges_by_edge_type("drug-1", "binds", direction="out")

one_hop = graph.sample_neighbors(
    "drug-1",
    "binds",
    direction="out",
    sample_size=5,
    rng=random.Random(7),
)

pattern = SamplingPattern([
    SamplingHop("binds", direction="out", sample_size=5),
    SamplingHop("associated_with", direction="out", sample_size=3),
])
paths = graph.sample_typed_paths(["drug-1"], pattern, rng=random.Random(11))
subgraph = graph.sample_typed_subgraph(["drug-1"], pattern)

print(neighbors, edges, one_hop)
print(subgraph["nodes"].keys(), subgraph["edges"].keys(), paths)
```

Typed traversal depends on `Edge(properties={"type": "..."})`. If a database was populated before typed adjacency existed, run `graph.rebuild_typed_adjacency()`.

## Ingest Arrow Columns

```python
from tempfile import TemporaryDirectory

import pyarrow as pa

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        graph.create_node_property_index("name")
        result = graph.ingest_arrow(
            pa.array(["alice", "bob", "carol"]),
            pa.array(["alice-knows-bob", "bob-knows-carol"]),
            pa.array(["alice", "bob"]),
            pa.array(["bob", "carol"]),
            pa.array(["knows", "knows"]),
            labels=pa.array([["Person"], ["Person"], ["Person"]]),
            node_properties={"name": pa.array(["Alice", "Bob", "Carol"])},
            edge_properties={"since": pa.array([2024, 2025])},
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )
        print(result)
    finally:
        graph.close()
```

## Ingest Polars DataFrames

```python
from tempfile import TemporaryDirectory

import polars as pl

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

nodes = pl.DataFrame({
    "node_id": ["alice", "bob", "carol"],
    "labels": [["Person"], ["Person"], ["Person"]],
    "name": ["Alice", "Bob", "Carol"],
    "age": [34, 36, 29],
})

edges = pl.DataFrame({
    "edge_id": ["alice-knows-bob", "bob-knows-carol"],
    "source": ["alice", "bob"],
    "target": ["bob", "carol"],
    "edge_type": ["knows", "knows"],
    "since": [2024, 2025],
})

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        graph.create_node_property_index("name")
        graph.ingest_polars(
            nodes,
            edges,
            node_property_columns=["name", "age"],
            edge_property_columns=["since"],
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )

        result = graph.query(
            'MATCH (a:Person) MATCH (a)-[:knows]->(b) RETURN a.name, b.name ORDER BY a.name'
        )
        print(result.records)
    finally:
        graph.close()
```

## Build and Use a Sampler Snapshot

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
            Node(node_id="drug-1", properties={"kind": "drug"}),
            Node(node_id="protein-1", properties={"kind": "protein"}),
            Node(node_id="disease-1", properties={"kind": "disease"}),
        ])
        graph.put_edges_bulk([
            Edge(edge_id="d1-p1", source="drug-1", target="protein-1", properties={"type": "binds"}),
            Edge(edge_id="p1-dis1", source="protein-1", target="disease-1", properties={"type": "associated_with"}),
        ])

        snapshot = graph.build_sampler_snapshot(f"{tmpdir}/sampler")
        engine = SamplerEngine.load(snapshot.path, mode="ram", seed=13)

        drug_id = snapshot.external_node_ids.tolist().index("drug-1")
        binds_id = snapshot.external_relation_ids.tolist().index("binds")
        sample = engine.sample_neighbors([drug_id], fanout=10, direction="out", relations=[binds_id])
        print(snapshot.external_node_ids[sample.neighbor_nodes].tolist())

        seed_edge = snapshot.external_edge_ids.tolist().index("d1-p1")
        batch = engine.sample_subgraph(
            [seed_edge],
            fanouts=[10, 5],
            negative_config=HardNegativeConfig(negatives_per_positive=2),
        )
        arrays = batch.to_numpy()
        print(arrays["senders"], arrays["receivers"], arrays["positives"], arrays["negatives"])
    finally:
        graph.close()
```

Use `SamplerEngine.load(path, mode="memmap")` for large snapshots that should be memory mapped instead of eagerly loaded into RAM.

## Visualize Graphs

GestaltDB ships offline interactive visualization: the D3.js + React front
end is prebuilt and packaged with the library, so saving `.html` artifacts
or rendering inline in Jupyter needs no JavaScript toolchain or network.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.api import VizOptions, visualize_query

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge(edge_id="e1", source="alice", target="bob", properties={"type": "knows"}))

        figure = visualize_query(
            graph,
            'MATCH (a:Person)-[r:knows]->(b) RETURN a, r, b',
            options=VizOptions(title="Knows graph", theme="dark"),
        )
        figure.save(f"{tmpdir}/knows.html")  # open offline in any browser
        print(repr(figure))
    finally:
        graph.close()
```

`GraphDB.visualize` is the same path as a method, and `visualize_sample`
covers large graphs through typed sampling instead of full dumps:

```python
from gestaltdb.sampling import SamplingHop, SamplingPattern
from gestaltdb.viz.api import VizOptions

pattern = SamplingPattern([SamplingHop("binds", direction="out", sample_size=5)])
figure = graph.visualize(seeds=["drug-1"], pattern=pattern)
figure = graph.visualize(
    'MATCH (a:Person) RETURN a LIMIT 25',
    options=VizOptions(max_nodes=500, max_edges=1000),
)
```

Caps (defaults 2000 nodes / 5000 edges) truncate deterministically with a
`TruncationWarning` and an on-canvas banner; payloads beyond twice the caps
raise `VizCapExceededError` naming the sampling alternative.

## Define Canonical Temporal Values

Temporal values require timezone-aware datetimes, normalize to UTC
microseconds, and use half-open intervals `[start, end)`. These values establish
shared semantics; they do not yet filter `GraphDB` records or sampler snapshots.

```python
from datetime import datetime, timedelta, timezone

from gestaltdb.temporal import TemporalContext, TemporalInstant, TemporalInterval

valid_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
valid_to = valid_from + timedelta(days=30)
validity = TemporalInterval.from_values(valid_from, valid_to)

assert TemporalContext.as_of(
    datetime(2026, 1, 15, tzinfo=timezone.utc)
).matches(validity)
assert not TemporalContext.as_of(valid_to).matches(validity)

instant = TemporalInstant.parse("2026-01-15T12:00:00Z")
assert TemporalInstant.decode_sortable(instant.encode_sortable()) == instant
```

## Append Immutable Temporal Versions

Temporal version writes preserve assertions, corrections, and retractions
without changing current-state records returned by `get_node` or `get_edge`.
Exact history, as-of resolution, and typed traversal use separate temporal
indexes.

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer
from gestaltdb.temporal import TemporalInterval

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())
    try:
        asserted = graph.put_node_version(
            Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}),
            valid=TemporalInterval.parse("2020-01-01T00:00:00Z"),
            metadata={"source": "people-import"},
        )
        corrected = graph.correct_node_version(
            Node(node_id="alice", labels=["Person"], properties={"name": "Alicia"}),
            supersedes_version_id=asserted.version_id,
        )
        graph.retract_node_version(
            "alice",
            valid_from="2030-01-01T00:00:00Z",
            supersedes_version_id=corrected.version_id,
            reason="source withdrew the assertion",
        )

        history = list(graph.iter_node_versions("alice"))
        assert [version.operation.value for version in history] == [
            "assert", "correct", "retract"
        ]
        assert graph.get_node(b"alice") is None

        at_2025 = graph.get_node_as_of(
            "alice", valid_time="2025-01-01T00:00:00Z"
        )
        assert at_2025.node.properties["name"] == "Alicia"
    finally:
        graph.close()
```
