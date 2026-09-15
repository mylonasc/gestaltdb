# Cypher Clause Pipeline and Aggregation Features

## 1. Purpose

This document defines the next GestaltDB read-only Cypher features as independently trackable work items. It covers the migration from AST-specific execution to an authoritative typed operator pipeline, followed by clause-oriented parsing, `WITH`, clause-local `WHERE`, general projections, and aggregation.

The work is intentionally split into three delivery milestones. Each milestone should leave the existing Cypher subset fully usable and tested.

## 2. Context

GestaltDB currently has three distinct Cypher layers:

1. `cypher_parser.py` parses queries with Lark and lowers them into specialized runtime query classes.
2. `cypher_plan.py` creates logical operators for inspection, but those operators do not execute the query.
3. `cypher_runtime.py` dispatches on specialized query classes and independently implements scans, matching, filtering, projection, ordering, and pagination.

This architecture has supported the current read subset, but it creates several constraints:

- Planner and runtime behavior can drift because the generated logical plan is discarded by `cypher.execute()`.
- The current query model assumes consecutive `MATCH` clauses, one shared `WHERE`, and one terminal `RETURN`.
- Projection syntax is limited to variables, property references, and `RETURN *` even though the expression evaluator supports richer expressions.
- Clause-local variable scopes do not exist, which prevents a correct `WITH` implementation.
- Aggregation would duplicate result-shaping behavior unless projection, grouping, sorting, and pagination become real operators first.
- Existing callers and tests inspect concrete results from `gestaltdb.cypher.parse()`, so replacing those objects directly would introduce an avoidable compatibility break.

The proposed end state is:

```text
query text
  -> canonical clause AST
  -> semantic analysis and scope resolution
  -> typed logical plan
  -> executable operator pipeline
  -> QueryResult(columns, records)
```

The public `GraphDB.query()` and `QueryResult` contracts remain unchanged.

## 3. Design Principles

1. The logical plan is authoritative for execution.
2. Clause order and clause-local scope are explicit in the AST and plan.
3. Existing storage scans, traversal helpers, caches, and index filtering are reused rather than rewritten.
4. Blocking operations such as sorting and aggregation are explicit; streaming is retained where semantics permit it.
5. Existing `parse()` results remain compatible for syntax supported before this work.
6. New clause-oriented syntax is represented by a canonical `parse_ast()` API.
7. Aggregation follows Cypher implicit grouping rather than adding non-Cypher `GROUP BY` syntax.
8. Source-located syntax and semantic errors remain part of the parser contract.
9. Every milestone must pass the existing Cypher regression suite before later features begin.

## 4. Feature Index

| ID | Feature | Delivery | Direct dependencies |
|---|---|---|---|
| CY-001 | Typed runtime rows and operator contracts | Milestone 1 | None |
| CY-002 | Executable result operators | Milestone 1 | CY-001 |
| CY-003 | Authoritative plan execution | Milestone 1 | CY-001, CY-002 |
| CY-004 | Canonical clause AST and compatible parser API | Milestone 2 | None |
| CY-005 | Clause-aware semantic analysis and scopes | Milestone 2 | CY-004 |
| CY-006 | General projection expressions | Milestone 2 | CY-002, CY-004, CY-005 |
| CY-007 | `WITH` and clause-local `WHERE` | Milestone 2 | CY-003, CY-005, CY-006 |
| CY-008 | Core aggregation and implicit grouping | Milestone 3 | CY-003, CY-005, CY-006, CY-007 |
| CY-009 | Shared Cypher value identity | Milestone 3 | CY-001 |
| CY-010 | Diagnostics, documentation, and tooling alignment | Every milestone | The feature being documented |

## 5. Dependency Graph

```text
CY-001 Typed runtime rows
  -> CY-002 Executable result operators
  -> CY-003 Authoritative plan execution

CY-004 Canonical clause AST
  -> CY-005 Clause-aware semantics
  -> CY-006 General projections

CY-003 + CY-005 + CY-006
  -> CY-007 WITH and clause-local WHERE

CY-001
  -> CY-009 Shared value identity

CY-003 + CY-005 + CY-006 + CY-007 + CY-009
  -> CY-008 Core aggregation

Each completed feature
  -> CY-010 Documentation and tooling alignment
```

CY-001 through CY-003 and CY-004 through CY-006 can be developed with limited overlap, but CY-007 must not ship until both tracks are integrated. CY-008 depends on the completed clause pipeline so aggregation works consistently in both `WITH` and `RETURN`.

