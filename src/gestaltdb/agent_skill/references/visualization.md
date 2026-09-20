# Visualization For Library Users

GestaltDB ships offline interactive visualization with the core library: the
D3.js + React front end is prebuilt and packaged, so saving `.html`
artifacts or rendering in Jupyter needs no JavaScript toolchain, no
network, and no new runtime dependency.

Entry points (import from `gestaltdb.viz.api`, not the package root):

- `visualize_nodes_edges(nodes, edges)` for explicit collections.
- `visualize_query(graph, cypher, parameters=None)` for Cypher matches
  (matched entities are highlighted in the canvas).
- `visualize_sample(graph, seeds, pattern)` for typed sampling around seeds
  (the large-graph path).
- `visualize_sampler_batch(batch, snapshot)` for ML batches mapped back to
  external IDs via `node_ids_global`.
- `graph.visualize(cypher=...)` or `graph.visualize(seeds=..., pattern=...)`
  as a `GraphDB` method.
- `VizOptions` for caps (defaults 2000 nodes / 5000 edges), absolute
  ceilings (default twice the caps), iframe height, light/dark theme,
  force-layout charge/link distance, title, and label/property visibility.

Every builder returns a `VizFigure` with `save(path)`, `as_html()`, and
Jupyter `_repr_html_()`. Caps truncate deterministically with a
`TruncationWarning` and an on-canvas banner; payloads beyond the absolute
ceiling raise `VizCapExceededError` naming the sampling alternative.

## Example

```python
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer
from gestaltdb.viz.api import VizOptions, visualize_query

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        graph.put_node(Node("alice", labels=["Person"], properties={"name": "Alice"}))
        graph.put_node(Node("bob", labels=["Person"], properties={"name": "Bob"}))
        graph.put_edge(Edge("e1", "alice", "bob", properties={"type": "KNOWS"}))

        figure = visualize_query(
            graph,
            "MATCH (a:Person)-[r:KNOWS]->(b) RETURN a, r, b",
            options=VizOptions(title="Knows graph"),
        )
        figure.save(f"{tmpdir}/knows.html")
        assert figure.viz.node_ids == ("alice", "bob")
    finally:
        graph.close()
```
