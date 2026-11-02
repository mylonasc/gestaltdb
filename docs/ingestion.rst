Load Arrow and Polars Data
==========================

Use columnar ingestion for table-shaped graph input. Use object writes for simple
incremental changes (:doc:`property-graph`). Choose the backend and serializer
independently (:doc:`storage-backends`); the examples use LMDB/JSON for a portable
demonstration, not a claim of native-writer speed.

Choose payload and index policies
----------------------------------

.. list-table:: Two independent ingestion choices
   :header-rows: 1
   :widths: 30 70

   * - Policy
     - Meaning
   * - ``ColumnarIngestionMode.ENTITY_COLUMNS`` (high-level default)
     - Build payloads from IDs, labels, endpoints, types and property columns.
       JSON-compatible structured input can use eligible optimized conversion.
   * - ``ColumnarIngestionMode.SERIALIZED_PAYLOADS``
     - Supply node/edge bytes encoded with the graph's serializer. Column IDs,
       endpoints and types must agree with the payloads; arbitrary JSON blobs
       are not necessarily serialized graph entities.
   * - ``IndexMaintenanceMode.MAINTAIN``
     - Maintain configured secondary indexes during loading; ready for indexed reads.
   * - ``IndexMaintenanceMode.DEFER``
     - Write canonical records and typed adjacency, mark secondary indexes stale;
       call ``rebuild_deferred_indexes()`` before index-backed reads.
   * - ``IndexMaintenanceMode.DEFER_REBUILD`` (high-level default)
     - Defer during loading, then rebuild before the high-level call returns.
       This moves work; it does not guarantee lower total time.

Property indexes are explicit. Create them before loading or build them afterward;
labels/types are automatic. All examples below choose the policy explicitly so
their read-after-load behavior is clear.

Arrow entity columns
---------------------

**Standalone runnable example.** Install ``gestaltdb[lmdb,arrow]``. Node columns
have two rows and edge columns one row; lengths must agree **within** each entity
family, not across node and edge families.

.. code-block:: python

   from tempfile import TemporaryDirectory
   import pyarrow as pa
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.ingestion import ColumnarIngestionMode, IndexMaintenanceMode

   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           graph.create_node_property_index("name")
           graph.create_edge_property_index("score")
           loaded = graph.ingest_arrow(
               pa.array(["drug-1", "protein-1"]), pa.array(["binding-1"]),
               pa.array(["drug-1"]), pa.array(["protein-1"]), pa.array(["binds"]),
               labels=pa.array([["Drug"], ["Protein"]]),
               node_properties={"name": pa.array(["Aspirin", "PTGS1"])},
               edge_properties={"score": pa.array([0.9])},
               ingestion_mode=ColumnarIngestionMode.ENTITY_COLUMNS,
               index_mode=IndexMaintenanceMode.DEFER_REBUILD,
           )
           assert loaded["nodes"] == 2 and loaded["edges"] == 1
           assert [n.get_id for n in graph.nodes_by_property("name", "Aspirin")] == ["drug-1"]
           assert [e.get_id for e in graph.edges_by_type_property_range("binds", "score", 0.8, None)] == ["binding-1"]
           assert graph.neighbors_by_edge_type("drug-1", "binds") == [b"protein-1"]

Polars entity columns with an explicit rebuild
-----------------------------------------------

**Standalone runnable example.** Install ``gestaltdb[lmdb,polars]``. Select
property columns explicitly; do not assume every input column becomes a property.
This example deliberately defers indexes and rebuilds before indexed lookups.

.. code-block:: python

   from tempfile import TemporaryDirectory
   import polars as pl
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.ingestion import ColumnarIngestionMode, IndexMaintenanceMode

   nodes = pl.DataFrame({
       "node_id": ["drug-1", "protein-1"],
       "labels": [["Drug"], ["Protein"]],
       "name": ["Aspirin", "PTGS1"],
   })
   edges = pl.DataFrame({
       "edge_id": ["binding-1"], "source": ["drug-1"], "target": ["protein-1"],
       "edge_type": ["binds"], "score": [0.9],
   })
   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           graph.create_node_property_index("name")
           graph.ingest_polars(
               nodes, edges, node_property_columns=["name"], edge_property_columns=["score"],
               ingestion_mode=ColumnarIngestionMode.ENTITY_COLUMNS,
               index_mode=IndexMaintenanceMode.DEFER,
           )
           # Known-ID reads use canonical records; secondary indexes are still stale.
           assert graph.get_node("drug-1").properties["name"] == "Aspirin"
           graph.rebuild_deferred_indexes()
           assert [n.get_id for n in graph.nodes_by_property("name", "PTGS1")] == ["protein-1"]

Pre-serialized payloads
-----------------------

**Standalone runnable example.** Install ``gestaltdb[lmdb,arrow]``. Build bytes
with the graph's own entity serializers so IDs, labels and relationship types are
represented correctly. Pre-serialization is useful when an upstream producer
already owns compatible payload construction, not as a reason to double-encode it.

.. code-block:: python

   from tempfile import TemporaryDirectory
   import pyarrow as pa
   from gestaltdb.graphdb import Edge, GraphDB, Node
   from gestaltdb.ingestion import ColumnarIngestionMode, IndexMaintenanceMode

   with TemporaryDirectory() as root:
       with GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json") as graph:
           nodes = [Node("drug-1", labels=["Drug"]), Node("protein-1", labels=["Protein"])]
           edge = Edge("binding-1", "drug-1", "protein-1", properties={"type": "binds"})
           graph.ingest_arrow(
               pa.array([n.get_id for n in nodes]), pa.array([edge.get_id]),
               pa.array([edge.source]), pa.array([edge.target]), pa.array(["binds"]),
               node_values=pa.array([graph.serialize_node_value(n) for n in nodes]),
               edge_values=pa.array([graph.serialize_edge_value(edge)]),
               ingestion_mode=ColumnarIngestionMode.SERIALIZED_PAYLOADS,
               index_mode=IndexMaintenanceMode.MAINTAIN,
           )
           assert [n.get_id for n in graph.nodes_by_label("Drug")] == ["drug-1"]
           assert graph.get_edge("binding-1").properties["type"] == "binds"

Boundaries and lower-level interfaces
--------------------------------------

- Columnar edge ingestion is **append-only**. Use object/Cypher writes for
  relationship replacement. Typed adjacency is maintained, while the legacy
  adjacency blobs are intentionally not populated by this ingestion path.
- ``chunk_size`` controls write chunks, not an end-to-end process-memory cap.
  Conversion, index rebuilding and backend buffers have separate costs.
- ``native=True`` requests available fast paths; it does not guarantee every
  backend/codec/input uses a native writer. Plain PyRex can use native columnar
  writes; transactional PyRex currently has a different write path.
- Do not infer portable cross-call or cross-family atomicity from the word
  “bulk”. Graph-level wrappers use available transactions; use an explicit
  supported transaction for a larger application scope (:doc:`storage-backends`).
- Format services are available as ``graph.ingest.arrow`` / ``graph.ingest.polars``.
  Their ``nodes``/``edges`` methods consume payloads; ``node_entities``/
  ``edge_entities`` consume structured columns. Low-level maintain/defer policy
  is distinct from the high-level graph-wide defer/rebuild coordinator.
- This loads current records, not temporal versions or epistemic claims. See
  :doc:`temporal-versioning` for immutable history writes.

Continue with :doc:`data-access` for indexed lookup choices, :doc:`cypher-tutorial`
for result composition, or :doc:`property-filtering` for a filtered sampler build.
