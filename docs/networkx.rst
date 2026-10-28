NetworkX and GEXF
=================

Install the optional adapter and a backend, for example::

   pip install 'gestaltdb[networkx,lmdb]'

Import helpers from ``gestaltdb.integrations.networkx``. NetworkX and
``defusedxml`` are loaded lazily; importing GestaltDB or the integration module
does not require them. No live NetworkX backend or algorithm dispatch is
installed: algorithms operate on a materialized export of current records.

NetworkX algorithm round trip
-----------------------------

.. code-block:: python

   from tempfile import TemporaryDirectory
   import networkx as nx
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.integrations.networkx import from_networkx, to_networkx

   source = nx.MultiDiGraph(name="people")
   source.add_node("alice", category="Person", name="Alice")
   source.add_node("bob", category="Person", name="Bob")
   source.add_edge("alice", "bob", key="friend", id="e1", relation="KNOWS")

   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory + "/db", backend="lmdb", serializer="json")
       try:
           result = from_networkx(
               source, graph,
               node_labels_attribute="category", edge_type_attribute="relation",
           )
           exported = to_networkx(graph, graph_attributes=result.graph_attributes)
           assert nx.shortest_path(exported, "alice", "bob") == ["alice", "bob"]
           assert exported.edges["alice", "bob", "friend"]["type"] == "KNOWS"
       finally:
           graph.close()

Static GEXF loading
-------------------

``load_gexf`` accepts a local path (including ``.gz`` and ``.bz2``) or an already
decompressed binary stream. It never fetches URLs and never closes a supplied
stream. The caller owns the destination GraphDB handle.

.. code-block:: python

   from io import BytesIO
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.integrations.networkx import load_gexf

   data = b'''<gexf xmlns="http://www.gexf.net/1.2draft" version="1.2">
     <graph defaultedgetype="directed" mode="static">
       <nodes><node id="alice" label="Alice"/><node id="bob" label="Bob"/></nodes>
       <edges><edge id="e1" source="alice" target="bob" weight="2.5"/></edges>
     </graph>
   </gexf>'''
   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory + "/db", backend="lmdb", serializer="json")
       try:
           result = load_gexf(BytesIO(data), graph, label_property="name")
           assert result.imported_nodes == 2 and result.imported_edges == 1
           assert graph.get_node(b"alice").properties["name"] == "Alice"
       finally:
           graph.close()

Supported format matrix
-----------------------

=============================== ==============================================
Input                           Behavior
=============================== ==============================================
NetworkX DiGraph/MultiDiGraph    Directed import; isolated nodes and loops kept
NetworkX Graph/MultiGraph        Reject unless ``undirected="bidirectional"``
GEXF 1.1draft and 1.2draft       Static graphs using their standard namespaces
GEXF attributes                 Reader-supported scalar types; defaults applied
GEXF node visualization         Color, size, thickness, shape, position properties
GEXF graph metadata             Name, mode, defaults and root meta in result
GEXF dynamics/spells/slices      Rejected; no implicit temporal-history mapping
Mixed/mutual edge direction     Rejected
Hierarchy/hyperedges/extensions Rejected rather than silently ignored
GEXF edge visualization         Rejected (not preserved by the NetworkX reader)
GEXF writing                    Not provided
=============================== ==============================================

GEXF ``id`` remains node identity; ``label`` becomes a display property
(``label`` by default, configurable with ``label_property``), not a native
GestaltDB label. Edge IDs and weights remain properties as well as serving
their identity/weight roles. Relationship types are not inferred from edge
IDs, labels, or keys. Static attribute definitions with duplicate IDs/titles
or reserved reader fields fail clearly. Duplicate node IDs, edge IDs,
multiedge keys, undeclared endpoints, and unsupported XML fields also fail
before writes. Root ``meta`` is included under ``result.graph_attributes['gexf_meta']``.

Mapping policies
----------------

* **Node IDs:** Nonempty string keys are kept. Other finite scalar or tuple
  keys use tagged, SHA-256-derived IDs, distinguishing e.g. ``1`` and ``"1"``.
  ``node_id(key) -> str`` overrides ID generation and enables custom hashable
  objects; custom objects export as their mapped ID, with a result warning.
  Mapped IDs must be nonempty UTF-8 strings of at most 255 bytes. All collisions
  within the input or against the destination are errors. No update/merge mode
  is provided.
