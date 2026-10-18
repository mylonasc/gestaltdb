# Indexing For Library Users

GestaltDB automatically indexes node labels and relationship types. Property indexes are explicit.

## Rules

- Call `create_node_property_index` before `nodes_by_property` or property range lookups.
- Call `create_edge_property_index` before edge property lookups.
- Pass `None` for an unbounded range endpoint.
- Lookup order is not guaranteed; compare ID sets instead of ordered lists.
- If using deferred ingestion, call `rebuild_deferred_indexes()` before relying on property indexes unless you used `IndexMaintenanceMode.DEFER_REBUILD`.
- Property index creation also maintains label/property and type/property composite entries; these are not arbitrary multi-property composite indexes.
- Use `iter_node_ids_by_label_property_range` / `iter_edge_ids_by_type_property_range` when selecting cohort IDs without hydrating entities. ID iterators return bytes; decode before comparing to external strings. Counts iterate indexed IDs rather than promising O(1) lookup.
- Range bounds are inclusive by default; use `include_start=False` / `include_end=False` for exclusive bounds. Encoded secondary/range keys cannot exceed 511 bytes.
- Eligible Cypher exact/range predicates use configured candidate indexes; residual expressions are still checked, and not every expression is index-backed.
- Filtering seed IDs does not restrict subsequent hops. Use `build_sampler_snapshot(node_filter=..., edge_filter=...)` for a property-defined sampled graph, then reuse `SamplerEngine` batches. Build callbacks scan source records, not automatic index pushdown. See the `sampling` topic for policies.

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
            Node("n1", labels=["Item"], properties={"score": 0.4}),
            Node("n2", labels=["Item"], properties={"score": 0.9}),
        ])
        graph.put_edge(Edge("e1", "n1", "n2", properties={"type": "LINKS", "weight": 0.8}))
        graph.create_node_property_index("score")
        graph.create_edge_property_index("weight")
        assert {n.get_id for n in graph.nodes_by_label_property_range("Item", "score", 0.5, None)} == {"n2"}
        assert {e.get_id for e in graph.edges_by_type_property_range("LINKS", "weight", 0.7, None)} == {"e1"}
    finally:
        graph.close()
```
