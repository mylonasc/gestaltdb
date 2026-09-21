Bounded Modal and Epistemic Evaluation
======================================

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

Python API
----------

.. code-block:: python

   graph_db.assert_world_accessibility(
       agent="alice",
       from_world="actual",
       to_world="alice-belief-1",
       kind="belief",
       valid_from="2025-01-01T00:00:00Z",
   )
   graph_db.assert_claim(
       subject="bob",
       predicate="LOCATED_IN",
       object="paris",
       polarity="positive",
       agent="source:registry",
       world="alice-belief-1",
       confidence=0.9,
       valid_from="2025-01-01T00:00:00Z",
   )

   result = graph_db.entails(
       "alice",
       {"subject": "bob", "predicate": "LOCATED_IN", "object": "paris"},
       "BELIEVES",
       world="actual",
       valid_time="2025-06-01T00:00:00Z",
       max_depth=4,
       max_states=1000,
   )
   assert result.status.value == "supported"

``system_time`` or ``through_commit`` selects a historical system horizon.
The result contains a conservative confidence and a deterministic explanation
with the read horizon, visited states, accessibility versions, claim validity
and system times, source metadata, and rule-derivation provenance.

Validated Formula Shape
-----------------------

An atom is ``{"subject": ..., "predicate": ..., "object": ...}``; use
``objectKind: "literal"`` for a literal object and optional ``agent`` to scope
atomic evidence. Logical and nested modal forms are:

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

.. code-block:: cypher

   CALL kg.entails(
     'alice',
     {subject: 'bob', predicate: 'LOCATED_IN', object: 'paris'},
     'KNOWS',
     {world: 'actual', validTime: 1735689600000000, maxDepth: 4, maxStates: 1000}
   )
   YIELD status, confidence, explanation
   RETURN status, confidence, explanation
