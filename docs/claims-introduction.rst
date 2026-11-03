Sourced claims and conflicting evidence
========================================

Use ``graph.epistemic`` to record who asserted or denied a proposition, where it
came from, and when it applies. A claim is not a mutable graph edge or an
independently verified fact. Read :doc:`temporal-introduction` for valid versus
recorded time; claims use the same immutable temporal history.

Evidence is not a single Boolean
---------------------------------

Positive evidence supports a proposition; explicit negative evidence refutes it.
Both can coexist without making unrelated propositions follow
(:term:`paraconsistent`). Missing evidence is **unknown**, not false.

.. list-table:: Four-valued claim status
   :header-rows: 1

   * - Positive evidence
     - Negative evidence
     - ``ClaimStatus``
   * - Present
     - Absent
     - ``SUPPORTED``
   * - Absent
     - Present
     - ``REFUTED``
   * - Present
     - Present
     - ``BOTH``
   * - Absent
     - Absent
     - ``UNKNOWN``

Confidence is optional finite metadata in ``[0, 1]``, not a logical probability
or a vote deciding polarity. A high-confidence positive does not erase a
lower-confidence denial. An **absence check** is different again: it tests for
missing positive evidence under an explicit closed-world scope, rather than
asserting a negative claim.

Assert, derive, explain and withdraw support
--------------------------------------------

This complete example records conflicting reports of Alice's employment. A stored
rule derives affiliation from the positive report; the explicit denial does not
silently override that report or create an unrelated conclusion. After withdrawing
the positive source, maintenance retracts the unsupported derived affiliation.
The historical explanation remains readable at its recorded horizon.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb import ClaimStatus
   from gestaltdb.graphdb import GraphDB

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           positive = graph.epistemic.assert_claim(
               subject="alice", predicate="WORKS_FOR", object="acme",
               polarity="positive", agent="hr", source="hr.csv", confidence=0.97,
               world="reported", valid=(0, 100),
           )
           negative = graph.epistemic.assert_claim(
               subject="alice", predicate="WORKS_FOR", object="acme",
               polarity="negative", agent="auditor", source="interview", confidence=0.6,
               world="reported", valid=(0, 100),
           )
           assert graph.epistemic.claim_status(
               "alice", "WORKS_FOR", "acme", valid_time=50, world="reported",
           ) is ClaimStatus.BOTH
           assert graph.epistemic.claim_status(
               "alice", "LOCATED_IN", "paris", valid_time=50, world="reported",
           ) is ClaimStatus.UNKNOWN
           graph.rules.create_rule(
               "employment-affiliation", when=[("?p", "WORKS_FOR", "?c")],
               then=("?p", "AFFILIATED_WITH", "?c"),
           )
           limits = dict(max_iterations=20, max_derivations=100, max_justifications=1000)
           run = graph.rules.run(as_of=50, world="reported", **limits)
           assert run.derived_count == 1
           derived = run.versions[0]
           explanation = graph.rules.explain(
               derived.logical_id, valid_time=50, max_depth=8, max_nodes=100,
           )
           assert not explanation.truncated
           evidence_ids = {node.node_id for node in explanation.nodes}
           assert positive.version_id in evidence_ids and negative.version_id not in evidence_ids
           graph.epistemic.retract_claim(
               positive.logical_id, supersedes_version_id=positive.version_id,
           )
           maintained = graph.rules.maintain(as_of=50, world="reported", **limits)
           assert maintained.retracted_count == 1
           assert graph.epistemic.get_claim_as_of(derived.logical_id, valid_time=50) is None
           assert graph.epistemic.claim_status(
               "alice", "WORKS_FOR", "acme", valid_time=50, world="reported",
           ) is ClaimStatus.REFUTED
           historical = graph.rules.explain(
               derived.logical_id, valid_time=50, system_time=derived.system_time,
           )
           assert historical.root_version_id == derived.version_id
           assert graph.get_node("alice") is None  # claims do not create current graph records
       finally:
           graph.close()

Here integers are UTC microseconds on a schematic timeline. Use timezone-aware
instants or explicit UTC strings for application dates. Stored rules consume
positive entity-object claims in one world; they do not infer arbitrary facts from
a contradiction. A negative claim is not a rule's scoped absence check.

Identity and revision
----------------------

``statement_id`` identifies the proposition. ``claim_id`` also binds polarity,
agent, source and world, so independent sources remain distinguishable.
Confidence/provenance are not identity fields; ``correct_claim`` revises them
without replacing the sourced identity. Corrections/retractions append history
and can be valid-interval-local. Entity IDs are the default object kind; use
``object_kind="literal"`` for canonical JSON-compatible values.

Claims do not require preexisting ``Node`` objects. Graph payload serializer choice
does not change their canonical JSON claim encoding. Indexed reads still require
fresh temporal indexes; exhaust historical iterators and discard partial output
on failure (:doc:`temporal-reads`).

Continue by task
-----------------

- :doc:`stored-rules`: independent supports, bounded maintenance, scoped absence,
  and the difference from standalone Datalog.
- :doc:`modal-epistemic`: explicitly linked worlds, belief/knowledge/possibility
  and one authenticated read horizon.
- :doc:`inference-audit`: tagged model-output evidence and bounded audit exports.
  Confidence scores and exported explanations are not proof verification.
- :ref:`temporal-claims-reference`: exact claim lookup, correction and identity
  contracts. :doc:`temporal-updates` covers shared index/recovery boundaries.
