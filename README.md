# PyGraphDB

<img src="https://raw.githubusercontent.com/mylonasc/pygraphdb/refs/heads/main/assets/coverage_badge.svg">

PyGraphDB is a pure Python graph database toolkit for attributed graphs. It stores nodes, edges, labels, typed adjacency records, and property indexes on embedded key-value backends.

Documentation: https://mylonasc.github.io/pygraphdb/

## Install

From a local checkout:

```sh
uv sync
```

Install into another project:

```sh
uv add /path/to/pygraphdb
```

With pip:

```sh
python -m pip install /path/to/pygraphdb
```

Optional extras include `lmdb`, `leveldb`, `rocksdb`, `arrow`, `polars`, `fast-ingest`, `msgpack`, `protobuf`, `bloom`, `docs`, `dev`, and `all`.

## Quick Example

```python
from pygraphdb.graphdb import Edge, GraphDB, Node
from pygraphdb.kvstores import LMDBStore
from pygraphdb.serializers import PickleSerializer

graph = GraphDB(LMDBStore(path="example_lmdb"), PickleSerializer())

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

## Features

- Attributed `Node` and `Edge` objects with stable IDs.
- Native node labels and typed edge traversal through `edge.properties["type"]`.
- LMDB, LevelDB, and RocksDB/PyRex storage backends.
- Pickle, JSON, MessagePack, and Protobuf serializers.
- Label, relationship type, property, composite, and range indexes.
- Read-only Cypher subset for indexed scans, typed traversal, filtering, ordering, limits, and chained `MATCH` clauses.
- Bulk and columnar ingestion helpers.
- Typed path and subgraph sampling.

See the full documentation for backend selection, indexing, Cypher syntax, ingestion, sampling, and benchmarks.