## 6. Feature Definitions

### 6.1 CY-001: Typed Runtime Rows and Operator Contracts

**Objective:** Replace implicit dictionary-shaped runtime rows with typed internal row objects and define the execution contracts used by physical operators.

**Current limitation:** Runtime helpers exchange dictionaries containing `bindings`, `current_node_id`, and sometimes `used_relationship_ids`. The shape is implicit and varies between helpers.

**Proposed improvement:** Introduce typed row dataclasses and operator protocols while adapting existing matching helpers incrementally.

Candidate internal types:

```python
@dataclass(frozen=True, slots=True)
class BindingRow:
    bindings: dict[str, object]
    current_node_id: bytes | None = None
    used_relationship_ids: frozenset[bytes] = frozenset()


@dataclass(frozen=True, slots=True)
class ProjectedRow:
    values: dict[str, object]
    source_bindings: dict[str, object] | None = None
```

`source_bindings` temporarily preserves existing support for ordering by a matched expression that is not itself returned. A later standards review may narrow that behavior, but this migration should not change it accidentally.

**Implementation:**

1. Add typed row classes to `src/gestaltdb/cypher_runtime.py` or a small dedicated runtime types module if import cycles require it.
2. Define typed protocols for binding, projection, and result operators.
3. Convert scan and pattern helpers to accept and return typed rows.
4. Keep narrow adapters around directly tested legacy runtime helpers until all execution paths use typed rows.
5. Preserve query-scoped `QueryContext` node and edge hydration caches.

**Compatibility requirements:**

- Do not change `GraphDB.query()` or `QueryResult`.
- Do not change the values bound to node and relationship variables.
- Preserve relationship identity by stable edge ID.
- Preserve anonymous pattern behavior and textual `RETURN *` ordering.

**Acceptance criteria:**

- Runtime row keys are no longer repeated as string literals across the execution pipeline.
- Existing node, relationship, anchored, and generalized path queries return identical records.
- Relationship reuse remains prohibited within one `MATCH` group and permitted again in a later `MATCH` group.
- Existing query-scoped hydration behavior remains intact.

### 6.2 CY-002: Executable Result Operators

**Objective:** Move projection and result shaping out of `materialize_results()` into composable, typed operators.

**Current limitation:** `materialize_results()` contains projection, sorting, distinct handling, skipping, limiting, and decisions about streaming. Logical `Project`, `Distinct`, `Sort`, `Skip`, and `Limit` operators are descriptive only.

**Proposed improvement:** Add executable operators with explicit streaming or blocking behavior.

Required operators:

| Operator | Behavior | Execution mode |
|---|---|---|
| `ProjectOperator` | Evaluate projection expressions and assign output names | Streaming |
| `DistinctOperator` | Retain the first row for each result value key | Streaming with a seen set |
| `SortOperator` | Apply stable multi-key ordering | Blocking |
| `SkipOperator` | Skip a resolved number of rows | Streaming |
| `LimitOperator` | Stop after a resolved number of rows | Streaming |

**Implementation:**

1. Extract each result-shaping concern into an operator.
2. Resolve literal and parameterized `SKIP` and `LIMIT` through `QueryContext`.
3. Preserve the existing validation that pagination values are non-negative integers and not booleans.
4. Retain `materialize_results()` as a compatibility wrapper until direct runtime callers have migrated.
5. Make blocking boundaries visible in the executable plan.

**Operator ordering:**

The semantic clause order is:

```text
Project or Aggregate
  -> Distinct
  -> Sort
  -> Skip
  -> Limit
```

During the compatibility migration, ordering by non-projected expressions may require `ProjectedRow.source_bindings`. Tests must lock down the current behavior before internal operation order changes.

**Acceptance criteria:**

- `LIMIT 0` consumes no source rows.
- A plain `LIMIT` preserves early termination.
- A plain `SKIP` does not force full materialization.
- Multi-key sorting remains stable.
- Ascending nulls remain last and descending nulls remain first unless a separate standards change is approved.
- Existing alias-aware `ORDER BY` behavior remains compatible.

### 6.3 CY-003: Authoritative Plan Execution

**Objective:** Make `cypher.execute()` execute a planner-derived pipeline instead of dispatching directly on specialized parsed query classes.

**Current limitation:** `cypher.execute()` calls `plan_query(parsed)` and discards the result. It then selects one of four AST-specific runtime entry points.

