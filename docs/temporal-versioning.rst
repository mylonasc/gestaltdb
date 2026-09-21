Temporal Version History
========================

GestaltDB can append immutable node and edge versions alongside its existing
mutable graph. Temporal versions use two distinct time dimensions:

* **valid time** is the half-open interval ``[start, end)`` in which a fact is
  true in the modeled domain;
* **system time** is the monotonically increasing UTC instant at which GestaltDB
  committed that version.

The explicit version APIs do not update records returned by ``get_node`` or
``get_edge`` and do not alter Cypher or sampling behavior. Temporal history has
separate indexes for exact versions, logical entities, valid starts, system
times, and typed outgoing/incoming traversal.

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

As-Of Resolution and Traversal
------------------------------

``get_node_version`` and ``get_edge_version`` retrieve visible versions by UUID.
``iter_node_versions`` and ``iter_edge_versions`` return append order and accept
an optional system-time or commit horizon. ``iter_temporal_commits`` accepts a
commit horizon.

``get_node_as_of`` and ``get_edge_as_of`` select a logical entity at one valid
instant and optional inclusive system horizon. Eligible versions are ordered by
commit and in-commit ordinal. The newest assertion or correction supplies the
payload; a newest retraction returns ``None``.

.. code-block:: python

   historical = graph.get_node_as_of(
       "alice",
       valid_time="2025-01-01T00:00:00Z",
       system_time=corrected.system_time,
   )

   edges = list(graph.iter_edges_as_of(
       source="alice",
       edge_type="WORKS_FOR",
       valid_time="2025-01-01T00:00:00Z",
   ))

Temporal traversal resolves each logical edge before checking its winning
source, target, and type, so interval-local topology corrections and retractions
do not leak historical adjacency.

Consistent Read Views
---------------------

``GraphDB.read_view`` captures the contiguous prefix of fully visible commits
and a default valid-time instant. It stops before the first incomplete commit,
so a later marker cannot change the view. Every history and as-of call through
the view uses that horizon, even if newer commits are published through the
graph handle.

.. code-block:: python

   with graph.read_view(valid_time="2025-01-01T00:00:00Z") as view:
       alice = view.get_node_as_of("alice")
       snapshot = view.build_sampler_snapshot("snapshots/2025-01-01")
       token = view.provenance.token

Pass ``system_time=...`` to capture the visible prefix at a historical system
instant, or ``through_commit=...`` to select a commit directly; these options
are mutually exclusive.

The immutable ``ReadViewProvenance`` records the stable database UUID, visible
commit marker and full visibility-prefix digest, valid instant, backend layout,
serializer format, and snapshot mechanism. Its token is SHA-256 over canonical
JSON. Snapshot builds materialize nodes and edges from immutable temporal
history at that exact point,
publish through a staging directory, and store the authenticated provenance.
``SamplerSnapshot.verify_source`` and provenance-aware node/edge hydration fail
if the source identity, serializer, backend, commit marker, or token differs.
Legacy snapshots without source provenance retain their existing unverified
behavior.

``through_commit`` may recreate an older visible view. Database paths are not
part of provenance, so moving a managed database does not change its identity.

Index Maintenance
-----------------

Temporal writes maintain indexes by default. Pass
``index_mode=IndexMaintenanceMode.DEFER`` for bulk append workflows; indexed
reads then fail closed until ``rebuild_temporal_indexes`` or
``rebuild_deferred_indexes`` succeeds. ``DEFER_REBUILD`` rebuilds before the
write returns. Rebuilds are additive and idempotent because canonical history is
append-only. Temporal node scans and untyped endpoint traversal use derived
catalog indexes in addition to logical, system, and typed-adjacency indexes. A
database predating any current temporal index format is automatically reported
with the ``temporal`` stale family and can be migrated with
``rebuild_deferred_indexes``.

Current Limits
--------------

Read views cover immutable temporal history on every backend. Bitemporal Cypher
``MATCH`` reads use these views, while unqualified Cypher retains mutable
current-state behavior. Read views do not make mutable current-state records
snapshot-safe on backends without a unified read snapshot. Temporal property
indexes and temporal writes through Cypher are not implemented yet. Temporal
sampler snapshots support point/window traversal, causal paths, node
availability, and time-aware hard-negative rejection. Marker-backed data that fails hash or
envelope validation raises
``TemporalCorruptionError`` rather than returning partial history.

Version dataclasses contain immutable metadata and detached entity payloads.
``Node`` and ``Edge`` payload objects remain mutable for compatibility; mutating
a returned payload never changes persisted history, and a later read returns a
fresh decoded payload whose hash still matches storage. Temporal history must be
reopened with the same configured serializer used to write it.
