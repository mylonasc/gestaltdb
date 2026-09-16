# Cypher For Library Users

Use `graph.query(cypher, parameters=None)` for Cypher reads and writes. It returns `QueryResult(columns, records)` and iteration yields record dictionaries.

## Supported

- Node labels and multi-entry inline property maps: `MATCH (p:Person {name: $name, active: true})`.
- Typed and untyped fixed traversal: `MATCH (a)-[:KNOWS]->(b)` and `MATCH (a)-->(b)`.
- Anonymous pattern elements, endpoint labels/properties, unanchored multi-hop paths, and comma-separated pattern parts.
- `WHERE` arithmetic and comparisons, `NOT`/`AND`/`XOR`/`OR`, `IN`, null predicates, string predicates, and `=~`.
- Cypher three-valued null logic; use `IS NULL` rather than `= null`.
- Predicate contexts are strictly Boolean/null. `ORDER BY` is stable, with null last ascending and first descending.
- `RETURN`, aliases, `RETURN *`, general projection expressions, `DISTINCT`, alias-aware `ORDER BY`, and literal or parameterized `SKIP`/`LIMIT`.
- Chained `MATCH` clauses with clause-local `WHERE`.
- `WITH` with scope replacement: only projected variables and aliases survive, and `WITH` may carry its own `WHERE`, `DISTINCT`, `ORDER BY`, `SKIP`, and `LIMIT`.
- Core aggregates `count`, `collect`, `sum`, `avg`, `min`, and `max` in `WITH` and `RETURN`, including `count(*)` and aggregate `DISTINCT`. Non-aggregate projections are implicit grouping keys; `ORDER BY` in aggregate queries must reference projected outputs.
- `{id: $id}` on node patterns addresses the stable entity ID as a GestaltDB extension.
- `OPTIONAL MATCH`, `UNWIND`, `UNION [ALL]`, correlated `CALL { ... }`, relationship property maps, variable-length and shortest paths, path bindings, and label expressions.
- `CREATE`, `SET`, `REMOVE`, `DELETE`/`DETACH DELETE`, `MERGE`, and write-only `FOREACH` loops.
- Persisted single-property node `UNIQUE` and `IS NOT NULL` constraints through `CREATE CONSTRAINT`, `DROP CONSTRAINT`, and `SHOW CONSTRAINTS`; enforcement applies to Cypher writes.
- `SHOW INDEX`/`SHOW INDEXES` returns configured node and relationship property indexes as `entityType`/`properties` records.
- Registered procedures use generalized `CALL name(...) YIELD field [AS alias] RETURN alias`; only `pg.sample_typed_paths` currently executes and it accepts parameters.
- Configured exact/range node and typed relationship indexes are reused where predicates permit.

## Unsupported

Do not use pattern comprehensions, pattern arguments to `exists()`, GQL quantified path patterns, relationship or multi-property constraints, or procedures beyond the documented sampling call. GQL quantifier errors suggest the supported legacy `*min..max` form.

## Example

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_nodes([
            Node("alice", labels=["Person"], properties={"name": "Alice", "age": 34}),
            Node("bob", labels=["Person"], properties={"name": "Bob", "age": 36}),
            Node("carol", labels=["Person"], properties={"name": "Carol", "age": 29}),
        ])
        graph.put_edges_bulk([
            Edge("e1", "alice", "bob", properties={"type": "KNOWS", "since": 2020}),
            Edge("e2", "alice", "carol", properties={"type": "KNOWS", "since": 2022}),
        ])
        result = graph.query(
            'MATCH (a:Person {name: $name}) '
            'MATCH (a)-[:KNOWS]->(b) '
            'WHERE b.age >= $min_age '
            'RETURN b.name AS friend, b.age AS age '
            'ORDER BY b.age ASC '
            'LIMIT 1',
            parameters={"name": "Alice", "min_age": 30},
        )
        assert result.records == [{"friend": "Bob", "age": 36}]
    finally:
        graph.close()
```