**Proposed improvement:** Separate semantic logical operators from physical execution where needed, then lower the logical plan into executable operators.

Target flow:

```python
parsed = parse_ast_or_legacy(query)
logical_plan = plan_query(parsed)
executable_plan = lower_plan(logical_plan)
records = execute_plan(executable_plan, context)
```

**Implementation:**

1. Add explicit executable match operators for node patterns, relationship patterns, anchored patterns, and generalized paths.
2. Reuse `node_scan_ids`, relationship scan helpers, and `apply_*_pattern_clause` implementations behind those operators.
3. Add an explicit relationship-isomorphism scope reset between textual `MATCH` clauses.
4. Enrich under-specified logical operators or add clause-level logical operators where the current objects lack variables, direction, arguments, or correlation semantics.
5. Add `lower_plan()` and `execute_plan()`.
6. Convert specialized `execute_node_scan`, `execute_relationship_scan`, `execute_match`, and `execute_multi_match` functions into compatibility wrappers over the plan path.
7. Switch `cypher.execute()` to the authoritative plan path only after parity tests pass.
8. Route `pg.sample_typed_paths` through an executable procedure operator without changing its public result.

**Correctness constraints:**

- Index seeks produce candidate IDs; final filters remain required for correctness.
- Bound variables correlate later patterns instead of being rescanned.
- Disconnected patterns retain Cartesian product behavior.
- Incoming and undirected relationships retain orientation behavior, including one result for an undirected self-loop.
- Untyped relationships continue using canonical edge records.
- The plan must represent the boundary between comma-separated patterns in one `MATCH` and patterns in separate `MATCH` clauses.

**Acceptance criteria:**

- Removing or changing a planned operator changes execution, proving the plan is authoritative.
- `cypher.execute()` contains no query-class-specific scan/traversal dispatch.
- Existing logical plan inspection remains available through `cypher.plan()`.
- All current Cypher tests pass without expected-result changes.

### 6.4 CY-004: Canonical Clause AST and Compatible Parser API

**Objective:** Represent a query as an ordered sequence of clauses while preserving the existing `parse()` contract for old syntax.

**Current limitation:** Parsing lowers directly into one of several specialized runtime query classes. Those classes duplicate result fields and cannot represent repeated projection and scope boundaries.

**Proposed improvement:** Add a canonical clause AST returned by `parse_ast(query)`.

Candidate AST nodes:

| Node | Responsibility |
|---|---|
| `Query` | Ordered clause sequence and source text |
| `MatchClause` | One textual `MATCH`, containing one or more patterns |
| `WhereClause` | One filter attached to the preceding `MATCH` or `WITH` |
| `WithClause` | Projection, scope boundary, and local result modifiers |
| `ReturnClause` | Final projection and local result modifiers |
| `ProjectionItem` | Expression and optional alias |
| `FunctionCall` | Function name, arguments, and argument-level `DISTINCT` |
| `Wildcard` | Projection wildcard or the argument in `count(*)` |
| `SourceSpan` | Exact source range for diagnostics |

Existing `NodePattern`, `PatternHop`, and `PathPatternClause` types should be reused initially to avoid rewriting the pattern parser.

**Parser APIs:**

```python
def parse_ast(query: str) -> Query | SampleTypedPathsCall:
    """Return the canonical clause-oriented representation."""


def parse(query: str) -> LegacyQuery | SampleTypedPathsCall:
    """Preserve specialized results for syntax supported before this feature."""
```

For an existing query shape, `parse()` must continue returning the same concrete query type and compatible fields. For new syntax that cannot be represented by a legacy query class, `parse()` should raise a located `CypherSemanticError` instructing callers to use `parse_ast()`. `GraphDB.query()` and `cypher.plan()` should use the canonical path and therefore support the new syntax.

**Grammar direction:**

```text
query          := clause+ [";"]
clause         := match_clause
                | where_clause
                | with_clause
                | return_clause

match_clause   := MATCH pattern ("," pattern)*
where_clause   := WHERE expression
with_clause    := WITH [DISTINCT] projection_items result_modifiers
return_clause  := RETURN [DISTINCT] projection_items result_modifiers
```

The grammar should parse an ordered clause stream and leave legal clause sequencing to semantic analysis so errors can identify the offending clause precisely.

**Compatibility requirements:**

- Preserve `parse_literal()` and `split_top_level_args()`.
- Preserve existing syntax and semantic exception classes.
- Preserve specialized `NodeScanQuery`, `RelationshipScanQuery`, `MatchQuery`, and `MultiMatchQuery` results for old syntax.
- Preserve `match_group_ids` when lowering canonical clauses to `MultiMatchQuery`.
- Preserve current dataclass equality and repr behavior when source spans are added.

**Acceptance criteria:**

- `parse_ast()` preserves exact textual clause order.
- Comma-separated patterns remain grouped in one `MatchClause`.
- Separate textual `MATCH` clauses remain distinguishable.
- `WITH` and `RETURN` each own their `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT` modifiers.
- All existing parser compatibility tests pass unchanged.

### 6.5 CY-005: Clause-Aware Semantic Analysis and Scopes

**Objective:** Validate queries from left to right and assign an explicit variable scope to each clause boundary.

**Current limitation:** Semantic validation collects all variables from all `MATCH` patterns, then validates one shared `WHERE` and one `RETURN`. It cannot model variables being renamed or removed.

**Proposed improvement:** Add a semantic analysis pass over the canonical AST.

**Scope rules:**

1. `MATCH` adds its named node and relationship variables to the incoming scope.
2. A `WHERE` after `MATCH` reads that updated scope and does not alter it.
3. `WITH` expressions read the incoming scope and replace it with their projected output names.
4. A `WHERE` after `WITH` reads the `WITH` output scope.
5. `RETURN` expressions read the current scope and terminate the query.
6. Anonymous pattern elements never enter scope.
7. `WITH *` and `RETURN *` expand variables in deterministic scope order.
8. An alias becomes the downstream name; the pre-alias variable does not survive unless separately projected.

**Clause sequencing rules:**

- A query must terminate with `RETURN` or the supported procedure form.
- `WHERE` must immediately follow `MATCH` or `WITH`.
- `RETURN` must be terminal except for modifiers owned by that clause.
- Duplicate output names are rejected within each projection clause.
- Node and relationship variables cannot use the same name in one active scope.

**Implementation:**

1. Introduce a typed scope model containing ordered symbol definitions and entity kind where known.
2. Walk canonical clauses in source order.
3. Validate all expression variable references against the applicable scope.
4. Resolve projection output names and wildcard expansion during analysis or logical planning.
5. Attach analyzed scope information to a semantic query model or return it as a separate immutable analysis result.
6. Keep parsing and semantic analysis separate enough that syntax and scope errors remain distinguishable.

**Acceptance criteria:**

- `WITH n AS person RETURN person` succeeds.
- `WITH n AS person RETURN n` fails as out of scope.
- `WITH n.age AS age WHERE age >= 18 RETURN age` succeeds.
- Multiple `WITH` clauses replace scope in order.
- `RETURN *` after `WITH` exposes only the current scope.
- Errors point to the relevant reference rather than the first matching text in the query.

### 6.6 CY-006: General Projection Expressions

**Objective:** Allow expressions, literals, parameters, and supported function calls in `WITH`, `RETURN`, and `ORDER BY`.

**Current limitation:** General expressions are accepted in `WHERE`, but projection and ordering grammar is limited to variables and property references.

**Proposed improvement:** Reuse the existing expression AST and evaluator for projections.

Initial supported examples:

```cypher
MATCH (n:Person)
RETURN n.age + 1 AS age_next_year
```

```cypher
MATCH (n:Person)
WITH n, $minimum AS minimum
WHERE n.age >= minimum
RETURN n.name
```

```cypher
MATCH (n:Person)
RETURN n.name, 1 AS rank
ORDER BY n.age + 1 DESC
```

**Implementation:**

1. Change projection grammar from a restricted `projection` rule to `"*" | expression`.
2. Store expression and alias in `ProjectionItem`.
3. Add deterministic rendering for unaliased output column names.
4. Extend `Project` logical operators to carry typed projection items rather than only output strings.
5. Reuse `evaluate_expression()` for non-aggregate expressions.
6. Validate output-name uniqueness after aliases and expression rendering are resolved.
7. Reserve function-call AST support for known aggregate functions in CY-008; unknown scalar functions remain unsupported unless separately defined.

**Initial wildcard rule:**

Allow `WITH *` or `RETURN *` as the sole projection item. Mixed wildcard projections such as `RETURN *, n.id` should remain unsupported until their output ordering and duplicate-name semantics are explicitly defined.

**Acceptance criteria:**

- Arithmetic, literals, parameters, lists, and maps can be projected.
- Projection errors use the same runtime type rules as equivalent `WHERE` expressions.
- Aliases are available to the owning clause's `ORDER BY` and to later clauses after `WITH`.
- Existing variable and property projection column names remain unchanged.

