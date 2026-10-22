An ontology turns vocabulary into implications
===============================================

Why a schema is more than a set of labels
-----------------------------------------

Chapter 1 stored a researcher but could not retrieve it as a person. The missing
piece is not another index: it is a statement that every researcher is a person.
An **ontology** makes statements about the modeled vocabulary. Its **axioms**
are assumptions the reasoner may use, not observations it discovered itself.

Our two axioms are intentionally small:

* ``Domain("worksFor", "Researcher")``: a subject of a research-appointment
  ``worksFor`` statement belongs to ``Researcher``.
* ``SubClassOf("Researcher", "Person")``: every instance of ``Researcher``
  also belongs to ``Person``. It says nothing about whether every person is a
  researcher; implication is directional.

Schema statements like these are often called the **TBox** (terminological
knowledge); statements about Alice and Acme are the **ABox** (assertional
knowledge). These are useful conceptual roles, not necessarily two physical
databases. An RDF graph can contain both kinds of statement.

Reading the notation
---------------------

``(alice, worksFor, acme)`` is a triple. ``(x, rdf:type, Researcher)`` is a
pattern with a variable ``x``. When ``x = alice``, the pattern becomes a ground
statement: there are no variables left. In formulas, ``rdf:type`` abbreviates
the full reserved IRI supplied by ``TYPE_PREDICATE`` in Python.

``C ⊑ D`` reads "class C is a subclass of class D." An inference bar reads
"if the statements above the line hold, conclude the statement below it":

.. math::

   \frac{(x, \texttt{rdf:type}, C)}{(x, \texttt{rdf:type}, D)}
   \quad (C \sqsubseteq D) \qquad
   \frac{(x, p, y)}{(x, q, y)} \quad (p \sqsubseteq q) \qquad
   \frac{(x, p, y)\;\;(y, p, z)}{(x, p, z)} \quad (\text{Transitive}(p))

Read the three cases as membership inheritance, property inclusion, and
transitivity. In the last case, joining ``x → y`` to ``y → z`` on the shared
``y`` permits ``x → z`` *only because p is declared transitive*. The mere
existence of a two-hop path does not make every relationship transitive.

From one assertion to two consequences
--------------------------------------

**Intuition.** Read the picture below in two groups: blue is *data*
(what someone asserted), purple is *schema* (what the arrows mean).
``domain`` says "anything a ``worksFor`` arrow leaves is a ``Researcher``";
``subClassOf`` says "every ``Researcher`` is also a ``Person``". The
``inferred type`` arrows are computed consequences, not stored labels. Both
``Researcher`` and ``Person`` memberships follow from the one asserted
``worksFor`` fact:

.. figure:: figures/subsumption-example.svg
   :width: 100%
   :alt: Data vs schema: worksFor domain and Researcher subClassOf Person infer alice is a Person

   Schema and data are visually separate; memberships are computed in this
   example, not written back. Explicit publication is a separate API.

.. code-block:: python

   from gestaltdb.ontology import Ontology, SubClassOf, Domain, compile_ontology
   from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits
   from gestaltdb.ontology import TYPE_PREDICATE

   plan = compile_ontology(Ontology("people", [
       Domain("worksFor", "Researcher"),
       SubClassOf("Researcher", "Person"),
   ]))
   asserted = Fact("alice", "worksFor", "acme")
   result = DatalogEngine().evaluate(
       plan.program, [asserted],
       limits=ReasoningLimits(), collect_proofs=True)
   researcher = Fact("alice", TYPE_PREDICATE, "Researcher")
   person = Fact("alice", TYPE_PREDICATE, "Person")
   assert result.facts == tuple(sorted((asserted, researcher, person)))
   assert not result.explain(person).truncated

Read this as a two-step explanation: the domain rule derives ``Researcher``;
the subclass rule uses it to derive ``Person``. ``result`` retains the asserted
fact as well as both conclusions. Its explanation is one bounded witness,
not a complete inventory of alternative supports for deletion maintenance.

.. note::

   Domain is an implication, not an input-validation constraint. An untyped
   subject of ``worksFor`` gains ``Researcher`` membership; it is not rejected
   for lacking that label. Short names in this example are exact local symbols:
   ``Person`` and ``person`` are different constants, and only ``TYPE_PREDICATE``
   supplies the full reserved ``rdf:type`` IRI.

What compilation contributes
-----------------------------

The ontology is convenient for expressing domain meaning; the evaluator needs
executable rules. ``compile_ontology`` is the bridge. It rewrites the two axioms
into "if worksFor, infer Researcher" and "if Researcher, infer Person," retaining
an origin mapping from generated rules back to source axioms. That mapping is
useful when explaining *which modeling assumption* produced an answer.

The selected ``gestalt-core-v1`` fragment includes subclass/subproperty,
equivalence, domain/range, inverse, symmetric and transitive properties,
binary property chains, selected left-side class expressions, and scoped
reflexivity. It is not full RDFS or OWL. Disjunctive conclusions, arbitrary
anonymous restrictions, longer property chains, and unscoped reflexivity must
not be inferred from the fact that the engine supports simpler forms. See
:doc:`ch08-gaps` for boundaries and :doc:`../ontology` for API details.

Theory and further reading
---------------------------

* `OWL 2 overview <https://www.w3.org/TR/owl2-overview/>`_ introduces the wider
  ontology language. Supporting some OWL-shaped axioms is not the same as
  implementing every OWL profile.
* `Description logic <https://en.wikipedia.org/wiki/Description_logic>`_
  provides the foundation for much ontology reasoning, including languages
  with richer class expressions than this tutorial's named-class fragment.
* `Horn clause <https://en.wikipedia.org/wiki/Horn_clause>`_ explains the
  single-conclusion rule shape used by this compiler. The next chapter explains
  how an engine evaluates such rules, rather than treating them as magic.

Check your understanding
-------------------------

**Question.** If we remove the domain axiom, does ``SubClassOf(Researcher,
Person)`` alone still imply that Alice is a person from the ``worksFor`` fact?
What if we instead assert Alice's ``Researcher`` membership directly?

**Answer.** The first inference no longer follows: nothing supplies the
``Researcher`` premise. The second does follow, because the asserted membership
is enough to trigger the subclass implication. A rule does not manufacture its
own missing premise.
