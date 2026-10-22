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
