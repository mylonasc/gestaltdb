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
backend transaction. All filesystem-backed stores reserve commit IDs through a
durable sidecar sequence while holding a database-wide writer lock. LevelDB and
default PyRex then write the descriptor and records and publish a visibility
marker last. Records without a valid marker remain invisible. Reservations are
never reused, including when an outer backend transaction rolls back, so visible
commit IDs may contain gaps.

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

End-to-End Temporal Knowledge Workflow
--------------------------------------

A production workflow can keep one graph fact aligned across each read layer:

.. code-block:: python

   from gestaltdb import ClaimStatus, SamplerEngine, TemporalContext, TemporalInstant

   # Append node and edge versions, then query the same valid instant through Cypher.
   graph.put_edge_version(employment_edge, valid=(0, None))
   rows = graph.query(
       "MATCH (a {id: 'alice'})-[:WORKS_FOR]->(b) "
       "FOR VALID_TIME AS OF datetime($at) RETURN b.id AS company",
       {"at": "2024-06-01T00:00:00Z"},
   )

   # Export one authenticated history horizon and filter before seeded fanout.
   snapshot = graph.build_sampler_snapshot(
       "snapshots/knowledge", temporal=True, time_bucket="day"
   )
   engine = SamplerEngine(snapshot, seed=7)
   sampled = engine.sample_neighbors(
       [alice_compact_id], fanout=10,
       temporal=TemporalContext.as_of(TemporalInstant.parse("2024-06-01T00:00:00Z")),
   )

   # Claims remain sourced; rules derive claims; modal evaluation returns evidence.
   graph.assert_claim(
       subject="alice", predicate="WORKS_FOR", object="acme",
       polarity="positive", agent="source:hr", world="verified", valid=(0, None),
   )
   graph.create_rule(
       "employment-implies-affiliation",
       when=[("?p", "WORKS_FOR", "?c")], then=("?p", "AFFILIATED_WITH", "?c"),
   )
   graph.run_rules(as_of=0, world="verified")
   graph.assert_world_accessibility(
       agent="auditor", from_world="actual", to_world="verified",
       kind="knowledge", valid=(0, None),
   )
   result = graph.entails(
       "auditor",
       {"subject": "alice", "predicate": "AFFILIATED_WITH", "object": "acme"},
       "KNOWS", world="actual", valid_time=0, max_depth=4, max_states=100,
   )
   assert result.status is ClaimStatus.SUPPORTED

Temporal snapshots contain graph history, not claims. Claims, rule conclusions,
truth maintenance, and modal evidence remain in the source database and share
the read-view commit horizon. Use time-aware hard-negative policies described in
:doc:`typed-sampling` to prevent future positives from leaking into training.

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

Migrating ``TimeIndexedEdge``
-----------------------------

The deprecated ``TimeIndexedEdge`` class encoded a datetime in a mutable edge
key; it is not an immutable temporal version. Use
``migrate_time_indexed_edges`` once, with concurrent writers stopped. The method
maps successive timestamps for each logical edge ID to half-open intervals,
publishes all new versions in one temporal commit, and can optionally delete the
legacy records after publication. Validate first with ``delete_legacy=False``;
cleanup is independently retryable.

Migration fails closed for undecodable payloads, payload/key mismatches,
duplicate timestamps, or changed legacy input after a previous migration. It
does not rewrite existing temporal history or snapshots. See :doc:`operations`
for the backup, retry, serializer, snapshot, and recovery contract.

Epistemic Claims and Provenance
-------------------------------

Epistemic claims are immutable, sourced subject-predicate-object assertions or
denials. They use the same valid/system-time commits and read views as node and
edge versions, but remain separate from graph edges and current-state Cypher.
Entity objects are stable graph IDs; pass ``object_kind="literal"`` for a
JSON-compatible literal value.

.. code-block:: python

   from gestaltdb import ClaimStatus

   reported = graph.assert_claim(
       subject="alice", predicate="WORKS_FOR", object="acme",
       polarity="positive", agent="source:hr-feed", source="hr-2024.csv",
       confidence=0.97, world="reported", provenance={"row": 42},
       valid_from="2024-01-01T00:00:00Z",
   )
   graph.assert_claim(
       subject="alice", predicate="WORKS_FOR", object="acme",
       polarity="negative", agent="source:investigator", source="interview-7",
       confidence=0.6, world="reported",
       valid_from="2024-01-01T00:00:00Z",
   )
   assert graph.claim_status(
       "alice", "WORKS_FOR", "acme",
       valid_time="2024-06-01T00:00:00Z", world="reported",
   ) is ClaimStatus.BOTH

``Claim.statement_id`` is a deterministic SHA-256 identity for the proposition
only. ``Claim.claim_id`` additionally includes polarity, agent, source, and
world. Confidence and provenance are not identity fields, so
``correct_claim`` can revise them while preserving the sourced claim identity.
Confidence is optional or a finite number from 0 through 1; it never determines
polarity. Claim payloads and provenance are canonical JSON and therefore do not
depend on the graph's configured entity serializer.

``iter_claims_as_of`` filters by statement, subject, predicate, object kind,
agent, source, world, and polarity. ``get_claim_as_of`` resolves one claim ID.
Both accept ``system_time`` or ``through_commit`` and use half-open valid-time
intervals. Corrections and retractions append history:

