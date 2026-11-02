# Quickstart For Library Users

Read this when creating small scripts, notebooks, tests, or applications that use GestaltDB.

## Rules

- Import core classes from `gestaltdb.graphdb`.
- Use `GraphDB.create` for manifest-backed directories when possible.
- Use `TemporaryDirectory` in examples and tests.
- Always close graph handles in `finally` (`GraphDB` also supports `with GraphDB.create(...) as graph:`).
- Relationship types live in `Edge.properties["type"]`.
- ID-taking reads accept `str` or `bytes`; returned keys and traversal neighbor IDs are bytes, so decode with `graph.key_to_string(...)` before comparing with string IDs.
- Node identity is the `get_id` property (also `to_dict()["id"]`); there is no `node.id` attribute and `get_id` is not callable.
- Create property indexes before index-backed lookups; unindexed property lookups return `[]`/`0`. Use `MATCH (n) RETURN id(n), labels(n), properties(n)` for inspectable Cypher rows instead of bare `RETURN n`.

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
        ])
        graph.put_edge(Edge("alice-knows-bob", "alice", "bob", properties={"type": "KNOWS"}))
        assert graph.get_node(b"alice").properties["name"] == "Alice"
        assert graph.neighbors_by_edge_type("alice", "KNOWS", direction="out") == [b"bob"]
    finally:
        graph.close()
```
