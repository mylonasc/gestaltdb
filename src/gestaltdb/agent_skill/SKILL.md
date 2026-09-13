---
name: gestaltdb-user-guide
description: Use when writing application code that uses GestaltDB, especially GraphDB creation, explicit imports, Cypher queries, indexing, ingestion, and typed sampling.
---

# GestaltDB User Guide For Agents

Use this packaged skill when you are an agentic coding assistant writing code that uses GestaltDB as a library. Focus on public APIs and runnable application code. Do not modify GestaltDB internals unless the user explicitly asks to develop the library itself.

## Fast Retrieval

GestaltDB ships a small retrieval CLI for agents and developer tooling:

```bash
python -m gestaltdb.agent_docs install-opencode-skill
python -m gestaltdb.agent_docs list
python -m gestaltdb.agent_docs get quickstart
python -m gestaltdb.agent_docs get cypher --examples
python -m gestaltdb.agent_docs search "IndexMaintenanceMode.DEFER"
```

Run `python -m gestaltdb.agent_docs install-opencode-skill` from an application project to copy this packaged skill to `.opencode/skills/gestaltdb-user-guide/SKILL.md`, where opencode can load it as a project-local skill.

Available topics:

- `quickstart`: imports, graph lifecycle, node/edge creation, close discipline.
- `backends`: `GraphDB.create/open`, manifests, LevelDB/PyRex/RocksDB, and inspection.
- `cypher`: supported read-only Cypher syntax and common unsupported forms.
- `indexing`: explicit property indexes, range lookups, deferred rebuilds.
- `sampling`: typed traversal sampling vs snapshot/engine sampling.
- `ingestion`: Arrow/Polars ingestion and index maintenance modes.

## Import Rules

Prefer explicit submodule imports for core classes:

```python
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer
```

The package root intentionally does not export `GraphDB`, `Node`, `Edge`, storage backends, or serializers. The root does export selected ingestion, Cypher result, and sampling helpers such as `IndexMaintenanceMode`, `QueryResult`, `SamplingHop`, and `SamplingPattern`.

## Core Usage Rules

- Use `GraphDB.create(path, backend="leveldb", serializer="json")` for a self-describing database directory, or construct `GraphDB(store, serializer)` directly.
- Use `GraphDB.open(path)` to reopen a self-describing database created with `GraphDB.create`.
- Use `graph.index_statistics()` and `graph.manifest` for inspection; inspect retrieved `Node.properties` and `Edge.properties` for property names/values.
- Always close graph handles with `graph.close()` in a `finally` block.
- Store relationship types as `Edge(properties={"type": "REL_TYPE"})`.
- Create property indexes explicitly before index-backed property lookups.
- Run `graph.rebuild_deferred_indexes()` before relying on secondary indexes after deferred ingestion.
- Use `SamplingHop` and `SamplingPattern` for GraphDB typed traversal sampling with external node IDs.
- Use `SamplerSnapshot` and `SamplerEngine` only when you need compact array-native IDs for ML data loading.

## Cypher Boundaries

`GraphDB.query(cypher, parameters=None)` is read-only. It supports label scans, inline properties, parameters, typed relationship traversal, `WHERE`, `RETURN`, `DISTINCT`, `ORDER BY`, `SKIP`, `LIMIT`, and chained `MATCH` clauses.

Do not use mutating clauses, aggregation, `WITH`, `OPTIONAL MATCH`, variable-length paths, multiple pattern parts in one `MATCH`, or path binding like `p = (a)-[:T]->(b)`.

## Minimal Runnable Pattern

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node("alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node("bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge("e1", "alice", "bob", properties={"type": "KNOWS", "since": 2024}))

        result = graph.query(
            'MATCH (a:Person {name: $name}) '
            'MATCH (a)-[:KNOWS]->(b) '
            'RETURN a.id AS source, b.name AS target',
            parameters={"name": "Alice"},
        )
        assert tuple(result.columns) == ("source", "target")
        assert result.records == [{"source": "alice", "target": "Bob"}]
    finally:
        graph.close()
```

For more examples, run `python -m gestaltdb.agent_docs get <topic> --examples`.
