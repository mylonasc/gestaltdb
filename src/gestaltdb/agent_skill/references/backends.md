# Backends, Persistence, And Inspection

Read this when creating a durable database directory, reopening a stored DB, selecting a backend, or inspecting what indexes and properties a graph contains.

## Rules

- Use `GraphDB.create(path, backend="leveldb"|"pyrex"|"lmdb", serializer="json"|"pickle"|"messagepack"|"protobuf")` for manifest-backed directories.
- Install backend and serializer extras independently, such as `gestaltdb[leveldb,msgpack]` or `gestaltdb[lmdb,protobuf]`; no combination-specific extra is needed.
- A base install includes Pickle and JSON but no embedded backend. Use `gestaltdb[backends]` and `gestaltdb[serializers]` when all implementations are needed.
- Parameterless `GraphDB.create(path)` retains the `pyrex` compatibility default and therefore requires `gestaltdb[rocksdb]`; there is no silent backend fallback.
- Reopen manifest-backed stores with `GraphDB.open(path)` instead of reconstructing the backend manually.
- Inspect backend and serializer choices with `graph.manifest`.
- Inspect persisted property index definitions with `graph.index_statistics()` or `graph.indexed_node_properties` and `graph.indexed_edge_properties`.
- Inspect node and edge properties from retrieved `Node.properties` and `Edge.properties`; GestaltDB does not expose a schema catalog for all property names.
- For RocksDB-backed workloads, use `backend="pyrex"` or `PyRexStore`; install `gestaltdb[rocksdb]` when `PyRexStore` reports a missing optional dependency.
- Close every opened graph handle in `finally`, including reopened handles.

## Persistence And Inspection Example

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node

with TemporaryDirectory() as tmpdir:
    db_path = f"{tmpdir}/stored_graph"
    graph = GraphDB.create(
        db_path,
        backend="leveldb",
        serializer="json",
        indexed_node_properties=["kind"],
        indexed_edge_properties=["score"],
    )
    try:
        graph.put_node(Node("drug", labels=["Drug"], properties={"kind": "drug", "name": "Aspirin"}))
        graph.put_node(Node("protein", labels=["Protein"], properties={"kind": "protein", "name": "PTGS1"}))
        graph.put_edge(Edge("e1", "drug", "protein", properties={"type": "TARGETS", "score": 0.92}))
    finally:
        graph.close()

    reopened = GraphDB.open(db_path)
    try:
        assert reopened.manifest["backend"]["name"] == "leveldb"
        assert reopened.index_statistics() == {
            "indexed_node_properties": ("kind",),
            "indexed_edge_properties": ("score",),
        }
        assert reopened.get_node(b"drug").properties == {"kind": "drug", "name": "Aspirin"}
        assert reopened.get_edge(b"e1").properties == {"type": "TARGETS", "score": 0.92}
        assert [node.get_id for node in reopened.nodes_by_property("kind", "drug")] == ["drug"]
        assert [edge.get_id for edge in reopened.edges_by_property("score", 0.92)] == ["e1"]
    finally:
        reopened.close()
```

## PyRex/RocksDB And Polars Example

```python
from tempfile import TemporaryDirectory

import polars as pl

from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(
        f"{tmpdir}/rocks_graph",
        backend="pyrex",
        serializer="json",
        backend_options={"disable_wal": True},
        indexed_node_properties=["kind"],
        indexed_edge_properties=["score"],
    )
    try:
        node_df = pl.DataFrame({
            "node_id": ["drug", "protein"],
            "labels": [["Drug"], ["Protein"]],
            "kind": ["drug", "protein"],
        })
        edge_df = pl.DataFrame({
            "edge_id": ["e1"],
            "source": ["drug"],
            "target": ["protein"],
            "edge_type": ["TARGETS"],
            "score": [0.92],
        })
        result = graph.ingest_polars(
            node_df,
            edge_df,
            node_property_columns=["kind"],
            edge_property_columns=["score"],
            chunk_size=1,
        )
        assert result["nodes"] == 2
        assert result["edges"] == 1
        assert graph.neighbors_by_edge_type("drug", "TARGETS") == [b"protein"]
    finally:
        graph.close()
```
