The gestalt-core-v1 fragment and its compiler
==============================================

``gestaltdb.ontology.model`` defines immutable axioms: ``SubClassOf``,
``SubPropertyOf``, ``EquivalentClasses/Properties``, ``Domain``, ``Range``,
``InverseOf``, ``Symmetric``, ``Transitive``, plus ``PropertyChain``,
``IntersectionSubClassOf`` / ``UnionSubClassOf`` (left-side only) and scoped
``Reflexive``. ``PROFILE = "gestalt-core-v1"``. Each axiom carries a content
``axiom_id`` (``sha256`` over ``["gestaltdb-axiom-v1", signature]``).

``compile_ontology(Ontology(id, axioms))`` (``ontology/compiler.py``) lowers
each axiom to safe positive Horn rules with ``rule_id =
ontology:{axiom_id}:{direction}`` and a ``RuleOrigin`` row per rule. The
``CompiledOntology`` checks that origins cover exactly the source axioms.

.. math::

   \frac{(x, \texttt{rdf:type}, C)}{(x, \texttt{rdf:type}, D)}
   \quad (C \sqsubseteq D) \qquad
   \frac{(x, p, y)}{(x, q, y)} \quad (p \sqsubseteq q) \qquad
   \frac{(x, p, y)\;\;(y, p, z)}{(x, p, z)} \quad (\text{Transitive}(p))

Rejected (fail closed): disjunctive heads, unscoped reflexivity, chains of
length != 2, ``rdf:type`` as a property, right-side unions. This is *not* full
RDFS/OWL — see :doc:`ontology <../ontology>` and gated profile work (#184,
#185).

Theory and further reading
---------------------------

- `Description logic <https://en.wikipedia.org/wiki/Description_logic>`_
  studies the decidable fragments of first-order logic behind OWL
  (``ALC``, ``SHOIN``, ``SROIQ``). Gestalt-core-v1 sits far below them: only
  the axioms with direct Horn rewrites are admitted, which is why this
  fragment needs no tableau reasoner.
- `Web Ontology Language
  <https://en.wikipedia.org/wiki/Web_Ontology_Language>`_ (`OWL 2 overview
  <https://www.w3.org/TR/owl2-overview/>`_) standardizes the full vocabulary
  (existentials, cardinality, keys) that gestaltdb deliberately excludes;
  the RE-23 profile matrix (``reasoning/profiles.py``) records exactly which
  OWL RL constructs map to Horn rules and which are rejected.
- `Horn clause <https://en.wikipedia.org/wiki/Horn_clause>`_ — at most one
  positive literal — is the logical shape every compiled rule must have.
  Anything that cannot be written this way (disjunctive heads, unscoped
  reflexivity) fails closed at compile time.

**Intuition.** Read the picture below in two layers: the bottom row is *data*
(what someone asserted), the top row is *schema* (what the arrows mean).
``domain`` says "anything a ``worksFor`` arrow leaves is a ``Researcher``";
``subClassOf`` says "every ``Researcher`` is also a ``Person``". The
``inferred type`` arrow is the only one nobody stored — the engine pencils
it in:

.. figure:: figures/subsumption-example.svg
   :width: 100%
   :alt: Data vs schema: worksFor domain and Researcher subClassOf Person infer alice is a Person

   Schema (top) over data (bottom); ``inferred type`` is derived, never stored.

.. code-block:: python

   from gestaltdb.ontology import Ontology, SubClassOf, Domain, compile_ontology
   from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits
   from gestaltdb.ontology import TYPE_PREDICATE

   plan = compile_ontology(Ontology("people", [
       Domain("worksFor", "Researcher"),
       SubClassOf("Researcher", "Person"),
   ]))
   result = DatalogEngine().evaluate(
       plan.program, [Fact("alice", "worksFor", "acme")],
       limits=ReasoningLimits(), collect_proofs=True)
   assert Fact("alice", TYPE_PREDICATE, "Person") in result
