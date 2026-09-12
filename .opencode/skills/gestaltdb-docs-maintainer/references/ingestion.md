# Columnar Ingestion (Arrow & Polars)

**When to read:** Read this file when you are working on bulk data loading, columnar pipelines, PyArrow/Polars integration, memory tuning for ingestion, or index maintenance strategies during ingest.

---

## 1. Columnar Ingestion Overview

GestaltDB provides vectorized, columnar ingestion interfaces to bypass the overhead of Python object allocations:
- `graph.ingest_arrow(...)`: Uses PyArrow arrays/record batches.
- `graph.ingest_polars(...)`: Uses Polars DataFrames.

Both methods support streaming writes directly into the underlying key-value store, maintaining typed adjacency and optional secondary indexes.

---

## 2. Ingestion Modes (`ColumnarIngestionMode`)

Imported from package root or `gestaltdb.ingestion`:
- `ColumnarIngestionMode.ENTITY_COLUMNS`:
  Takes structured columns for IDs, labels, edge types, and properties. GestaltDB encodes payloads using the database's configured serializer.
- `ColumnarIngestionMode.SERIALIZED_PAYLOADS`:
  Expects pre-serialized byte columns (`node_value`, `edge_value`). Useful when payloads are pre-encoded in an upstream distributed worker or data pipeline.

---

## 3. Index Maintenance Modes (`IndexMaintenanceMode`)

- `IndexMaintenanceMode.MAINTAIN`:
  Updates secondary property indexes synchronously during insertion. Safest for immediate querying, but has write overhead.
- `IndexMaintenanceMode.DEFER`:
  Skips updating secondary property indexes during ingest. Useful for multi-stage bulk loads. You must call `graph.rebuild_deferred_indexes()` before querying.
- `IndexMaintenanceMode.DEFER_REBUILD` (Default for high-level APIs):
  Streams records with deferred index maintenance, then executes an optimized batch index rebuild immediately before returning. Recommended for most bulk loads.

---

## 4. Key Rules

- Node IDs, Edge IDs, and edge source/target values must be strings or bytes.
- Columnar edge ingestion maintains **typed adjacency** (`A:<type>:<dir>:<id>`), but intentionally skips legacy untyped adjacency blobs for maximum throughput.
- When ingesting with `PyRexStore`, ensure `transactional=False` so the native direct columnar batch writer is used.

---

## 5. Runnable Examples

### Ingesting with Polars DataFrames
```python
from tempfile import TemporaryDirectory
import polars as pl

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

nodes_df = pl.DataFrame({
    "node_id": ["n1", "n2", "n3"],
    "labels": [["Customer"], ["Customer"], ["Product"]],
    "name": ["Alice", "Bob", "Widget"],
    "tier": [1, 2, 1],
})

edges_df = pl.DataFrame({
    "edge_id": ["e1", "e2"],
    "source": ["n1", "n2"],
    "target": ["n3", "n3"],
    "edge_type": ["purchased", "purchased"],
    "qty": [2, 5],
})

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/ingest_demo"), JSONSerializer())
    try:
        graph.create_node_property_index("tier")
        graph.ingest_polars(
            nodes_df,
            edges_df,
            node_property_columns=["name", "tier"],
            edge_property_columns=["qty"],
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )

        tier1_nodes = graph.nodes_by_property("tier", 1)
        print("Tier 1 nodes:", [n.get_id for n in tier1_nodes])
    finally:
        graph.close()
```

### Ingesting with PyArrow Arrays
```python
from tempfile import TemporaryDirectory
import pyarrow as pa

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/arrow_demo"), JSONSerializer())
    try:
        graph.ingest_arrow(
            pa.array(["u1", "u2"]),
            pa.array(["rel1"]),
            pa.array(["u1"]),
            pa.array(["u2"]),
            pa.array(["follows"]),
            labels=pa.array([["User"], ["User"]]),
            node_properties={"handle": pa.array(["alice_x", "bob_y"])},
            edge_properties={"timestamp": pa.array([1700000000])},
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )

        node = graph.get_node(b"u1")
        print("Ingested user:", node.properties["handle"])
    finally:
        graph.close()
```
