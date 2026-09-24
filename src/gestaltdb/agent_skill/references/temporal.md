# Temporal History And Migration For Library Users

Read this topic for immutable graph history, bitemporal reads, legacy
`TimeIndexedEdge` migration, and recovery decisions.

## Rules

- Temporal node/edge versions are separate from mutable current-state records.
- Valid intervals are half-open `[start, end)`; system horizons are inclusive visible commit prefixes.
- Use `GraphDB.read_view` to pin graph, claim, Cypher, and temporal snapshot work to one horizon.
- Deferred temporal writes make indexed reads fail closed until `rebuild_temporal_indexes()` or `rebuild_deferred_indexes()` completes.
- `TimeIndexedEdge` is deprecated legacy current-state data. Back up the complete database and stop writers before migration.
- Run `migrate_time_indexed_edges(delete_legacy=False)`, validate representative as-of reads, then rerun with `delete_legacy=True`.
- Migration retries skip exact prior results. Corrupt keys/payloads and changed post-migration input fail closed.
- Open a database with the serializer that wrote it. Temporal graph payloads support Pickle, JSON, MessagePack, and Protobuf when their dependencies and property types are compatible.
- Format-v1 snapshots still load without temporal/integrity guarantees. Rebuild from source for authenticated format v2; there is no in-place snapshot upgrade.
- Rebuild derived indexes after an interrupted deferred write. Restore canonical-history corruption from a complete backup.
- Temporal history has no pruning or TTL API. Deleting mutable records or snapshots does not remove history.

## Example

```python
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import GraphDB, TimeIndexedEdge
from gestaltdb.kvstores import LevelDBStore
from gestaltdb.serializers import PickleSerializer

with TemporaryDirectory() as tmpdir:
    graph = GraphDB(LevelDBStore(path=f"{tmpdir}/graph"), PickleSerializer())
    try:
        legacy = TimeIndexedEdge(
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            edge_id="e1", source="alice", target="acme",
            properties={"type": "WORKS_FOR"},
        )
        graph.put_edge(legacy, update_adjacency=False)
        migrated = graph.migrate_time_indexed_edges(delete_legacy=False)
        assert len(migrated) == 1
        assert graph.get_edge_as_of("e1", valid_time="2024-06-01T00:00:00Z")
        graph.migrate_time_indexed_edges(delete_legacy=True)
    finally:
        graph.close()
```
