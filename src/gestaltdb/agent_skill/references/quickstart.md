# Quickstart For Library Users

Read this when creating small scripts, notebooks, tests, or applications that use GestaltDB.

## Rules

- Import core classes from `gestaltdb.graphdb`.
- Use `GraphDB.create` for manifest-backed directories when possible.
- Use `TemporaryDirectory` in examples and tests.
- Always close graph handles in `finally`.
- Relationship types live in `Edge.properties["type"]`.

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
