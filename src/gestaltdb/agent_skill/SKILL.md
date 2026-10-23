---
name: gestaltdb-user-guide
description: Use when writing application code that uses GestaltDB, especially GraphDB creation, explicit imports, Cypher queries, indexing, ingestion, epistemic claims, typed sampling, and offline visualization.
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
python -m gestaltdb.agent_docs get ontology --examples
python -m gestaltdb.agent_docs search "IndexMaintenanceMode.DEFER"
```

Run `python -m gestaltdb.agent_docs install-opencode-skill` from an application project to copy this packaged skill to `.opencode/skills/gestaltdb-user-guide/SKILL.md`, where opencode can load it as a project-local skill.

Available topics:

- `quickstart`: imports, graph lifecycle, node/edge creation, close discipline.
- `backends`: `GraphDB.create/open`, manifests, LevelDB/PyRex/RocksDB, and inspection.
- `cypher`: supported Cypher reads, writes, schema commands, procedures, and limitations.
- `indexing`: property-graph filtering, exact/range and label/type composite lookups, ID-only cohorts, deferred rebuilds.
- `sampling`: high-performance CSR snapshots, weighted neighbors, layers, walks, hard negatives, temporal filters, and live typed traversal.
- `ingestion`: Arrow/Polars ingestion and index maintenance modes.
- `visualization`: offline HTML/Jupyter, property labels/styling, directed exploration, saved views, SVG/PNG export, and `GraphDB.visualize`.
- `epistemic`: sourced bitemporal claims, provenance, contradiction, and four-valued status.
- `temporal`: version history, legacy migration, recovery, and compatibility limits.

## Agent Workflow

- Write the solution file first, then validate and iterate; do not exhaustively explore library internals before writing code.
- Prefer `python -m gestaltdb.agent_docs get <topic> --examples` for copy-pasteable patterns over reading `src/` files.
- Use `python -m gestaltdb.agent_docs search "<API name>" --examples-only` to get an API signature card plus the runnable example that covers it.

## Import Rules

Prefer explicit submodule imports for core classes:

```python
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer, PickleSerializer
```

The package root intentionally does not export `GraphDB`, `Node`, `Edge`, storage backends, or serializers. The root does export selected ingestion, epistemic, Cypher result, and sampling helpers such as `ClaimStatus`, `IndexMaintenanceMode`, `QueryResult`, `SamplingHop`, and `SamplingPattern`.

## Core Usage Rules

- Install backend and serializer extras independently, for example `gestaltdb[leveldb,msgpack]`; Pickle and JSON need no serializer extra, while a base install has no embedded backend.
- Use `GraphDB.create(path, backend="leveldb", serializer="json")` for a self-describing database directory, or construct `GraphDB(store, serializer)` directly.
- Use `GraphDB.open(path)` to reopen a self-describing database created with `GraphDB.create`.
- Use `graph.index_statistics()` and `graph.manifest` for inspection; inspect retrieved `Node.properties` and `Edge.properties` for property names/values.
- Traversal records carry byte IDs under `neighbor_id`/`edge_id`/`source_id`/`target_id`; decode with `graph.key_to_string(...)` and compare ID sets, since lookup order is not guaranteed.
- Always close graph handles with `graph.close()` in a `finally` block.
- Store relationship types as `Edge(properties={"type": "REL_TYPE"})`.
- Create property indexes explicitly before index-backed property lookups.
- Run `graph.rebuild_deferred_indexes()` before relying on secondary indexes after deferred ingestion.
- Use `SamplingHop` and `SamplingPattern` for GraphDB typed traversal sampling with external node IDs.
- Use reusable `SamplerSnapshot`/`SamplerEngine` arrays for repeated ML batches; RAM/memmap sampling avoids repeated KV reads and graph-object deserialization.
- Filter the sampled property graph with build-time `node_filter`/`edge_filter`; filtering seeds alone does not constrain every hop. Callbacks scan source records, while runtime sampling uses relation/direction/temporal policies.
- Import visualization helpers from `gestaltdb.viz.api`. `VizOptions` configures labels, attribute styling, and caps; `VizFigure.save()` writes offline HTML, while `as_svg()` / `save_svg()` generate dependency-free static SVG. Browser PNG and view-state exports are local; static Python SVG does not capture iframe state.
- Use `assert_claim` for sourced positive/negative propositions and `claim_status` for open-world four-valued evaluation at valid/system time.
- Use `create_rule` and bounded `run_rules(as_of=...)` for positive Horn inference over entity claims; use `maintain_truth` after premise/rule changes and `explain_claim` for bounded historical derivation graphs.
- Use `assert_world_accessibility` plus bounded `entails` for temporal `BELIEVES`, `KNOWS`, `POSSIBLE`, and `NECESSARY` evaluation. Belief, knowledge, and generic modal accessibility are separate frames.
- Treat `TimeIndexedEdge` as deprecated legacy data. Back up and stop writers before `migrate_time_indexed_edges`; validate before deleting legacy records.

## Cypher Boundaries

`GraphDB.query(cypher, parameters=None)` supports reads and writes, including
read composition, paths, core expressions and aggregates, mutations, persisted
node constraints, and `SHOW CONSTRAINTS`/`SHOW INDEXES`. Registered procedures
use `CALL name(...) YIELD ...`; `pg.sample_typed_paths` and bounded
`kg.entails` execute.

Do not use pattern comprehensions, pattern arguments to `exists()`, GQL
quantified paths, relationship/multi-property constraints, or unregistered
procedures.

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