* **Edge IDs:** The ``id`` property (configurable via ``edge_id_attribute``)
  is preserved when present and must satisfy the same string ID contract.
  Otherwise IDs hash mapped endpoints and a tagged multigraph key. Generated
  undirected IDs use canonically ordered endpoints. Multigraph keys may be
  finite scalars or tuples; arbitrary key objects are not supported.
* **Undirected edges:** Explicit bidirectional expansion writes two directed
  edges, or one for a self-loop. Reverse edge IDs are deterministically derived.
  The export remains directed; undirected storage semantics are not implied.
* **Labels/types:** ``node_labels_attribute`` selects a string or list of
  nonempty strings. ``edge_type_attribute`` defaults to ``type`` and can select
  another nonempty string property. A conflicting existing ``type`` is an error.
  Missing attributes mean no additional native labels/relationship type.
  Original source properties are retained.
* **Attributes:** Import accepts string-keyed dictionaries, lists, ``None``,
  booleans, integers, strings and finite floats. Tuples as property values,
  sets, arbitrary objects, non-string dictionary keys and nonfinite floats
  require explicit ``attribute_transform(kind, attrs) -> dict`` conversion.
  ``kind`` is ``node``, ``edge`` or ``graph``. Transforms receive copies and
  produce a warning; source graphs are never mutated. The destination
  serializer is exercised during preflight, including its numeric limits.
* **Reserved metadata:** ``__gestaltdb_networkx__`` is a reserved property and
  NetworkX attribute namespace. Stored metadata contains tagged original keys;
  exported node metadata contains ``id``/``labels``, and edge metadata contains
  ``id``. This allows reimport of adapter exports with stable IDs and native
  labels. Do not use this namespace for application attributes. Malformed or
  conflicting metadata is rejected, not overwritten. Simple source edges have
  no multigraph key; their exported key is their stored edge ID.
* **Graph attributes:** Returned in ``ImportResult.graph_attributes``, not
  persisted as hidden nodes or database metadata. Pass them explicitly as
  ``to_networkx(..., graph_attributes=result.graph_attributes)`` to restore them.

Export and selections
---------------------

``to_networkx(graph)`` creates a ``MultiDiGraph`` including isolated nodes.
``multigraph=False`` creates a ``DiGraph`` only if no parallel edges would be
lost; reduction policies are deliberately not implicit. Duplicate original keys
or dangling endpoints are errors. Exported property dictionaries are copies.

``node_ids=[...]`` selects an induced subgraph using stored GestaltDB IDs.
``edge_ids=[...]`` selects edges and their endpoints. With both, all selected
edge endpoints must be selected nodes. Missing explicitly selected IDs fail.
Only current mutable graph records are exported: not claims, rules, temporal
versions or sampler history. Exports do not automatically pin a read snapshot;
quiesce writers when a consistent materialized view is needed.

Import results, indexes and failures
------------------------------------

``ImportResult`` reports source/imported counts, node-ID mappings,
``edge_ids[(source, target, key)]`` tuples of written edge IDs (simple source
graphs use key ``None``), graph attributes, warnings, and direction conversion.

Imports prepare all records and check mappings, existing IDs, attributes,
serializer compatibility and encoded index-key limits before writing. Native
label, relationship-type, adjacency and configured property indexes are
maintained through ``graph.nodes.put_many``/``graph.edges.put_many``; no deferred rebuild is
needed. Configure explicit property indexes before import when required.

Writes use ``batch_size=1000`` by default. Each batch inherits the backend's
normal write/transaction guarantees. **The import is not one atomic transaction**
on any backend; completed batches survive later failures. ``ImportWriteError``
provides ``result`` (completed-batch counts), ``phase``, ``batch_start`` and the
underlying exception as its cause. The failing batch may also have partial
writes on nontransactional storage. Inspect the destination before retrying.
Quiesce other writers during imports: preflight is not a concurrent uniqueness
constraint. The adapter never closes or replaces the caller's database.

Memory and XML security
-----------------------

NetworkX loading and export fully materialize graphs. Import also prepares
entity records in memory. Batching destination writes is **not** streaming or
out-of-core loading.

GEXF is parsed first by ``defusedxml`` with DTDs, entities and external
references forbidden; only the validated, sanitized tree reaches NetworkX.
``max_bytes`` defaults to 64 MiB and limits decompressed XML, including compressed
paths. Caller-owned streams must implement normal bounded ``read(size)``
semantics. XML/tree/attribute expansion and graph objects still consume more
memory than the input; there is no CPU or total-memory quota. Use appropriately
small limits and process isolation for adversarial inputs.

API reference
-------------

.. automodule:: gestaltdb.integrations.networkx
   :members:
