Temporal Version History
========================

GestaltDB can append immutable node and edge versions alongside its existing
mutable graph. Temporal versions use two distinct time dimensions:

* **valid time** is the half-open interval ``[start, end)`` in which a fact is
  true in the modeled domain;
* **system time** is the monotonically increasing UTC instant at which GestaltDB
  committed that version.

The explicit version APIs do not update records returned by ``get_node`` or
``get_edge`` and do not alter Cypher or sampling behavior. Indexed as-of graph
resolution and traversal are planned separately.

Assertions, Corrections, and Retractions
----------------------------------------

.. code-block:: python

   from gestaltdb.graphdb import Edge, Node
   from gestaltdb.temporal import TemporalInterval

   alice = graph.put_node_version(
       Node(node_id="alice", labels=["Person"], properties={"name": "Alice"}),
       valid=TemporalInterval.parse("2020-01-01T00:00:00Z"),
       metadata={"source": "people-import"},
   )

   corrected = graph.correct_node_version(
       Node(node_id="alice", labels=["Person"], properties={"name": "Alicia"}),
       supersedes_version_id=alice.version_id,
   )

   graph.retract_node_version(
       "alice",
       valid_from="2030-01-01T00:00:00Z",
       supersedes_version_id=corrected.version_id,
       reason="source withdrew the assertion",
   )

Corrections and retractions append records. They never modify or physically
delete the superseded payload. Omitting ``valid`` from a correction inherits the
superseded version's interval.

Atomic Logical Batches
----------------------

Use write descriptors from :mod:`gestaltdb.versioning` to publish several
versions under one commit ID:

.. code-block:: python

   from gestaltdb.versioning import EdgeVersionWrite, NodeVersionWrite

   commit = graph.commit_versions([
       NodeVersionWrite.assertion(Node(node_id="acme"), (0, None)),
       EdgeVersionWrite.assertion(
           Edge(
               edge_id="employment-1",
               source="alice",
               target="acme",
               properties={"type": "WORKS_FOR"},
           ),
           (0, None),
       ),
   ], metadata={"source": "hr-import"})

Transactional LMDB and transactional PyRex publish the complete batch in one
backend transaction. LevelDB and default PyRex reserve a commit ID, write the
descriptor and records, and publish a visibility marker last. Records without a
valid marker remain invisible. Non-transactional temporal writes currently
require one externally serialized writer handle. Commit IDs are strictly
increasing for visible commits; an ID provisionally returned inside a rolled
back outer transaction is not durable and may be reused.

Inspection and Current Limits
-----------------------------

``get_node_version`` and ``get_edge_version`` retrieve visible versions by UUID.
``iter_node_versions``, ``iter_edge_versions``, and ``iter_temporal_commits``
return append order and accept an optional commit horizon.

These TKG-02 operations scan commit history. Logical-ID, version-ID, interval,
system-time, and temporal adjacency indexes are not implemented yet. Temporal
versions are also not visible through Cypher or sampler snapshots. Marker-backed
data that fails hash or envelope validation raises
``TemporalCorruptionError`` rather than returning partial history.

Version dataclasses contain immutable metadata and detached entity payloads.
``Node`` and ``Edge`` payload objects remain mutable for compatibility; mutating
a returned payload never changes persisted history, and a later read returns a
fresh decoded payload whose hash still matches storage. Temporal history must be
reopened with the same configured serializer used to write it.
