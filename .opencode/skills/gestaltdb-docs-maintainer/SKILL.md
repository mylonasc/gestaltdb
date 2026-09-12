---
name: gestaltdb-docs-maintainer
description: Use when creating or maintaining GestaltDB agent-facing documentation, especially AGENTS.md, EXAMPLES.md, and src/gestaltdb/__init__.py package docs.
---

# GestaltDB Docs Maintainer

Use this skill only for documentation work that teaches agents or developers how to use GestaltDB. It is specifically for `AGENTS.md`, `EXAMPLES.md`, and the package docstring in `src/gestaltdb/__init__.py`, plus consistency checks against Sphinx docs in `docs/`.

## Required Context Pass

Before editing these docs, inspect the current implementation and existing docs rather than relying on memory:

- Read `src/gestaltdb/__init__.py` to see the package-root exports.
- Read `src/gestaltdb/graphdb.py` for `Node`, `Edge`, `GraphDB`, indexes, ingestion, Cypher entry points, and typed traversal methods.
- Read `src/gestaltdb/kvstores.py` for backend names and optional dependency behavior.
- Read `src/gestaltdb/serializers.py` for serializer names and constraints.
- Read `src/gestaltdb/ingestion.py` for `ColumnarIngestionMode`, `IndexMaintenanceMode`, `NodeList`, and `EdgeList`.
- Read `src/gestaltdb/sampling/__init__.py`, `snapshot.py`, `engine.py`, and `batch.py` for sampling APIs and ID mapping semantics.
- Read `docs/index.rst`, `docs/quickstart.rst`, `docs/storage-backends.rst`, `docs/serializers.rst`, `docs/typed-sampling.rst`, and `docs/cypher.rst` for public narrative guidance.
- Search tests for changed behavior, especially `tests/test_graphdb_interfaces.py`, `tests/test_ingestion.py`, `tests/test_cypher.py`, and `tests/test_sampling.py`.

## AGENTS.md Rules

`AGENTS.md` should be a concise map for coding agents. Keep it factual and implementation-aligned.

Include:

- Source, docs, tests, and important module paths.
- Correct import paths, distinguishing package-root re-exports from submodule imports.
- Core object model: `Node`, `Edge`, `GraphDB`, `KVStore`, serializers, typed edge property.
- Backend selection guidance for `LMDBStore`, `LevelDBStore`, and `PyRexStore`.
- Indexing rules: automatic label/type indexes, explicit property indexes, range indexes, deferred index rebuilds.
- Ingestion rules: object writes, Arrow/Polars entity columns, serialized payload columns, index maintenance modes.
- Read-only Cypher support and explicit limitations.
- Sampling distinction between `GraphDB` typed traversal sampling and array-native `SamplerSnapshot`/`SamplerEngine`.
- Development commands for tests and Sphinx builds.
- Documentation maintenance checklist.

Do not include long tutorials in `AGENTS.md`; put runnable code in `EXAMPLES.md`.

## EXAMPLES.md Rules

`EXAMPLES.md` should be runnable-oriented. Prefer short examples that demonstrate real API paths.

Include examples for:

- Creating a graph with `GraphDB(store, serializer)`.
- Creating and opening a manifest-backed graph with `GraphDB.create` and `GraphDB.open`.
- Backend and serializer selection.
- Property indexes, label/type lookups, and range lookups.
- Read-only Cypher with parameters and iteration over `QueryResult`.
- Typed traversal and typed path/subgraph sampling.
- Arrow ingestion with `IndexMaintenanceMode.DEFER_REBUILD`.
- Polars ingestion with entity columns.
- `SamplerSnapshot`/`SamplerEngine` usage, including compact ID lookup and `SampledSubgraphBatch.to_numpy()`.

Example style:

- Use explicit submodule imports for `GraphDB`, `Node`, `Edge`, backends, and serializers.
- Use `TemporaryDirectory` where examples create stores.
- Always close graph handles with `try/finally` or clearly show `graph.close()`.
- Keep Cypher examples within the documented read-only subset.
- Use `properties={"type": "..."}` for typed relationships.
- Do not imply that unsupported optional frameworks are required; frame `to_pyg`, `to_dgl`, `to_tf_gnns`, Arrow, and Polars as optional dependency paths.

## Package Docstring Rules

The `src/gestaltdb/__init__.py` docstring should guide developers and agents at import time. It should not become a full manual.

Include:

- One-sentence description of GestaltDB.
- Explicit import guidance: core classes from `gestaltdb.graphdb`, backends from `gestaltdb.kvstores`, serializers from `gestaltdb.serializers`, sampling helpers from `gestaltdb.sampling` or package root.
- A minimal create/write/query/close example.
- Notes that edge type comes from `edge.properties["type"]`, Cypher is read-only and partial, property indexes are explicit, and bulk ingestion/sampling have dedicated helpers.
- Pointers to `AGENTS.md`, `EXAMPLES.md`, and `docs/`.

Do not change `__all__` unless the task explicitly asks for an API change.

## Accuracy Checks

Before finishing:

- Confirm every documented import path exists.
- Confirm every documented method exists and signatures are broadly correct.
- Confirm examples do not depend on unsupported Cypher syntax.
- Confirm backend names match `GraphDB.create` registry names: `lmdb`, `leveldb`, and `pyrex`.
- Confirm serializer names match the registry: `pickle`, `json`, `messagepack`, and `protobuf`.
- Run at least a focused docs sanity check when practical: `uv run pytest tests/test_graphdb_interfaces.py tests/test_cypher.py tests/test_sampling.py -q`.
- If Sphinx dependencies are installed, run `uv run sphinx-build -b html docs docs/_build/html` after changing Sphinx docs.

After creating or changing this skill, remind the user to restart opencode so the running session can load it.
