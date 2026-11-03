Bounded Modal and Epistemic Evaluation
======================================

Start with :doc:`claims-introduction` for sourced evidence and four-valued status,
then :doc:`stored-rules` for persisted derivations. This guide adds explicit
world links; modal evaluation is read-only and does not run rules or authenticate
the real-world truth of a belief. ``KNOWS`` selects a named logical frame, not an
independent certification that an agent knows a true proposition.

Terms before formulas
----------------------

**Epistemic** concerns knowledge; **modal** operators express claims such as
belief, knowledge or possibility relative to other contexts. A :term:`world` is
a named claim context, and :term:`accessibility` explicitly links contexts for
an agent. A **frame** is the relevant world/link structure. These are logical
contexts and relations, not separate physical databases.

Contradictory evidence can coexist: :term:`paraconsistent` means that a
contradiction does not make unrelated propositions follow. A missing positive
claim is not automatically a negative claim. The :doc:`glossary` explains the
related :term:`bitemporal`, :term:`valid time` and :term:`system time` terminology.

World models
-------------

GestaltDB evaluates finite world models over the existing bitemporal claim
history. Accessibility is not inferred: each agent and frame has explicit,
temporal links created with ``assert_world_accessibility``. ``belief``,
``knowledge``, and ``modal`` are independent frames, so ``BELIEVES`` and
``KNOWS`` cannot silently collapse to the same relation.

Semantics
---------

Every formula has independent truth and falsity support. The resulting
``ClaimStatus`` is ``supported``, ``refuted``, ``both``, or ``unknown``.
``NOT`` swaps support and refutation. ``AND`` and ``OR`` use the standard
four-valued, paraconsistent truth tables; a contradiction therefore does not
entail an unrelated proposition.

``BELIEVES``, ``KNOWS``, and ``NECESSARY`` require their formula in every
accessible world and are refuted by a refuting accessible world. ``POSSIBLE``
is supported by a supporting accessible world and refuted only when every
accessible world refutes its formula. A modal expression with no matching
positive accessibility facts is ``unknown``, not vacuously true. Negative
accessibility claims do not create links.

Atoms read all visible claims in their world unless the atom includes an
``agent`` field. Accessibility always requires the evaluating agent. Reads are
pinned to one ``GraphReadView`` at the requested valid time and system horizon.
``max_depth`` bounds formula nesting and ``max_states`` bounds memoized
expression/world states; exhaustion raises ``ModalEvaluationLimitError``.

Compare belief, knowledge and possibility
-------------------------------------------

This standalone example links Alice's belief frame to a positive report and her
knowledge frame to a denial. A separate modal frame reaches both worlds.
Different results reflect the explicit frames, not confidence voting. All reads
share one authenticated history horizon; a missing agent link remains unknown.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb import ClaimStatus
   from gestaltdb.graphdb import GraphDB

   with TemporaryDirectory() as root:
       graph = GraphDB.create(f"{root}/graph", backend="lmdb", serializer="json")
       try:
           proposition = {"subject": "bob", "predicate": "LOCATED_IN", "object": "paris"}
           for world, polarity in (("rumor", "positive"), ("verified", "negative")):
               graph.epistemic.assert_claim(
                   **proposition, polarity=polarity, agent="source:" + world,
                   world=world, valid=(0, 100),
               )
           for kind, target in (("belief", "rumor"), ("knowledge", "verified"),
                                ("modal", "rumor"), ("modal", "verified")):
               graph.epistemic.assert_world_accessibility(
                   agent="alice", from_world="actual", to_world=target,
                   kind=kind, valid=(0, 100),
               )
           with graph.temporal.open_read_view(
               graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50,
           ) as view:
               statuses = {mode: graph.epistemic.entails(
                   view, "alice", proposition, mode, world="actual", max_depth=4, max_states=100,
               ).status for mode in ("BELIEVES", "KNOWS", "POSSIBLE", "NECESSARY")}
               assert statuses == {
                   "BELIEVES": ClaimStatus.SUPPORTED, "KNOWS": ClaimStatus.REFUTED,
                   "POSSIBLE": ClaimStatus.SUPPORTED, "NECESSARY": ClaimStatus.REFUTED,
               }
               missing = graph.epistemic.entails(
                   view, "outsider", proposition, "KNOWS", world="actual",
                   max_depth=4, max_states=100,
               )
               assert missing.status is ClaimStatus.UNKNOWN
       finally:
           graph.close()

The example uses UTC microseconds on a small schematic timeline. Pass
``system_time`` or ``through_commit`` to ``open_read_view`` for an older horizon.
The result contains a conservative confidence and a deterministic explanation
with the read horizon, visited states, accessibility versions, claim validity
and system times, source metadata, and rule-derivation provenance. Its confidence
is evidence metadata, not a calibrated modal probability. Keep depth/state bounds
finite; a limit exception is not an unknown result or a successful partial answer.

Validated Formula Shape
-----------------------

An atom is ``{"subject": ..., "predicate": ..., "object": ...}``; use
``objectKind: "literal"`` for a literal object and optional ``agent`` to scope
atomic evidence. Logical and nested modal forms are:

**Shape fragments:** ``atom``, ``left`` and ``right`` stand for validated formula
objects, not additional variables supplied by a runnable setup.

.. code-block:: python

   {"operator": "NOT", "formula": atom}
   {"operator": "AND", "operands": [left, right]}
   {"operator": "OR", "operands": [left, right]}
   {"operator": "KNOWS", "agent": "bob", "formula": atom}
   {"operator": "BELIEVES", "agent": "bob", "formula": atom}
   {"operator": "POSSIBLE", "agent": "bob", "formula": atom}
   {"operator": "NECESSARY", "agent": "bob", "formula": atom}

Unknown fields, unknown operators, missing agents for modal operators,
and non-finite expression shapes are rejected before evaluation.

Cypher Procedure
----------------

``kg.entails`` is explicitly allowlisted. Its options require ``world`` and
``validTime`` and accept ``systemTime``, ``throughCommit``, ``maxDepth``, and
``maxStates``. ``systemTime`` and ``throughCommit`` are mutually exclusive.

The following is a query fragment for the example's still-open graph, at the same
valid instant. Ordinary ``MATCH`` does not materialize claims or modal results.

.. code-block:: cypher

   CALL kg.entails(
     'alice',
     {subject: 'bob', predicate: 'LOCATED_IN', object: 'paris'},
     'KNOWS',
      {world: 'actual', validTime: 50, maxDepth: 4, maxStates: 100}
   )
   YIELD status, confidence, explanation
   RETURN status, confidence, explanation