### 6.7 CY-007: `WITH` and Clause-Local `WHERE`

**Objective:** Add composable query stages with explicit projection boundaries, local filtering, and local result modifiers.

**Current limitation:** All `MATCH` clauses must precede one shared `WHERE`, and only a final `RETURN` can project values.

**Proposed improvement:** Plan each `WITH` as a projection and scope boundary in the executable pipeline.

Supported clause shape:

```text
Argument
  -> MatchGroup
  -> FilterAfterMatch
  -> ProjectWith
  -> DistinctWith
  -> SortWith
  -> SkipWith
  -> LimitWith
  -> FilterAfterWith
  -> MatchGroup
  -> ...
  -> ProjectReturn
  -> DistinctReturn
  -> SortReturn
  -> SkipReturn
  -> LimitReturn
  -> Result
```

Examples:

```cypher
MATCH (p:Person)
WHERE p.active = true
WITH p.department AS department, p
WHERE department IS NOT NULL
RETURN department, p.name
```

```cypher
MATCH (p:Person)
WITH p
ORDER BY p.score DESC
LIMIT 10
MATCH (p)-[:MEMBER_OF]->(team)
RETURN p.name, team.name
```

**Implementation:**

1. Lower each `MatchClause` to a match group with an explicit relationship-isomorphism scope.
2. Lower an immediately following `WhereClause` to a filter in the correct position.
3. Lower each `WithClause` to project, distinct, sort, skip, and limit operators owned by that clause.
4. Start the next clause with only the projected `WITH` bindings.
5. Support repeated `MATCH`, `WHERE`, and `WITH` stages before one terminal `RETURN`.
6. Ensure later `MATCH` patterns can correlate on variables retained by `WITH`.

**Compatibility requirements:**

- Queries currently written as `MATCH ... MATCH ... WHERE ... RETURN ...` retain their meaning.
- Existing final `RETURN` modifier behavior remains compatible.
- `GraphDB.query()` returns the same `QueryResult` shape.
- `parse()` compatibility behavior is as defined in CY-004; execution uses `parse_ast()`.

**Acceptance criteria:**

- Multiple `WITH` stages execute in source order.
- Clause-local filters observe the correct scope.
- `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT` on `WITH` execute before subsequent clauses.
- Variables omitted by `WITH` cannot be referenced downstream.
- A retained node variable can anchor a later traversal without being rescanned.
- Existing relationship-isomorphism tests continue to pass.

### 6.8 CY-008: Core Aggregation and Implicit Grouping

**Objective:** Implement `count`, `collect`, `sum`, `avg`, `min`, and `max` in `WITH` and `RETURN`.

**Current limitation:** Function calls and aggregate projections are not represented by the parser or runtime.

**Supported forms:**

```cypher
RETURN count(*)
RETURN count(n.value)
RETURN count(DISTINCT n.value)
RETURN collect(n.value)
RETURN sum(n.value), avg(n.value), min(n.value), max(n.value)
RETURN n.kind, count(*) AS total
```

Aggregate-argument `DISTINCT` applies independently from clause-level `WITH DISTINCT` or `RETURN DISTINCT`.

**Grouping model:**

- If a projection contains any aggregate, every non-aggregate projection expression is an implicit grouping key.
- A projection containing only aggregates has one global group, including for empty input.
- A grouped projection produces no rows when its input is empty.
- Null is a valid grouping key.
- Group order follows first appearance unless an `ORDER BY` is present.

**Null and empty-input semantics:**

| Aggregate | Treatment of null input | Empty or all-null result |
|---|---|---|
| `count(*)` | Counts every row | `0` |
| `count(expr)` | Ignores null | `0` |
| `collect(expr)` | Ignores null | `[]` |
| `sum(expr)` | Ignores null | `0` |
| `avg(expr)` | Ignores null | `None` |
| `min(expr)` | Ignores null | `None` |
| `max(expr)` | Ignores null | `None` |

**Type rules:**

- `sum` and `avg` accept integers and floats but reject booleans.
- `sum` preserves integer output when all inputs are integers.
- `avg` uses numeric division.
- `min` and `max` require mutually comparable non-null values.
- `count` accepts every value type.
- `collect` preserves input order after null removal and aggregate-level deduplication.

**Semantic restrictions:**

