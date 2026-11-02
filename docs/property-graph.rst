The Property Graph
==================

Use the current property graph for mutable application data: entities, their
attributes, and directed relationships. Use :doc:`temporal-versioning` when you
need immutable historical assertions instead. Ordinary current-graph writes do
not automatically record temporal versions.

Nodes, edges, and identity
--------------------------

A node has a stable ID, zero or more native labels, and a property dictionary.
An edge has its own stable ID, a source, a target, and properties. Store its
relationship type in ``properties["type"]``. Labels such as ``Drug`` describe
nodes; types such as ``binds`` describe edges. Neither requires an ontology.

Model the topology as a directed multigraph :math:`G=(V,E,s,t)`, where
:math:`s,t:E\to V` give each edge's source and target. Distinct edges may have
the same endpoints. Edge identity is therefore not just the pair :math:`(u,v)`.
Properties and labels attach to the identified entities; they are not implicit
extra nodes or relationships.

Create, replace, and delete
---------------------------

**Standalone runnable example.** Install ``gestaltdb[lmdb]``. This uses built-in
JSON, disposable storage and a context manager to close the handle.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import Edge, GraphDB, Node

   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           graph.put_nodes([
               Node("drug-1", labels=["Drug"], properties={"name": "Aspirin"}),
               Node("protein-1", labels=["Protein"], properties={"name": "PTGS1"}),
           ])
           graph.put_edge(Edge(
               "binding-1", "drug-1", "protein-1",
               properties={"type": "binds", "score": 0.9},
           ))
           assert graph.get_node("drug-1").get_id == "drug-1"
           assert graph.get_edge("binding-1").properties["score"] == 0.9
           assert graph.neighbors_by_edge_type("drug-1", "binds") == [b"protein-1"]

           # Same ID: replace the current entity, not append a history version.
           graph.put_node(Node(
               "drug-1", labels=["Drug"],
               properties={"name": "Aspirin", "approved": True},
           ))
           assert graph.get_node("drug-1").properties["approved"] is True

           # Delete the relationship before its endpoint in this workflow.
           graph.delete_edge("binding-1")
           graph.delete_node("protein-1")
           assert graph.get_edge("binding-1") is None
           assert graph.get_node("protein-1") is None

``put_node`` and ``put_edge`` write complete entities. Include the labels and
properties you want retained when replacing them. Do not interpret replacement
as an implicit property merge or a temporal correction. Use
:doc:`cypher` for ``SET``/``REMOVE`` and explicit graph-mutation patterns.

For incremental application code, start with ``get_node`` / ``get_edge``,
``put_node`` / ``put_edge``, ``put_nodes`` / ``put_edges_bulk`` and explicit
deletion. For table-shaped input, follow the ingestion workflows in
:doc:`api-workflows`. Batch APIs do not by themselves promise one transaction
across arbitrary operations; see :doc:`storage-backends`.

IDs and returned objects
-------------------------

- ID-taking graph reads accept ``str`` or ``bytes``. The examples use strings for
  application identity; key iterators and traversal neighbors return bytes.
  Decode keys with ``graph.key_to_string(key)`` when comparing them with strings.
- The entity ID is the ``get_id`` **property**, not ``node.id`` or a callable
  ``get_id()``. Cypher's ``n.id`` is query syntax, not the Python attribute API.
- Read node attributes through ``node.properties`` and labels through
  ``node.labels``; read edge attributes through ``edge.properties``.
- Fetching or modifying a Python object is not a write-through operation. Persist
  intended changes with the write APIs. Serializer choice determines which
  property values round-trip (:doc:`serializers`).
- Do not depend on iteration order unless the particular API documents it.

Choose an access path
----------------------

.. list-table:: Current-graph reads and writes
   :header-rows: 1

   * - Goal
     - Interface
     - Important boundary
   * - Fetch a known entity
     - ``get_node`` / ``get_edge``
     - No property index is needed for an ID lookup.
   * - Find a label, type or property cohort
     - ``nodes_by_*`` / ``edges_by_*`` and ID/count helpers
     - Labels/types are indexed automatically; property indexes are explicit
       (:doc:`property-filtering`).
   * - Traverse a known relationship type
     - ``neighbors_by_edge_type`` / ``iter_typed_adjacency``
     - Direction is explicit; traversal neighbors are byte IDs.
   * - Compose patterns, aggregates or mutations
     - ``graph.query(...)``
     - Follow the supported Cypher subset and atomicity scope (:doc:`cypher`).
   * - Repeatedly draw training neighborhoods
     - ``SamplerSnapshot`` / ``SamplerEngine``
     - A built artifact is immutable; it does not track later graph writes
       (:doc:`typed-sampling`).

Narrow services and read authority
----------------------------------

``GraphDB`` is the convenient integration handle. Applications can pass narrower
services instead: ``graph.nodes`` and ``graph.edges`` own current CRUD;
``graph.indexes`` owns index operations; ``graph.lookup`` composes decoded
lookups; and ``graph.traversal`` owns live typed traversal/sampling.

A repository's ``reader`` narrows write authority. It does not automatically pin
the underlying data. For consistent multi-step reads, use
``graph.current_read_view()`` on a supported backend; its ``nodes``, ``edges``,
``indexes``, ``lookup`` and ``traversal`` are snapshot-bound readers.
See :doc:`storage-backends` for a complete pinned-read example and backend matrix.

These services borrow the owning store's lifetime. Do not retain a reader/view
after its owning scope closes. Use handle-bound services when sharing a handle's
caches; independently constructed sessions do not synchronize their caches for you.
The detailed decomposition is in :doc:`architecture`.

Next steps
-----------

- :doc:`storage-backends`: manifests, backend/codec selection, transactions,
  snapshots and close/reopen lifecycle.
- :doc:`property-filtering`: explicit indexes and selected cohorts.
- :doc:`cypher`: supported read/write patterns, constraints and procedures.
- :doc:`temporal-versioning`: immutable history, not ordinary entity replacement.
- :doc:`operations`: compatibility, recovery and legacy migration.
