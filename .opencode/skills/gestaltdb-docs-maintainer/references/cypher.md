# Cypher Query Language Support & Limitations

**When to read:** Read this file when writing Cypher queries, modifying the parser/planner/runtime under `src/gestaltdb/query_engine/cypher/`, or debugging `GraphDB.query`.

---

## 1. Cypher Architecture

GestaltDB includes an embedded Cypher query processor:
- `graph.query(cypher_str, parameters=None)` returns a `QueryResult(columns, records)`.
- Iterating over `result` yields dictionaries mapping column names to values.
- Internal pipeline:
  - `parser.py`: Uses a Lark grammar; `parse_ast()` preserves an ordered canonical clause AST while legacy `parse()` returns runtime-compatible specialized objects. Shared errors live in `errors.py`.
  - `semantics.py`: Walks canonical clauses left-to-right with ordered scopes (`analyze_query`) for `MATCH`/`WHERE`/`WITH`/`RETURN` validation.
  - `plan.py`: Builds staged plans (`MatchStep`, `ProjectItems`) for `WITH` queries and an authoritative typed logical plan with a binding source and ordered operators.
  - `runtime.py`: Executes plans through typed binding rows and streaming or blocking result operators against `GraphDB`.
  - Modules under `src/gestaltdb/cypher*.py` are compatibility shims for existing import paths.

---

## 2. Supported Cypher Features

### Node Matching
- Labels: `MATCH (a:Person)` or multiple labels `MATCH (a:Person:Employee)`
- Multi-entry inline property filters: `MATCH (a:Person {status: "active", level: 2})`
- Parameterized inline filters: `MATCH (a:Person {country: $country})`

### Relationship Patterns
- Directed typed or untyped traversal: `MATCH (a)-[:works_at]->(b)` or `MATCH (a)-->(b)`.
- Anonymous elements: `MATCH ()-[r:works_at]->() RETURN r`.
- Labels and properties may appear on every node in a fixed path.
- Unanchored multi-hop paths and comma-separated pattern parts are supported.
- Chained traversal (endpoints bound by variable):
  ```cypher
  MATCH (a:Person {name: "Alice"})
  MATCH (b:Company)
  MATCH (a)-[:works_at]->(b)
  RETURN a.name, b.name
  ```
- Undirected traversal: `MATCH (a)-[:knows]-(b)`.
- Anchored pattern: `MATCH (a {id: "drug-1"})-[:targets]->(b) RETURN b.id`
- `WHERE` may follow any `MATCH` clause; each `WHERE` filters its own stage.
- Untyped relationship patterns scan canonical edge records and are slower than typed adjacency expansion.

### WITH and Variable Scope
- `WITH` projects intermediate values and replaces the variable scope: `MATCH (p:Person) WITH p AS person RETURN person.name`.
- Only projected variables and aliases survive; referencing a dropped variable downstream is a semantic error.
- `WITH` may carry its own `WHERE` (which runs after projection and `DISTINCT`), `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT`.
- Later `MATCH` clauses may correlate on retained entity variables, and each `MATCH` starts a new relationship uniqueness scope.
- `WITH *` carries all currently bound named variables; queries must still start with `MATCH`.

### WHERE Clauses
- Comparisons: `=`, `<>`, `!=`, `<`, `<=`, `>`, `>=`, and `=~`
- Arithmetic and property-to-property comparisons
- Logical `NOT`, `AND`, `XOR`, and `OR`, with parentheses and standard precedence
- String predicates: `STARTS WITH`, `ENDS WITH`, and `CONTAINS`
- Membership: `WHERE a.department IN ["Engineering", "Product"]`
- Nullity: `WHERE a.manager IS NOT NULL` and `WHERE b.closed_at IS NULL`
- Query parameters: `$param_name`

### RETURN and Modifiers
- Property projection: `RETURN a.name AS full_name, b.id AS target_id`
- General expressions: `RETURN n.age + 1 AS next`, literals, parameters, lists, and maps (unaliased expressions use deterministic rendered names)
- Star projection: `RETURN *` (must be the only projection item)
- Modifiers: `DISTINCT`, `ORDER BY <expression-or-alias> [ASC|DESC]`, `SKIP <n-or-parameter>`, `LIMIT <n-or-parameter>`
- Predicates use Cypher three-valued null logic. Use `IS NULL`, not `= null`.
- Predicate contexts require Boolean/null values; `null IN []` is false.
- `ORDER BY` is stable, uses Cypher's supported value hierarchy, and places null last ascending/first descending.
- Regular conversion functions reject unsupported value categories while malformed supported strings convert to null.