- Only `count` accepts `*`.
- `count(DISTINCT *)` is invalid.
- Nested aggregate calls are invalid.
- Aggregate calls are invalid in `MATCH` property maps and pre-aggregation `WHERE` clauses.
- Filtering aggregated aliases is performed by a `WHERE` after an aggregating `WITH`.
- Explicit `GROUP BY` remains unsupported because Cypher grouping is implicit.
- General scalar function calls remain unsupported unless added as a separate feature.

**Implementation:**

1. Parse function calls and argument-level `DISTINCT` into `FunctionCall` nodes.
2. Recognize aggregate function names case-insensitively during semantic analysis.
3. Detect aggregate queries and derive grouping keys from non-aggregate projection items.
4. Add a typed logical aggregate operator.
5. Add a blocking executable aggregate operator with per-function accumulator state.
6. Use the shared value identity rules from CY-009 for grouping and aggregate `DISTINCT`.
7. Apply the owning clause's distinct, sort, skip, and limit operators after aggregation.

**Acceptance criteria:**

- All six aggregates work globally and with one or more grouping keys.
- Aggregation works in both `WITH` and `RETURN`.
- Empty input and all-null input match the documented table.
- Aggregate `DISTINCT` handles scalars, lists, maps, nodes, and edges deterministically.
- `True` and `1` are not merged as one distinct Cypher value.
- Aggregate aliases can be used by the owning clause's `ORDER BY` and later clauses after `WITH`.

### 6.9 CY-009: Shared Cypher Value Identity

**Objective:** Define one reusable equality and hash key model for grouping, aggregate `DISTINCT`, and result-level `DISTINCT`.

**Current limitation:** Result distinctness recursively converts lists and maps, but Python equality can merge booleans with integers and graph entity handling can depend on object identity.

**Proposed improvement:** Add one canonical value-key helper used wherever rows or values must be grouped or deduplicated.

Required behavior:

| Value | Canonical identity |
|---|---|
| `None` | Tagged null value |
| Boolean | Tagged separately from numeric values |
| Integer/float | Numeric value under the supported Cypher equality rule |
| String | String value |
| List | Ordered recursive keys |
| Map | Key-sorted recursive entries |
| Node | Entity kind plus stable node ID |
| Edge | Entity kind plus stable edge ID |

**Implementation:**

1. Add a private `cypher_value_key(value)` helper in the runtime value layer.
2. Replace result `DISTINCT` key construction with the shared helper.
3. Use the helper for aggregate grouping keys and aggregate-level `DISTINCT`.
4. Add focused tests for nested values, entities, nulls, and boolean/numeric separation.

**Acceptance criteria:**

- Equal hydrated entities deduplicate even if represented by different Python instances.
- Nodes and edges with equal raw IDs remain different entity kinds.
- Nested lists and maps are supported as grouping and distinct values.
- Boolean values are not conflated with integers.

### 6.10 CY-010: Diagnostics, Documentation, and Tooling Alignment

**Objective:** Keep errors, user documentation, agent documentation, and benchmark tooling aligned as each feature becomes available.

**Diagnostics implementation:**

1. Capture Lark source metadata when canonical AST nodes are built.
2. Add optional non-comparing, non-repr source spans to expression nodes where precise semantic locations are needed.
3. Change semantic error construction to use source spans instead of `query.find()` where possible.
4. Preserve `CypherSyntaxError` and `CypherSemanticError` fields: `line`, `column`, `offset`, and `source`.

**Documentation locations:**

- `docs/cypher.rst`
- `AGENTS.md`
- `EXAMPLES.md`
- `src/gestaltdb/agent_skill/SKILL.md`
- `src/gestaltdb/agent_skill/references/cypher.md`
- `.opencode/skills/gestaltdb-docs-maintainer/references/cypher.md`
- `CHANGELOG.md`

**Tooling locations:**

- `.opencode/skills/gestaltdb-agent-performance-benchmarking/scripts/agent_benchmarking/trace_analysis.py`

The trace analyzer currently treats `WITH` and `COUNT(...)` as unsupported. Remove those classifications only when the corresponding runtime milestone ships.

The shipped skill also has an existing stale statement that comma-separated pattern parts are unsupported. Correct it during Milestone 1 documentation maintenance.

**Acceptance criteria:**

- User and agent documentation advertise only shipped syntax.
- Unsupported-feature lists are updated in the same milestone as implementation.
- Multiline scope errors point to the offending downstream reference.
- Documentation checks, runnable examples, and Sphinx builds pass.

## 7. Delivery Milestones