.. code-block:: python

   reviewed = graph.correct_claim(
       reported.logical_id,
       supersedes_version_id=reported.version_id,
       confidence=0.99,
       provenance={"row": 42, "reviewed": True},
   )
   graph.retract_claim(
       reported.logical_id,
       supersedes_version_id=reviewed.version_id,
       valid_from="2025-01-01T00:00:00Z",
       reason="source withdrew the assertion",
   )

``claim_status`` uses open-world four-valued semantics: ``SUPPORTED`` when at
least one matching positive claim is visible, ``REFUTED`` for negative only,
``BOTH`` when both polarities coexist, and ``UNKNOWN`` when neither is visible.
A ``GraphReadView`` exposes the same claim lookup, iteration, and status methods
at its pinned system horizon and default valid time.

Positive Horn Rules
-------------------

``create_rule`` validates and appends immutable system-time versions of named
positive Horn rules. Predicates are constants, and every variable in the head
must occur in the body. Invalid or unsafe definitions are rejected before the
catalog changes. Reusing a rule name creates a new version; ``get_rule`` and
``iter_rule_versions`` accept a system-time horizon.

.. code-block:: python

   rule = graph.create_rule(
       "employment-implies-affiliation",
       when=[("?p", "WORKS_FOR", "?c"), ("?c", "MEMBER_OF", "?g")],
       then=("?p", "AFFILIATED_WITH", "?g"),
   )
   result = graph.run_rules(
       as_of="2024-06-01T00:00:00Z",
       max_iterations=100,
       max_derivations=10_000,
       max_justifications=100_000,
   )

``run_rules`` evaluates the latest version of each rule with semi-naive deltas.
Only positive entity-object claims participate. All premises in a match must
share a world, and each justification contributes the intersection of its
premises' half-open valid-time intervals. Overlapping supports for an equivalent
conclusion are consolidated into one validity interval. Derived conclusions use the
reserved ``gestaltdb:rules`` agent identity and retain canonical justification
records containing the rule version ID and ordered premise version IDs.
Recursive finite rule sets stop at a fixpoint, and repeating an unchanged run
is idempotent.

The three positive resource limits are mandatory safeguards. Exceeding one
raises ``RuleEvaluationLimitError`` before any staged conclusion is persisted.
Successful conclusions are published together in one temporal commit and
remain separate from mutable graph edges and unqualified Cypher. Rule
evaluation does not consume negative claims, literal-object claims, or claims
from different worlds in one match.

Incremental Truth Maintenance and Explanations
-----------------------------------------------

``maintain_truth`` reconciles rule-derived claims after claims or rule versions
change. It recomputes a bounded fixpoint from non-derived positive entity
claims, indexes premise/rule dependents and independent supports, and appends
only changed derivations. Removing one support corrects the conclusion's
justifications; the conclusion is retracted only after its final support
disappears. Derived claims are never authoritative seeds, so unsupported rule
cycles terminate instead of sustaining themselves.

.. code-block:: python

   graph.retract_claim(
       reported.logical_id,
       supersedes_version_id=reviewed.version_id,
       reason="source correction",
   )
   maintained = graph.maintain_truth(
       as_of="2024-06-01T00:00:00Z",
       max_iterations=100,
       max_derivations=10_000,
       max_justifications=100_000,
   )

The first pass requires ``as_of``. Later calls may omit it to resume the valid
time recorded by the latest completed rule run or maintenance pass. Conclusions
are published in one temporal commit before the rebuildable dependency index;
retrying after interruption is therefore idempotent. A bound failure publishes
no maintenance commit.

``explain_claim`` resolves the claim at both valid and system time and returns a
``ClaimExplanation`` graph. Claim nodes contain exact immutable
``ClaimVersion`` values, rule nodes contain exact ``RuleVersion`` values, and
``derived_by``/``premise`` edges retain premise order. Historical horizons keep
earlier explanations reproducible after later corrections or retractions.
Traversal tracks visited version IDs and accepts ``max_depth`` and ``max_nodes``
bounds; ``truncated`` reports a reached bound.

.. code-block:: python

   explanation = graph.explain_claim(
       derived.logical_id,
       valid_time="2024-06-01T00:00:00Z",
       system_time=derived.system_time,
   )
   assert explanation.root_version_id == derived.version_id

Current Limits
--------------

Read views cover immutable temporal history on every backend. Bitemporal Cypher
``MATCH`` reads use these views, while unqualified Cypher retains mutable
current-state behavior. Read views do not make mutable current-state records
snapshot-safe on backends without a unified read snapshot. Temporal property
indexes and temporal writes through Cypher are not implemented yet. Temporal
sampler snapshots support point/window traversal, causal paths, node
availability, and time-aware hard-negative rejection. Claim, rule, and
explanation Cypher syntax is not implemented. Marker-backed data that fails hash or
envelope validation raises
``TemporalCorruptionError`` rather than returning partial history.

Version dataclasses contain immutable metadata and detached entity payloads.
``Node`` and ``Edge`` payload objects remain mutable for compatibility; mutating
a returned payload never changes persisted history, and a later read returns a
fresh decoded payload whose hash still matches storage. Temporal history must be
reopened with the same configured serializer used to write it.
There is no temporal-history pruning or TTL API; canonical commits are retained
indefinitely. Derived temporal indexes and sampler snapshots are rebuildable,
but corrupted canonical history requires restoring a complete backup.