### Writes and Constraints
- `CREATE`, `SET`, `REMOVE`, `DELETE`/`DETACH DELETE`, `MERGE` with `ON CREATE`/`ON MATCH`, and write-only `FOREACH` loops.
- Writes run in a backend transaction when supported; terminal `RETURN ... LIMIT` does not truncate earlier side effects.
- Persisted, single-property node `UNIQUE` and `IS NOT NULL` constraints through `CREATE CONSTRAINT`, `DROP CONSTRAINT`, and `SHOW CONSTRAINTS`.
- `SHOW INDEX`/`SHOW INDEXES` lists configured property indexes with `entityType` and `properties` columns.
- Top-level procedure syntax is generalized and allowlisted: `pg.sample_typed_paths` supports parameters and `YIELD path AS alias`; unknown procedures/fields are semantic errors.
- Constraints validate existing data on creation and final node states for Cypher writes. Direct object and columnar writes do not enforce them.
- Exact/range node and typed relationship predicates reuse configured property indexes. Independent comma-separated node scans start with the smallest label population.

### Custom GestaltDB Procedures
- Path sampling procedure:
  ```cypher
  CALL pg.sample_typed_paths(["p1"], [{"edge_type": "knows", "sample_size": 2}]) YIELD path RETURN path
  ```

---

## 3. Explicit Unsupported Cypher Features

### Aggregation and Implicit Grouping
- Core six: `count(*)`, `count(expr)`, `collect`, `sum`, `avg`, `min`, `max`, plus aggregate `DISTINCT`.
- Non-aggregate projection expressions are implicit grouping keys; all-aggregate projections have one global group (even for empty input).
- Null inputs are ignored except by `count(*)`; empty global input yields `0`/`[]`/`0`/`None`/`None`/`None` across the six.
- `ORDER BY` in aggregate queries must reference projected outputs (planner normalizes to output `Variable` refs).
- Aggregates are rejected in `WHERE`, pattern property maps, nested positions, and scalar arithmetic; unknown functions are rejected.
- Execution: staged `Aggregate` operator with first-seen group order and `cypher_value_key` grouping/distinct identity.

Do **not** document or expect the following syntax to work:
- Explicit grouping (`GROUP BY`; Cypher grouping is implicit)
- Pattern comprehensions and `exists()` with a pattern
- GQL quantified path patterns (diagnostics suggest legacy `*min..max` syntax)
- Relationship or multi-property constraints
- Generic procedure calls beyond the documented sampling procedure and correlated `CALL { ... }`

---

## 4. Runnable Examples

### Querying with Parameters, Filtering, and Sorting
```python
from tempfile import TemporaryDirectory
from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import JSONSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/cypher_demo"), JSONSerializer())
    try:
        # Populate
        graph.put_node(Node(node_id="p1", labels=["Patient"], properties={"name": "Alice", "age": 45}))
        graph.put_node(Node(node_id="p2", labels=["Patient"], properties={"name": "Bob", "age": 62}))
        graph.put_node(Node(node_id="d1", labels=["Disease"], properties={"name": "Diabetes"}))

        graph.put_edge(Edge(edge_id="e1", source="p1", target="d1", properties={"type": "diagnosed_with"}))
        graph.put_edge(Edge(edge_id="e2", source="p2", target="d1", properties={"type": "diagnosed_with"}))

        # Query (Note: WHERE may follow any MATCH or WITH clause;
        # traversal relationship patterns use bare variables like (p)-[:rel]->(d))
        query = (
            'MATCH (p:Patient) '
            'MATCH (d:Disease) '
            'MATCH (p)-[:diagnosed_with]->(d) '
            'WHERE p.age >= $min_age '
            'RETURN p.name AS patient_name, p.age AS age, d.name AS condition '
            'ORDER BY p.name ASC '
            'LIMIT 10'
        )
        result = graph.query(query, parameters={"min_age": 40})

        print("Columns:", result.columns)
        for record in result:
            print(f"Record: {record['patient_name']} ({record['age']}) -> {record['condition']}")
    finally:
        graph.close()
```
