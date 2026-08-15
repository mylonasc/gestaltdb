# GestaltDB

![Coverage](https://raw.githubusercontent.com/mylonasc/gestaltdb/refs/heads/main/assets/coverage_badge.svg)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://mylonasc.github.io/gestaltdb/)

GestaltDB is a pure Python graph database toolkit for attributed graphs. It stores nodes, edges, labels, typed adjacency records, and property indexes on embedded key-value backends.

Documentation: https://mylonasc.github.io/gestaltdb/

## Install From PyPI

With pip:

```sh
python -m pip install gestaltdb
```

With uv:

```sh
uv add gestaltdb
```

Install columnar ingestion dependencies:

```sh
python -m pip install "gestaltdb[arrow,polars]"
```

Install all optional backends and serializers:

```sh
python -m pip install "gestaltdb[all]"
```

Optional extras include `lmdb`, `leveldb`, `rocksdb`, `arrow`, `polars`, `fast-ingest`, `msgpack`, `protobuf`, `bloom`, `docs`, `dev`, and `all`.

## Basic Example

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())

    graph.put_node(Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}))
    graph.put_node(Node(node_id="bob", labels=["Person"], properties={"name": "Bob"}))
    graph.put_edge(Edge(
        edge_id="alice-knows-bob",
        source="alice",
        target="bob",
        properties={"type": "knows", "since": 2024},
    ))

    result = graph.query('MATCH (a:Person {name: "Alice"}) MATCH (a)-[:knows]->(b) RETURN a.id, b.name')
    print(result.records)

    graph.close()
```

## Arrow Ingestion Example

This example ingests entity columns from PyArrow arrays. `JSONSerializer` lets GestaltDB build node and edge payloads from structured columns.

```python
from tempfile import TemporaryDirectory

import pyarrow as pa

from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer
from gestaltdb import IndexMaintenanceMode

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), JSONSerializer())

    graph.create_node_property_index("name")

    result = graph.ingest_arrow(
        pa.array(["alice", "bob", "carol"]),
        pa.array(["alice-knows-bob", "bob-knows-carol"]),
        pa.array(["alice", "bob"]),
        pa.array(["bob", "carol"]),
        pa.array(["knows", "knows"]),
        labels=pa.array([["Person"], ["Person"], ["Person"]]),
        node_properties={"name": pa.array(["Alice", "Bob", "Carol"]), "age": pa.array([34, 36, 29])},
        edge_properties={"since": pa.array([2024, 2025])},
        index_mode=IndexMaintenanceMode.DEFER_REBUILD,
    )
    print(result)  # {'nodes': 3, 'edges': 2, 'rebuilt_indexes': ..., 'stale_indexes': ()}

    result = graph.query('MATCH (a:Person {name: "Alice"}) MATCH (a)-[:knows]->(b) RETURN a.id, b.name')
    print(result.records)

    graph.close()
```

## Polars Ingestion Example

This example ingests the same graph from Polars DataFrames. Property columns are converted into node and edge payloads during ingestion.

```python
from tempfile import TemporaryDirectory

import polars as pl

from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer
from gestaltdb import IndexMaintenanceMode

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
    graph.create_node_property_index("name")

    graph.ingest_polars(
        nodes,
        edges,
        node_property_columns=["name", "age"],
        edge_property_columns=["since"],
        index_mode=IndexMaintenanceMode.DEFER_REBUILD,
    )

    result = graph.query('MATCH (a:Person) MATCH (a)-[:knows]->(b) RETURN a.name, b.name ORDER BY a.name')
    print(result.records)

    graph.close()
```

## Install From A Checkout

From a local checkout:

```sh
uv sync
```

Install into another project:

```sh
uv add /path/to/gestaltdb
```

With pip:

```sh
python -m pip install /path/to/gestaltdb
```

## Backend and Ingestion Recommendations

For the current library:

- Use `LevelDBStore` for small local graphs, examples, and straightforward embedded use.
- Use `PyRexStore`/RocksDB for large append-only loads and Arrow/Polars columnar ingestion.
- Use `LMDBStore` when LMDB's storage model is desirable and you can size `map_size` ahead of loading.
- Use `JSONSerializer` with `GraphDB.ingest_polars` or `GraphDB.ingest_arrow` when input data is already tabular and JSON-compatible.
- Use pre-serialized `node_value` and `edge_value` columns when upstream data already has serializer-compatible payload bytes.
- Keep `IndexMaintenanceMode.MAINTAIN` for incremental writes that need indexes ready immediately.
- Use `IndexMaintenanceMode.DEFER` or `DEFER_REBUILD` for bulk loads when you want to move secondary-index work out of the write path.

Measured locally on 100k nodes and 500k edges, RocksDB native columnar ingestion was `1.16x` faster end-to-end than LevelDB on the same Python JSON payload path, and Polars JSON payload construction was `1.86x` faster than Python JSON serialization. Deferred indexing made the write phase `8.72x` faster, but immediate full rebuild made total ingest-plus-rebuild `17.2%` slower for that subset. Treat these as workload-specific guidance and benchmark your graph shape, serializer, and indexes.

## Features

- Attributed `Node` and `Edge` objects with stable IDs.
- Native node labels and typed edge traversal through `edge.properties["type"]`.
- LMDB, LevelDB, and RocksDB/PyRex storage backends.
- Pickle, JSON, MessagePack, and Protobuf serializers.
- Label, relationship type, property, composite, and range indexes.
- Read-only Cypher subset for indexed scans, typed traversal, filtering, ordering, limits, and chained `MATCH` clauses.
- Bulk and columnar ingestion helpers for Arrow and Polars.
- Typed path and subgraph sampling.

See the full documentation for backend selection, indexing, Cypher syntax, ingestion, sampling, and benchmarks.

<details>
<summary>Name origin</summary>

The name GestaltDB is inspired by Gestalt psychology and the idea that the whole is something more than its parts.

</details>
