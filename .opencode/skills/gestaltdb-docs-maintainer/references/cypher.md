# Cypher Query Language Support & Limitations

**When to read:** Read this file when writing Cypher queries, modifying the Cypher parser/planner/runtime (`cypher_parser.py`, `cypher_plan.py`, `cypher_runtime.py`), or debugging `GraphDB.query`.

---

## 1. Cypher Architecture

GestaltDB includes an embedded, read-only Cypher query processor:
- `graph.query(cypher_str, parameters=None)` returns a `QueryResult(columns, records)`.
- Iterating over `result` yields dictionaries mapping column names to values.
- Internal pipeline:
  - `cypher_parser.py`: Generates an abstract syntax tree (`CypherQuery`, `MatchClause`, `WhereClause`, `ReturnClause`).
  - `cypher_plan.py`: Builds a cost-aware physical execution plan utilizing label and property indexes.
  - `cypher_runtime.py`: Executes plan steps against the underlying `GraphDB` store.

---

## 2. Supported Cypher Features

### Node Matching
- Labels: `MATCH (a:Person)` or multiple labels `MATCH (a:Person:Employee)`
- Inline property filters: `MATCH (a:Person {status: "active"})`
- Parameterized inline filters: `MATCH (a:Person {country: $country})`

### Relationship Patterns
- Directed traversal: `MATCH (a:Person)-[:works_at]->(b:Company)` (scans all matching relationships)
- Chained traversal (endpoints bound by variable):
  ```cypher
  MATCH (a:Person {name: "Alice"})
  MATCH (b:Company)
  MATCH (a)-[:works_at]->(b)
  RETURN a.name, b.name
  ```
- Undirected traversal: `MATCH (a:Person)-[:knows]-(b:Person)`
- Anchored pattern: `MATCH (a {id: "drug-1"})-[:targets]->(b) RETURN b.id`
- **Important syntax rule:** In chained traversal matches, write `MATCH (a)-[:TYPE]->(b)` with bare variable identifiers. Do not put inline labels on the target inside the traversal step (e.g. avoid `(a)-[:T]->(b:Label)`; use `MATCH (b:Label) MATCH (a)-[:T]->(b)` instead).
- **Important WHERE placement:** In multi-match queries, all `MATCH` clauses must precede the `WHERE` clause: `MATCH (...) MATCH (...) WHERE ... RETURN ...`.

### WHERE Clauses
- Comparisons: `=`, `<>`, `<`, `<=`, `>`, `>=`
- Logical `AND`
- Membership: `WHERE a.department IN ["Engineering", "Product"]`
- Nullity: `WHERE a.manager IS NOT NULL` and `WHERE b.closed_at IS NULL`
- Query parameters: `$param_name`

### RETURN and Modifiers
- Property projection: `RETURN a.name AS full_name, b.id AS target_id`
- Star projection: `RETURN *`
- Modifiers: `DISTINCT`, `ORDER BY <variable>.<prop> [ASC|DESC]`, `SKIP <n>`, `LIMIT <n>`
- Note on `ORDER BY`: Cypher expressions sort on binding variables (e.g. `ORDER BY a.name ASC`), not output projection aliases.

### Custom GestaltDB Procedures
- Path sampling procedure:
  ```cypher
  CALL pg.sample_typed_paths($seeds, $pattern) YIELD path RETURN path
  ```

---

## 3. Explicit Unsupported Cypher Features

Do **not** document or expect the following syntax to work:
- ❌ Mutating queries (`CREATE`, `MERGE`, `SET`, `DELETE`, `REMOVE`)
- ❌ Aggregation functions (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `COLLECT`)
- ❌ Grouping or pipelining (`WITH`, `GROUP BY`)
- ❌ Optional matches (`OPTIONAL MATCH`)
- ❌ Variable-length path expansion (`[:KNOWS*1..3]`)
- ❌ Multiple pattern parts inside a single `MATCH` (e.g. `MATCH (a)-[:T1]->(b), (c)-[:T2]->(d)`)
- ❌ Path binding variables (e.g. `p = (a)-[:T]->(b)`)

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

        # Query (Note: in chained MATCH queries, place all MATCH clauses before WHERE;
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