### 7.1 Milestone 1: Authoritative Typed Operators

**Included features:** CY-001, CY-002, CY-003, and the applicable portion of CY-010.

**Outcome:** Existing Cypher syntax behaves the same, but execution is controlled by a typed planner-derived pipeline.

**Primary files:**

- `src/gestaltdb/cypher.py`
- `src/gestaltdb/cypher_plan.py`
- `src/gestaltdb/cypher_runtime.py`
- `tests/test_cypher_operator_pipeline.py`

**Implementation sequence:**

1. Characterize current runtime behavior with parity tests.
2. Introduce typed runtime rows and compatibility adapters.
3. Extract executable result operators.
4. Add executable match and procedure operators using existing helpers.
5. Add explicit relationship-isomorphism reset operators.
6. Lower logical plans to executable plans.
7. Switch `cypher.execute()` to execute the plan.
8. Remove AST-specific dispatch only after complete parity.

**Milestone gate:**

- No new query syntax is required.
- Existing result records and columns remain unchanged.
- Existing early-limit and index tests pass.
- A dedicated test proves that the generated plan controls execution.

### 7.2 Milestone 2: Clause AST, Scoping, and `WITH`

**Included features:** CY-004, CY-005, CY-006, CY-007, and the applicable portion of CY-010.

**Outcome:** GestaltDB supports ordered query stages, general projection expressions, clause-local filters, and `WITH` while preserving legacy `parse()` results for existing syntax.

**Primary files:**

- `src/gestaltdb/cypher_ast.py`
- `src/gestaltdb/cypher_parser.py`
- `src/gestaltdb/cypher_plan.py`
- `src/gestaltdb/cypher_runtime.py`
- `src/gestaltdb/cypher.py`
- `tests/test_cypher_parser.py`
- `tests/test_cypher_with.py`

**Implementation sequence:**

1. Add canonical clause and projection AST nodes.
2. Add `parse_ast()` and source spans.
3. Lower canonical old-syntax queries back to legacy `parse()` results.
4. Add ordered clause parsing and semantic clause validation.
5. Add explicit scope analysis and wildcard expansion.
6. Add general projection expression execution.
7. Plan and execute `WITH` boundaries and clause-local modifiers.
8. Add clause-local `WHERE` after `MATCH` and `WITH`.
9. Switch `cypher.plan()` and `cypher.execute()` to canonical parsing.

**Milestone gate:**

- Existing direct callers of `parse()` retain compatible objects for old syntax.
- `GraphDB.query()` supports `WITH` and clause-local `WHERE`.
- Dropped and renamed variables are enforced at semantic-analysis time.
- New errors contain correct source locations.

### 7.3 Milestone 3: Core Aggregation

**Included features:** CY-008, CY-009, and the applicable portion of CY-010.

**Outcome:** The core six aggregates execute in `WITH` and `RETURN` with implicit grouping, null handling, aggregate `DISTINCT`, and documented empty-input behavior.

**Primary files:**

- `src/gestaltdb/cypher_ast.py`
- `src/gestaltdb/cypher_parser.py`
- `src/gestaltdb/cypher_plan.py`
- `src/gestaltdb/cypher_runtime.py`
- `tests/test_cypher_aggregation.py`

**Implementation sequence:**

1. Add shared value identity and migrate result-level `DISTINCT` to it.
2. Parse function calls, `count(*)`, and argument-level `DISTINCT`.
3. Add aggregate placement and nesting validation.
4. Derive grouping keys from projection items.
5. Add logical and executable aggregate operators.
6. Implement the six accumulators and empty-input behavior.
7. Add aggregation through `WITH`, post-aggregation filtering, and ordering.
8. Update support matrices and benchmark syntax classification.

**Milestone gate:**

- Core aggregates pass global, grouped, distinct, null, and empty-input tests.
- Aggregate queries use the same clause-local result operators as non-aggregate queries.
- Existing non-aggregate query behavior remains unchanged.

## 8. Test Plan

### 8.1 Existing Regression Suites

Every milestone must run:

```bash
uv run pytest tests/test_cypher.py tests/test_cypher_parser.py tests/test_cypher_runtime_expanded.py -q
```

Critical existing behaviors include:

- Exact and range index candidate scans followed by correctness filters.
- Early `LIMIT` termination.
- Parameter validation.
- Three-valued null logic.
- Alias-aware ordering.
- Incoming, outgoing, undirected, and self-loop traversal.
- Typed and untyped generalized paths.
- Comma-separated joins and Cartesian products.
- Relationship isomorphism within and across `MATCH` groups.
- Deterministic `RETURN *` ordering.
- Direct parser, planner, and runtime helper compatibility.

