# Ingestion For Library Users

Use object writes for small incremental updates. Use Arrow or Polars ingestion for tabular bulk loads.

## Index Maintenance Modes

- `IndexMaintenanceMode.MAINTAIN`: update indexes during ingest.
- `IndexMaintenanceMode.DEFER`: write canonical records but leave secondary property indexes stale until `rebuild_deferred_indexes()`.
- `IndexMaintenanceMode.DEFER_REBUILD`: defer during ingest and rebuild before returning.

## Example

```python
from tempfile import TemporaryDirectory

import pyarrow as pa

from gestaltdb import IndexMaintenanceMode
from gestaltdb.graphdb import GraphDB

with TemporaryDirectory() as tmpdir:
    graph = GraphDB.create(f"{tmpdir}/graph", backend="leveldb", serializer="json")
    try:
        graph.create_node_property_index("name")
        graph.ingest_arrow(
            pa.array(["alice", "bob"]),
            pa.array(["e1"]),
            pa.array(["alice"]),
            pa.array(["bob"]),
            pa.array(["KNOWS"]),
            labels=pa.array([["Person"], ["Person"]]),
            node_properties={"name": pa.array(["Alice", "Bob"])},
            index_mode=IndexMaintenanceMode.DEFER_REBUILD,
        )
        assert [node.get_id for node in graph.nodes_by_property("name", "Alice")] == ["alice"]
    finally:
        graph.close()
```
