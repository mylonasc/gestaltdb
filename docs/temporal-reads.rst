Read one historical account consistently
=========================================

As-of reads answer one question at a valid instant and an optional recorded
horizon. An authenticated ``GraphReadView`` shares a fixed commit prefix across
several graph/claim reads and snapshot builds. It does not pin mutable current
records, and it is not a truth certificate. Read :doc:`temporal-introduction`
before using the source and horizon contracts below.

Choose a historical read
-------------------------

.. list-table:: Point, history and window operations
   :header-rows: 1

   * - Need
     - Operation
   * - Select one logical entity at a point
     - ``get_node_as_of`` / ``get_edge_as_of``
   * - Traverse winning historical relationships
     - ``iter_edges_as_of`` with source/target/type and valid instant
   * - Inspect append order or an exact version
     - ``iter_node_versions`` / ``iter_edge_versions`` / ``get_*_version``
   * - Reuse one authenticated horizon
     - ``graph.temporal.open_read_view(...)``
   * - Sample a finite valid-time window
     - ``TemporalContext.during`` on a temporal snapshot (:doc:`sampling-temporal`)
   * - Compute all conclusion intervals over a range
     - Explicit interval reasoning (:doc:`reasoning-temporal`), not an as-of lookup

The as-of entity/traversal APIs select **points**, not every instant in a window.
Enumerating versions is an audit of recorded history, not an already-resolved
validity union. Choose the window/range API matching the task rather than passing
a window to a point selector.

Pin a view while a newer account is recorded
---------------------------------------------

This standalone example first records Alice working for Acme, opens a view, then
corrects the relationship to Beta. The old view still reads Acme; a fresh read
sees Beta. Qualified Cypher uses the same explicit valid/system timestamps but
opens its own historical query view; simply having a Python view open does not
change unrelated ``graph.query`` calls.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           first = graph.temporal.commit_versions([
               NodeVersionWrite.assertion(Node("alice", labels=["Person"]), (0, None)),
               NodeVersionWrite.assertion(Node("acme", properties={"name": "Acme"}), (0, None)),
               NodeVersionWrite.assertion(Node("beta", properties={"name": "Beta"}), (0, None)),
               EdgeVersionWrite.assertion(
                   Edge("job", "alice", "acme", properties={"type": "WORKS_FOR"}), (0, None),
               ),
           ])
           with graph.temporal.open_read_view(
               graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=5,
           ) as view:
               graph.temporal.correct_edge_version(
                   Edge("job", "alice", "beta", properties={"type": "WORKS_FOR"}),
                   supersedes_version_id=first.versions[-1].version_id,
               )
               assert view.commit_horizon == first.commit_id
               assert [row.edge.target for row in view.iter_edges_as_of(source="alice")] == ["acme"]
               assert graph.temporal.get_edge_as_of("job", valid_time=5).edge.target == "beta"
               point = view.build_sampler_snapshot(f"{root}/point")
               assert point.metadata["source_provenance"]["commit_horizon"] == first.commit_id
               view.verify_source()

           historical = graph.query(
               "MATCH (p:Person)-[:WORKS_FOR]->(company) "
               "FOR VALID_TIME AS OF datetime($at) FOR SYSTEM_TIME AS OF datetime($known) "
               "RETURN company.name AS company",
               {"at": "1970-01-01T00:00:00.000005Z",
                "known": first.system_time.to_datetime().isoformat()},
           )
           assert historical.records == [{"company": "Acme"}]
       finally:
           graph.close()

The integer ``5`` and the ISO instant ending in ``.000005Z`` are the same UTC
microsecond. Cypher qualifiers must evaluate to temporal instants, not bare integer
values. Qualified historical queries are read-only and share their temporal
context across the supported query; see :doc:`cypher` for exact syntax and
query-wide restrictions. Unqualified queries still read the mutable graph.

What the view authenticates
----------------------------

The view captures a fixed contiguous prefix of fully visible commits and stops
before incomplete publication. Pass ``system_time=...`` for a recorded instant
or ``through_commit=...`` for an exact prefix; do not supply both. Calls through
the view use this horizon even when newer commits become visible.

``ReadViewProvenance`` binds database UUID, backend/serializer identity, visible
commit markers/digest and default valid instant. Snapshot source verification
checks that binding, not merely a matching database path. Close borrowed scopes
before closing the owning graph; do not use a view after its context exits.

``graph.temporal.history()`` is a narrower read-only capability borrowing store
lifetime. It does not independently pin or authenticate a source, initialize its
identity, repair indexes or grant write authority. A current read view instead
pins mutable records and has different backend-qualified provenance
(:doc:`storage-backends`). Temporal views are supported across the documented
backends, but this does not make current snapshots equally available on them.

Indexes and failures at the read boundary
-----------------------------------------

Temporal as-of reads require fresh temporal indexes. With deferred writes, rebuild
explicitly before opening/using an indexed workflow (:doc:`temporal-updates`).
Corrupt markers, descriptors or record envelopes fail closed rather than becoming
partial successful history. Exhaust historical iterators and discard partial
output on failure; stopping early is not successful authenticated completion.

Decoded-commit reuse does not remove live-store integrity checks or provide a
hard memory bound. Exact validation, cache and provenance contracts remain in
:ref:`temporal-read-view-reference`. For history-preserving sampling across a
window, use :doc:`sampling-temporal`; the point materialization above is not a
history-window artifact. Recovery and migration belong in :doc:`operations`.
