Choose How to Load and Query Data
=================================

Use this path after :doc:`quickstart` to choose between object writes, columnar
loading, indexed cohorts and Cypher. All operate on the current property graph;
they do not automatically append immutable temporal history or execute ontology
rules. The existing :doc:`architecture` diagram shows these interfaces and their
explicit handoffs; a loading/query tutorial does not need a second component map.

Choose an interface
--------------------

.. list-table:: Match the interface to the task
   :header-rows: 1
   :widths: 28 35 37

   * - Task
     - Start with
     - Check first
   * - Incrementally create or replace a few entities
     - ``put_node``, ``put_edge``, ``put_nodes``, ``put_edges_bulk``
     - Entity IDs, complete replacement payloads, and required transaction scope
       (:doc:`property-graph`, :doc:`storage-backends`).
   * - Load a table of entities/relationships
     - ``ingest_arrow`` or ``ingest_polars``
     - Structured versus serialized input, explicit property columns, append-only
       edges and index policy (:doc:`ingestion`).
   * - Fetch known IDs
     - ``get_node`` / ``get_edge``
     - No property index is required for an ID lookup.
   * - Select an exact/range cohort
     - ``nodes_by_*`` / ``edges_by_*`` or corresponding ID/count helpers
     - Property indexes must exist and be fresh (:doc:`property-filtering`).
   * - Match patterns, join results, aggregate or mutate
     - ``query(cypher, parameters=...)``
     - Supported clauses, parameter names and atomicity boundaries
       (:doc:`cypher-tutorial`, :doc:`cypher`).

An index is not required for every Cypher predicate: eligible indexes select
candidates, and remaining predicates are evaluated against them. A successful
query does not prove index use. Conversely, direct property-index helpers are not
a replacement for an unindexed scan; create the index before relying on them.

An indexed cohort, with expected IDs
------------------------------------

**Standalone runnable example.** Requires ``gestaltdb[lmdb]``. Property-index
creation builds entries for existing records and maintains subsequent writes.
Labels and relationship types are indexed automatically.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB, Node

   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           graph.put_nodes([
               Node("p1", labels=["Protein"], properties={"score": 0.9}),
               Node("p2", labels=["Protein"], properties={"score": 0.4}),
               Node("d1", labels=["Drug"], properties={"score": 0.95}),
           ])
           graph.create_node_property_index("score")
           cohort = graph.nodes_by_label_property_range("Protein", "score", 0.8, None)
           assert {node.get_id for node in cohort} == {"p1"}
           ids = list(graph.iter_node_ids_by_label_property_range("Protein", "score", 0.8, None))
           assert ids == [b"p1"]

The label/property helper selects a **Protein** cohort, not every high-scoring
entity. These are label-plus-one-property indexes, not arbitrary multi-property
composite indexes. Ranges are inclusive by default; use ``include_end=False``
for an exclusive upper bound and ``None`` for an unbounded endpoint. Keys and
ID iterators return bytes, while decoded entities expose string ``get_id`` values.
See :doc:`property-filtering` for the full exact/range/ID/count map and the portable
511-byte encoded-index-key limit.

Freshness is part of the workflow
----------------------------------

Object writes maintain configured current indexes. Columnar loading chooses an
index policy: maintain during writes, defer and explicitly rebuild, or defer and
rebuild before returning. A stale-family error is a signal to complete the
documented rebuild, not permission to ignore freshness or read raw index keys.
The :doc:`ingestion` examples show the policy explicitly.

For a multi-step current-graph selection that must observe one state, use a
supported current read view (:doc:`storage-backends`). A read-only repository
alone is not a pinned snapshot. Temporal-history indexes and sampler arrays have
separate freshness/identity rules.

From cohort selection to sampling
----------------------------------

An indexed cohort chooses starting entities. It does **not** restrict the graph
visited at later hops. If every hop must stay within a property-defined graph,
build a snapshot with ``node_filter`` / ``edge_filter``. The end-to-end workflow
is in :doc:`property-filtering`; draw policies and ID conversions are in
:doc:`typed-sampling`.

Next steps
-----------

- :doc:`ingestion`: runnable Arrow, Polars and pre-serialized input workflows.
- :doc:`cypher-tutorial`: pattern reads, aggregation and writes with expected rows.
- :doc:`cypher`: detailed syntax, temporal qualifiers, constraints and procedures.
- :doc:`performance`: measure writes, index rebuilding and reads separately.
