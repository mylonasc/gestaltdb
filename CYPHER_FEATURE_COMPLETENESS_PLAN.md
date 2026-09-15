# Cypher Feature-Completeness Plan

Target: **openCypher read + write**, **compatible + extensions** (retain
`n.id` / `r.id` / `n.labels` / `r.source` / `r.target` / anchored `{id:}`),
**backend-aware simple atomicity** (no new session/Tx API this iteration).

## 1. Baseline

Pipeline (`src/gestaltdb/cypher*.py`, ~3,893 lines):

```text
query text
  -> cypher_parser.py:parse_ast()  (Lark LALR grammar + transformer)
  -> cypher_ast.py                 (canonical Query + legacy MatchQuery/...)
  -> cypher_semantics.py:analyze_query()  (Scope/Symbol/QueryAnalysis)
  -> cypher_plan.py:plan_query()/plan_staged_query()  (LogicalPlan)
  -> cypher_runtime.py:execute_plan()/_execute_staged() (streaming operators)
  -> cypher.py:execute() -> QueryResult(columns, records)
```

Supported: label/multi-label scans, multi-entry inline maps, anchored +
unanchored fixed-length patterns, `A|B` types, comma parts, chained `MATCH`,
`WHERE` + three-valued logic, general `RETURN`/`WITH` projections,
`DISTINCT`/alias-`ORDER BY`/`SKIP`/`LIMIT`, `count/collect/sum/avg/min/max`
with implicit grouping, `CALL pg.sample_typed_paths`.
Unsupported (`docs/cypher.rst`): `CREATE/SET/DELETE/MERGE`, `OPTIONAL MATCH`,
variable-length, path binding `p=(a)-->(b)`, general functions/`CASE`/
comprehensions/map-projection, `UNWIND`/`UNION`/subqueries, rel property maps,
quantified paths.

Known debt: dual AST + dual planner/executor (`execute_plan:393` branches,
fine-grained ops emitted but ignored in `cypher_plan.py:430-446`),
`BindingRow` Mapping shim, duplicated alias/order logic, `repr()` grouping
fallback (`cypher_runtime.py:1472`), manual span plumbing.

GitHub tracking: milestones `cypher-stage-0..6`, issues `[cypher-NN]`.
Dependencies are listed per issue; each stage gates on the previous stage's
exit criteria.

## 2. Design Principles

1. One canonical AST; legacy `MatchQuery/MultiMatchQuery/...` become adapters,
   then are deleted.
2. One operator pipeline: `LogicalPlan` is authoritative for execution.
   Operators implement `execute(rows, ctx) -> rows`; no plan recompute in the
   runtime.
3. New reusable modules only: `cypher_expr.py` (evaluator), `cypher_functions.py`
   (function registry), `cypher_path.py` (matchers/expanders),
   `cypher_write.py` (mutation batches). Parser = grammar only, semantics =
   scope only, runtime = execution only.
4. Errors stay `CypherSyntaxError`/`CypherSemanticError(ValueError)` with
   `SourceSpan`; no bare `ValueError` for new syntax.
5. Every stage keeps `GraphDB.query()` / `QueryResult` stable, keeps the 211
   existing Cypher tests green, and updates `docs/cypher.rst` (+ `AGENTS.md` /
   `EXAMPLES.md` deltas where the user-facing contract changes).

## 3. Stage 0 — Foundation `[cypher-01..03]`, milestone `cypher-stage-0`

* `[cypher-01]` Operator protocol + immutable rows. Introduce `Operator`
  protocol in `cypher_runtime.py`, make `BindingRow` immutable (drop `Mapping`
  shim, keep `from_row`/`with_bindings` helpers during migration), centralize
  `evaluate_expression`. Depends on: none.
* `[cypher-02]` Single executable plan path. Make non-staged `plan_query`
  emit the same operator set as `plan_staged_query`; delete dead-op branches
  (`continue:430-446`) and the `_plan_source_rows` recompute. Depends on: 01.
* `[cypher-03]` Conformance harness. New `tests/test_cypher_conformance.py`
  with `SUPPORTED` / `UNSUPPORTED_XFAIL` tables mapping openCypher clauses to
  tests; source-span error assertions. Depends on: 02.
* Exit: full suite green, `plan()` output stable, no behavior change.

## 4. Stage 1 — Expression Completeness `[cypher-04..06]`, `cypher-stage-1`

* `[cypher-04]` Extended expressions. Grammar+AST+semantics+eval for `CASE`,
  list slice/index, map access/subscript, `ListComprehension`, `reduce()`,
  pattern comprehension, `MapProjection`, quantified `all/any/none/single`,
  `exists(pattern|prop|map)`. New `cypher_expr.py`. Depends on: 03.
* `[cypher-05]` Scalar function registry. New `cypher_functions.py`
  (`FunctionDef{arity, scalar|agg, impl}`): `coalesce, id/elementId, type,
  labels, startNode/endNode, properties, head/last/size/length, toBoolean/
  toInteger/toFloat/toString, trim/replace/split/substring/left/right,
  abs/ceil/floor/round/sqrt/pow/rand, range/reverse/tail/keys/nodes/
  relationships`. Depends on: 04.
