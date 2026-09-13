# Cypher For Library Users

Use `graph.query(cypher, parameters=None)` for read-only Cypher. It returns `QueryResult(columns, records)` and iteration yields record dictionaries.

## Supported

- Node labels and inline properties: `MATCH (p:Person {name: $name})`.
- Typed traversal: `MATCH (a)-[:KNOWS]->(b)`.
- `WHERE` comparisons, `AND`, `IN`, `IS NULL`, `IS NOT NULL`.
- `RETURN`, aliases, `RETURN *`, `DISTINCT`, `ORDER BY`, `SKIP`, `LIMIT`.
- Chained `MATCH` clauses.

## Unsupported

Do not use `CREATE`, `MERGE`, `SET`, `DELETE`, aggregation such as `COUNT`, `WITH`, `OPTIONAL MATCH`, variable-length paths, path binding, or multiple pattern parts in one `MATCH`.

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
