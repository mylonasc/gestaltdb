# Developer Ergonomics Enhancements — GestaltDB 0.16.0 (PyPI)

Scope: reasonably skilled library-user simulation. Only user-available
resources from the latest release were used: installed `gestaltdb==0.16.0`
wheel, package docstring, README metadata, `python -m gestaltdb.agent_docs`,
published docs site, and runtime docstrings. No `src/` reads, private
attributes, repo fixtures, or internal store access.

Reproduction: `developer-ergonomics/probe_release.py` writes
`developer-ergonomics/observations-0.16.0.json`. It runs every packaged
`agent_docs` example plus direct inspection probes (create → write →
index → query → visualize → temporal view → reopen). Environment:
CPython 3.12.3 with `lmdb`, `plyvel`, `pyrex-rocksdb`, `pyarrow`,
`polars`, `networkx`.

What worked first try: `GraphDB.create/open`, manifest round-trip,
`put_nodes`/`put_edges_bulk`, configured exact/range lookups,
`index_statistics` + `SHOW INDEXES`, label/type discovery via
`UNWIND labels(n)` / `keys(n)` / `type(r)`, counts via Cypher,
typed traversal with byte decode, offline `visualize` HTML/SVG,
and temporal separation (`put_node_version` invisible to `get_node`,
readable through `temporal.open_read_view`).

## Findings (remediation backlog)

### F1 — Packaged temporal migration example fails on 0.16.0
Observation `packaged_examples[temporal-1]`: `ValueError:
migrate_time_indexed_edges requires an explicit current-edge delete`.
The shipped `temporal` example calls
`migrate_time_indexed_edges(delete_legacy=False)` without the now-required
explicit delete argument, so copy-paste fails.
Remediation: update the packaged `temporal` example and web migration
snippet to the 0.16.0 signature; keep one runnable minimal call.

### F2 — User guide names `GraphDB.read_view`, release uses `temporal.open_read_view`
Packaged `temporal` rules say “Use `GraphDB.read_view`…”, but probing shows
`hasattr(graph, "read_view") is false` (`old_read_view_exists`). The working
call is verbose:
`graph.temporal.open_read_view(graph, manifest=..., database_id=...,
valid_time=...)`. Same relocation applies to rules/claims
(`graph.rules.*`, `graph.epistemic.*`; `old_run_rules_exists`,
`old_maintain_truth_exists`, `old_explain_claim_exists` are all false).
Remediation: replace stale `GraphDB.read_view` / `run_rules` references with
the service paths and keep a short “old name → new call” mapping.

### F3 — String IDs rejected; bytes + manual decode everywhere
`get_node("alice")` raises `TypeError: Won't implicitly convert Unicode to
bytes`. Node/edge keys and `neighbors_by_edge_type` return `bytes`, requiring
`graph.key_to_string(...)` before string comparison. Quickstart uses bytes,
but string IDs are the natural first attempt.
Remediation: accept `str` in ID-taking reads (or raise an actionable error
naming `.encode()` / `node_key_to_bytes`), and surface the
encode/decode rule once in quickstart/backends.

### F4 — Node ID accessor looks like a method but is a property
`node.id` raises `AttributeError`; `node.get_id()` raises
`TypeError: 'str' object is not callable`; the working forms are the
`get_id` property and `to_dict()["id"]` (`node_property_get_id`,
`node_dot_id`, `node_call_get_id`). `Node(node_id=...)` vs `get_id` vs
Cypher `n.id`/`id(n)` is three spellings for one identity.
Remediation: document the canonical accessor in the `Node` docstring and
quickstart; consider an `id` alias or a clearer error.

### F5 — Opaque `repr` for graph objects and `RETURN n`
`repr(node)` is `<gestaltdb.graphdb.Node object at 0x...>` and
`MATCH (n) RETURN n` returns the same opaque objects. Property-bag queries
(`properties(n)`, `labels(n)`, `id(n)`) work, but bare-entity returns are
uninspectable in a REPL.
Remediation: implement a summarizing `__repr__` (id, labels, key
properties); nudge inspection docs toward
`id(n)/labels(n)/properties(n)` projections.

### F6 — Unindexed property lookups silently return empty
With data present but no index, `nodes_by_property`, `edges_by_property`,
`nodes_by_property_range`, and `count_nodes_by_property` return `[]`/`0`
(`unconfigured_*`). After `create_*_property_index`, the same calls return
rows. Silent empty reads as “no data” rather than “no index”.
Remediation: fail closed or warn with the missing index name and the
`create_*_index` / `index_statistics` / `SHOW INDEXES` fix.

### F7 — `range_query_nodes` is a documented stub and disagrees with range API
Docstring is literally “Example stub…”, and it returned `[]` after indexes
existed while `nodes_by_property_range` returned the row
(`legacy_range_query_nodes` vs `configured_node_range`).
Remediation: fix, deprecate, or remove it; do not leave a same-name trap
next to the working range helpers.

### F8 — “No whole-graph enumeration” contradicts working key iterators
Backends guidance says “there is no whole-graph enumeration API”, yet
`list(graph.nodes.keys())`, `list(graph.edges.keys())`,
`get_node_keys_generator`, and `iter_edge_ids` all enumerate keys, while
`list(graph.nodes)` raises `TypeError: 'NodeRepository' object is not
iterable` (`all_node_keys`, `all_edge_keys`, `iterate_node_repository`).
Remediation: correct the guide; document the `keys()` + `get_node(s)` /
`get_edge` pagination recipe and its ordering/key-type caveats.

### F9 — No context-manager support
`hasattr(graph, "__enter__") is false` (`context_manager_support`), so every
handle needs explicit `try/finally: graph.close()`, including reopened
handles. Users expect `with GraphDB.create/open(...)`.
Remediation: add `__enter__/__exit__` closing the handle, or keep manual
close but explain why once in quickstart/backends.

### F10 — No obvious graph-size inspection
`manifest["graph"]` carries `node_count: null, edge_count: null`
(`manifest_counts`); `index_statistics` reports only index definitions.
Totals require discovering `count_*` helpers or Cypher `count(*)`, neither
linked from the inspection narrative.
Remediation: publish one inspection recipe (Cypher counts + `count_*` +
key-listing) and state that manifest counts are not live totals.

### F11 — Cypher introspection needs a translation table
`SHOW LABELS`, `CALL db.labels()`, and bare `RETURN 1 AS n` all raise
`CypherSyntaxError`; the working equivalents are
`MATCH (n) UNWIND labels(n)...`, `UNWIND keys(n)...`,
`RETURN DISTINCT type(r)...`, and `MATCH`-rooted projections. Only
`SHOW INDEXES` worked directly.
Remediation: add an “inspect the graph with Cypher” snippet covering
labels, property keys, types, counts, and the `MATCH`-first requirement.

### F12 — `agent_docs search` misses relocated service APIs
`search "read_view"` and `search "run_rules"` return no usable hit for
`temporal.open_read_view` / `rules.run`, while `search "iter_node"` finds
only index iterators. Users following old names hit dead ends.
Remediation: index service-method cards and old→new aliases so search
resolves `read_view`, `run_rules`, `maintain_truth`, and `explain_claim`.

## Limits
No source reads, raw-store access, concurrency/scale tests, snapshot-format
migration, or browser-interaction checks. JSON observations are the source
of truth; narrative above is interpretation for remediation triage.
