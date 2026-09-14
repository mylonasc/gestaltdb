# Backends, Persistence, And Inspection

Read this when creating a durable database directory, reopening a stored DB, selecting a backend, or inspecting what indexes and properties a graph contains.

## Rules

- Use `GraphDB.create(path, backend="leveldb"|"pyrex"|"lmdb", serializer="json"|"pickle")` for manifest-backed directories.
- Reopen manifest-backed stores with `GraphDB.open(path)` instead of reconstructing the backend manually.
- Inspect backend and serializer choices with `graph.manifest`.
- Inspect persisted property index definitions with `graph.index_statistics()` or `graph.indexed_node_properties` and `graph.indexed_edge_properties`.
- Inspect node and edge properties from retrieved `Node.properties` and `Edge.properties`; GestaltDB does not expose a schema catalog for all property names.
- Enumerate stored entities with label/type scans (`nodes_by_label`, `edges_by_type`) and fetch individuals with `get_node`/`get_edge`; there is no whole-graph enumeration API.
- Typed traversal neighbors come back as byte IDs; decode them with `graph.key_to_string(...)` before comparing with string IDs.
- Lookup and traversal order is not guaranteed; compare ID sets instead of ordered lists.
- For RocksDB-backed workloads, use `backend="pyrex"` or `PyRexStore`; install `pyrex-rocksdb` when `PyRexStore` reports a missing optional dependency.
- When round-tripping Polars tables through CSV, read numeric property columns back with an explicit dtype (e.g. `pl.read_csv(path, schema_overrides={"score": pl.Float64})`); otherwise they come back as strings and numeric range indexes silently break.
- CSV has no nested columns: write label columns as scalar strings and re-wrap them as `pl.List(pl.String)` after reading, before columnar ingestion.
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

## PyRex/RocksDB With CSV Round-Trip Example

CSV files cannot carry nested columns and `pl.read_csv` infers plain strings unless told otherwise, so restore the `labels` list column and the numeric `score` dtype after reading.

```python
from tempfile import TemporaryDirectory

import polars as pl

from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(
        f"{tmpdir}/rocks_csv_graph",
        backend="pyrex",
        serializer="json",
        backend_options={"disable_wal": True},
        indexed_node_properties=["kind"],
        indexed_edge_properties=["score"],
    )
    try:
        node_df = pl.DataFrame({
            "node_id": ["drug", "protein"],
            "labels": ["Drug", "Protein"],
            "kind": ["drug", "protein"],
        })
        edge_df = pl.DataFrame({
            "edge_id": ["e1"],
            "source": ["drug"],
            "target": ["protein"],
            "edge_type": ["TARGETS"],
            "score": [0.92],
        })
        node_df.write_csv(f"{tmpdir}/nodes.csv")
        edge_df.write_csv(f"{tmpdir}/edges.csv")
        node_df = pl.read_csv(f"{tmpdir}/nodes.csv")
        edge_df = pl.read_csv(f"{tmpdir}/edges.csv", schema_overrides={"score": pl.Float64})
        assert edge_df.schema["score"] == pl.Float64
        node_df = node_df.with_columns(
            pl.col("labels").map_elements(lambda label: [label], return_dtype=pl.List(pl.String))
        )
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