* `[cypher-06]` String/math/list function coverage + error typing (type errors
  vs null propagation, division-by-zero, regex errors). Depends on: 05.
* Exit: `RETURN n{.*, age: n.age+1}`, `[x IN xs WHERE .. | f(x)]`, `CASE`,
  `coalesce()` work; typed errors replace ad-hoc rejections.

## 5. Stage 2 — Read Composition `[cypher-07..10]`, `cypher-stage-2`

* `[cypher-07]` `OPTIONAL MATCH` (`OptionalMatchStep`, left-outer join,
  null-fill, chained optionals, null-aware post-filter). Depends on: 03.
* `[cypher-08]` `UNWIND list AS x` (`Unwind` operator; `NULL`/empty -> zero
  rows; param/list-expr args). Depends on: 07.
* `[cypher-09]` `UNION [ALL]` (column-name/coercion checks, per-branch
  `ORDER/SKIP/LIMIT` rules, `DISTINCT` dedup). Depends on: 08.
* `[cypher-10]` `CALL { ... }` correlated subqueries (`WITH`-import scoping,
  post-union aggregation); full params (`SKIP`/`LIMIT`, regex, `IN`); `USING`
  hints parsed-then-ignored. Depends on: 09.
* Exit: `MATCH..OPTIONAL..UNWIND..UNION..CALL{}` pipelines; scope-reset tests.

## 6. Stage 3 — Path Matching `[cypher-11..14]`, `cypher-stage-3`

* `[cypher-11]` Relationship property maps + `WHERE` on rel patterns
  (`-[r:T {p:1} WHERE r.x>..]->`; extend `NodePattern`/`PatternHop`). Depends
  on: 03.
* `[cypher-12]` Variable-length `-[*min..max]->` + `nodes()/relationships()/
  length()`; BFS expander in new `cypher_path.py` reusing `expand_typed`;
  edge-isomorphism default (no rel reuse per `MATCH`). Depends on: 11.
* `[cypher-13]` `shortestPath()` / `allShortestPaths()` + `SHORTEST k`
  selector over the path matcher. Depends on: 12.
* `[cypher-14]` Path binding `p=(a)-[:T]->(b)` (named-path reuse, `RETURN p`)
  + label expressions `(:A|B)` / `(:!A)`. Depends on: 12.
* Exit: old `*1..3` rejection becomes positive coverage; untyped var-length
  documented as canonical-scan (slower).

## 7. Stage 4 — Writes `[cypher-15..18]`, `cypher-stage-4`

Backend rule (kept simple): accumulate `WriteBatch{node_puts, edge_puts,
deletes, index_updates}` in new `cypher_write.py`; apply via existing
`GraphDB.put_*/delete_*` bulk APIs inside `graph.transaction()` when the
backend supports it (`LMDBStore`, transactional `PyRexStore`), otherwise
documented best-effort `put_*_bulk` after pre-validation (`DETACH` rel-check,
`MERGE` match-probe). No session/Tx API, no `IN TRANSACTIONS`, no `LOAD CSV`.

* `[cypher-15]` `CREATE` (+ path creation) + `SET`/`REMOVE` (`n.prop=`,
  `n:Label`, `n+=map`). Depends on: 03.
* `[cypher-16]` `DELETE` / `DETACH DELETE` (rel-existence check vs cascade).
  Depends on: 15.
* `[cypher-17]` `MERGE pattern [ON CREATE SET..][ON MATCH SET..]`
  (match-or-create within the per-query atomicity boundary). Depends on: 16.
* `[cypher-18]` `FOREACH (x IN list | ...)` write-only loop. Depends on: 17.
* Exit: write-then-read round-trips via `GraphDB.query`; per-backend atomicity
  documented.

## 8. Stage 5 — Planner, Indexes, Hardening `[cypher-19..20]`, `cypher-stage-5`

* `[cypher-19]` Planner: join ordering (small label-scan first), correct
  limit-pushdown, seek reuse (`nodes_by_label_property/range`,
  `edges_by_type_property/range`) for new clauses; uniqueness/existence
  constraint catalog stubs enforced for `MERGE` keys. Depends on: 14, 18.
* `[cypher-20]` Semantics hardening: full 3VL audit, coercion rules, `ORDER BY`
  nulls-first/last + stability, temporal/spatial stubs as typed unknown-function
  errors. Depends on: 19.
* Exit: `plan()` stable; perf suite for typed vs untyped expansion.

## 9. Stage 6 — Parity & Release `[cypher-21]`, `cypher-stage-6`

* `[cypher-21]` Remaining parity (`CALL proc YIELD` generalized, `SHOW INDEX`,
  GQL-quantifier errors with hints), `docs/cypher.rst` rewrite, `EXAMPLES.md`
  snippets, Sphinx build, `check_docs.py` + `doc_tool.py test-examples` green.
  Depends on: 20.
* Exit: conformance table green except explicitly deferred GQL items; release
  notes written.

## 10. Cross-Cutting Verification

Each issue must: add/extend tests under `tests/test_cypher*.py` (or the
conformance file), run `uv run pytest tests/test_cypher*.py -q`, run
`uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py`
when docs change, and keep `GraphDB.query(cypher, parameters)` backward
compatible.
