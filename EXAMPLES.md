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

## Run Read-Only Cypher

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

Supported Cypher is read-only. It covers indexed node scans, typed relationship traversal, filters, projection, ordering, limits, chained `MATCH` clauses, and `WITH` stages with scope replacement:

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

It does not support mutating queries, `OPTIONAL MATCH`, or variable-length paths.

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
