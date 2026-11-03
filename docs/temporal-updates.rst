Append and revise historical records
=====================================

Start with :doc:`temporal-introduction` for the two time dimensions. Historical
records are append-only: a :term:`correction` supplies another account and a
:term:`retraction` withdraws applicability over an interval. Neither operation
physically edits or deletes an older payload. This page uses the same Alice
department example to show interval-local effects.

Correct an interval, then retract part of it
---------------------------------------------

The latest eligible assertion/correction supplies the payload; an eligible
winning retraction produces ``None``. Ordering is by :term:`commit` and in-commit
:term:`ordinal` (record position),
not by the latest valid-start date. The smaller retracted interval does not
withdraw Alice's department for the whole year.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node
   from gestaltdb.temporal import TemporalInterval

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           first = graph.temporal.put_node_version(
               Node("alice", properties={"department": "Research"}),
               valid=TemporalInterval.parse("2024-01-01T00:00:00Z", "2024-12-31T00:00:00Z"),
           )
           corrected = graph.temporal.correct_node_version(
               Node("alice", properties={"department": "Safety"}),
               supersedes_version_id=first.version_id,
               valid=TemporalInterval.parse("2024-03-01T00:00:00Z", "2024-06-01T00:00:00Z"),
           )
           retracted = graph.temporal.retract_node_version(
               "alice", supersedes_version_id=corrected.version_id,
               valid=TemporalInterval.parse("2024-04-01T00:00:00Z", "2024-05-01T00:00:00Z"),
               reason="source withdrew this part of its account",
           )
           instants = ["2024-01-15", "2024-03-15", "2024-04-15", "2024-05-15", "2024-06-01"]
           selected = [graph.temporal.get_node_as_of("alice", valid_time=day + "T00:00:00Z")
                       for day in instants]
           assert [None if row is None else row.node.properties["department"]
                   for row in selected] == ["Research", "Safety", None, "Safety", "Research"]
           older = graph.temporal.get_node_as_of(
               "alice", valid_time="2024-04-15T00:00:00Z", through_commit=corrected.commit_id,
           )
           assert older.version_id == corrected.version_id
           assert graph.temporal.get_node_version(retracted.version_id) is not None
           assert len(list(graph.temporal.iter_node_versions("alice"))) == 3
       finally:
           graph.close()

Omitting ``valid`` from a correction inherits the superseded interval. Supply
explicit intervals when only part of an account should change. Retractions require
asserted temporal history of the same entity kind and logical ID (or an earlier
assertion in the same batch). A mutable current record alone is insufficient;
unknown identities fail before commit allocation. Known identities may be
retracted repeatedly or over disjoint intervals.

Publish a logical batch
------------------------

Use ``graph.temporal.commit_versions`` with ``NodeVersionWrite`` and
``EdgeVersionWrite`` descriptors to publish related versions under one commit ID.
Eligible versions within that commit use their ordinal for deterministic order.
This does not update mutable current nodes/edges.

LMDB and transactional PyRex publish through a backend transaction. LevelDB and
plain PyRex publish validated records with a visibility marker written last;
incomplete publications remain invisible. This shared logical commit contract is
not universal backend rollback support. See :ref:`temporal-batch-reference` and
:doc:`storage-backends` for the exact boundaries.

Keep derived indexes fresh
----------------------------

Ordinary temporal writes maintain indexes. Bulk workflows can defer them, but
indexed as-of reads fail closed while the temporal family is stale. This separate
standalone example rebuilds before reading; it does not silently repair during a
query or disable freshness checks.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node
   from gestaltdb.ingestion import IndexMaintenanceMode

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           version = graph.temporal.put_node_version(
               Node("alice", properties={"department": "Research"}), valid=(0, None),
               index_mode=IndexMaintenanceMode.DEFER,
           )
           assert "temporal" in graph.stale_indexes()
           graph.temporal.rebuild_temporal_indexes()
           assert "temporal" not in graph.stale_indexes()
           assert graph.temporal.get_node_as_of("alice", valid_time=0).version_id == version.version_id
       finally:
           graph.close()

Integers in the second example are UTC microseconds, not days or seconds.
``IndexMaintenanceMode.DEFER_REBUILD`` rebuilds before a high-level write returns;
``graph.rebuild_deferred_indexes()`` can rebuild all stale families. A rebuild
repairs derived indexes from valid canonical history, not corrupted history.

Commit IDs are never reused, so gaps alone are not proof of missing visible data.
For an interrupted write, use :ref:`temporal-recovery-operations` rather than
deleting markers, sidecars or records manually. Visible history has no pruning
or TTL API; retention and complete backups are operational requirements.