### 8.2 New Milestone Suites

| Test file | Primary coverage |
|---|---|
| `tests/test_cypher_operator_pipeline.py` | Executable plan authority, typed operators, streaming and blocking behavior |
| `tests/test_cypher_with.py` | Clause order, scopes, aliases, wildcard propagation, clause-local modifiers |
| `tests/test_cypher_aggregation.py` | Core six aggregates, grouping, nulls, empty input, aggregate `DISTINCT` |

`FakeCypherGraph` currently lives in `tests/test_cypher.py` and is imported by another test module. It may be moved to `tests/cypher_helpers.py` during Milestone 1 if that reduces coupling, but this cleanup is not required for the feature.

### 8.3 Focused Commands

Milestone 1:

```bash
uv run pytest tests/test_cypher_operator_pipeline.py tests/test_cypher_parser.py -q
```

Milestone 2:

```bash
uv run pytest tests/test_cypher_with.py tests/test_cypher_parser.py -q
```

Milestone 3:

```bash
uv run pytest tests/test_cypher_aggregation.py tests/test_cypher_with.py tests/test_cypher_parser.py -q
```

Full verification after each milestone:

```bash
uv run pytest
uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py
uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py test-examples
uv run sphinx-build -W -b html docs docs/_build/html
```

## 9. Compatibility Contract

The following contracts must remain stable throughout the work:

1. `GraphDB.query(cypher, parameters=None)` remains the user-facing execution API.
2. `QueryResult.columns` remains an ordered tuple of output names.
3. `QueryResult.records` remains a list of dictionaries keyed by output name.
4. Iterating a `QueryResult` continues to yield record dictionaries.
5. Existing query text produces compatible columns, values, ordering, and exceptions.
6. Existing `parse()` query shapes retain their specialized result classes and fields.
7. `parse_ast()` becomes the canonical representation for new syntax and internal execution.
8. Mutation remains unsupported; this plan does not add `CREATE`, `MERGE`, `SET`, `DELETE`, or `REMOVE`.

## 10. Explicit Non-Goals

This feature set does not include:

- Mutating Cypher clauses.
- `OPTIONAL MATCH`.
- Variable-length or quantified paths.
- Path binding values.
- Relationship property maps.
- `UNWIND`, `UNION`, or subqueries.
- `CASE`, list comprehensions, or map projections.
- General scalar functions.
- Explicit `GROUP BY` syntax.
- Percentile or standard-deviation aggregate families.
- Cost-based optimization.
- Persisted or externally serialized logical plans.

## 11. Principal Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Current logical operators lack enough execution context | Add clause-level operators or enrich payloads before making plans authoritative |
| Planner migration changes traversal semantics | Run old and new execution paths against the same query corpus before switching |
| Relationship uniqueness boundaries are lost | Represent textual `MATCH` group resets explicitly and retain existing regressions |
| Result operators introduce unnecessary materialization | Mark operators as streaming or blocking and retain early-consumption tests |
| `ORDER BY` loses access to hidden bindings | Preserve source bindings in projected rows during compatibility migration |
| Legacy `parse()` callers break | Add `parse_ast()` and lower old syntax back to existing specialized classes |
| Semantic locations point to the wrong repeated identifier | Capture parser source spans instead of relying on text search |
| Python equality merges `True` and `1` | Use tagged Cypher value keys |
| Entity distinctness depends on object identity | Key nodes and edges by entity kind and stable ID |
| Aggregation semantics vary across implementations | Lock null, empty-input, grouping, and type behavior in focused tests and docs |
| Documentation advertises partially shipped syntax | Update support matrices only at each milestone gate |

## 12. Completion Definition

This feature set is complete when:

1. All current and new Cypher queries execute through an authoritative typed plan.
2. The canonical AST represents ordered `MATCH`, `WHERE`, `WITH`, and `RETURN` clauses.
3. Clause-aware semantic analysis enforces variable scope and alias boundaries.
4. General projection expressions execute in `WITH` and `RETURN`.
5. The core six aggregates work with implicit grouping and aggregate `DISTINCT`.
6. `parse()` compatibility and `GraphDB.query()`/`QueryResult` behavior are preserved as defined above.
7. Existing and new test suites pass.
8. User-facing, agent-facing, and maintainer documentation accurately describe the shipped subset.
