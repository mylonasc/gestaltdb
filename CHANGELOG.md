# Changelog

## Unreleased

- Replaced regex-based Cypher parsing with a Lark grammar supporting comments, Unicode and escaped names, source-located errors, expression precedence, multiple inline properties, and parameterized pagination.
- Added arithmetic, expression comparisons, boolean and string predicates, regex matching, alias-aware ordering, Cypher three-valued null logic, and relationship-isomorphism enforcement for fixed paths.
- Corrected logical plans for `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT`, and removed unsafe intermediate `LIMIT` pushdown during multi-hop traversal.
- Added generalized fixed-length patterns with anonymous elements, optional relationship types, bracketless relationships, endpoint labels/properties, unanchored multi-hop traversal, comma-separated pattern parts, and canonical all-edge iteration for untyped matches.
- Added typed Cypher binding/result rows and executable projection, sorting, distinct, skip, and limit operators, then made the planner-derived logical plan authoritative for query execution.
- Added a canonical, source-spanned clause AST through `gestaltdb.cypher.parse_ast()` while preserving the existing specialized `parse()` results.

## 0.6.2

- Added a `backends` packaged agent-docs topic covering `GraphDB.create/open`, manifests, LevelDB/PyRex/RocksDB selection, and DB inspection (`manifest`, `index_statistics`, entity properties).
- Made `python -m gestaltdb.agent_docs search` return bounded, ranked, paged results with context snippets (`--limit`, `--page`, `--context`).
- Fixed the packaged skill/`Cypher` benchmark assertion to use tuple-safe `QueryResult.columns` comparison.
- Extended the agent benchmark runner with Qwen 27B max-context config, per-run context metadata, HTML trace viewer with context usage, packaged skill installation into worktrees, and a robust judge JSON extractor.
- Added persistence/inspection and PyRex/RocksDB Polars ingestion benchmark tasks.

## 0.6.0

- Added `AGENTS.md` as a concise agent-facing map of the repository, public API modules, backend guidance, indexing rules, ingestion modes, Cypher support, sampling APIs, and verification commands.
- Added `EXAMPLES.md` with runnable usage patterns for graph creation, manifest-backed stores, backend and serializer selection, indexing, read-only Cypher, typed traversal sampling, Arrow/Polars ingestion, and sampler snapshots.
- Added an opencode documentation-maintenance skill for keeping `AGENTS.md`, `EXAMPLES.md`, and the package docstring aligned with implementation and Sphinx docs.
- Expanded the `gestaltdb` package docstring with import guidance, a minimal write/query example, and key usage notes for developers and agents.
