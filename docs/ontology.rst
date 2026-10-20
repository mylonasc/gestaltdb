Ontology reasoning
========================

The ontology layer compiles immutable named-class/object-property axioms to a
standalone positive-Datalog program. It uses no database handle and adds no
mandatory solver dependency. It does not change asserted labels, relationships,
Cypher results or sampler membership. All database interactions are explicit
adapters; core and compiler depend only inward and on the standard library.

Optional offline RDF import
---------------------------

Install ``gestaltdb[rdf]`` and import from
``gestaltdb.ontology.adapters.rdf``. ``import_rdf(RDFDocument(text),
ontology_id="urn:my-ontology")`` returns an immutable import report; call its
``compile()`` method before reasoning. Turtle, N-Triples and UTF-8 RDF/XML are
supported. Named terms retain full IRIs. Class/object-property declarations and
the selected axiom forms are accepted; ABox statements, restrictions, builtin
class/datatype semantics and other unsupported logic fail strictly by default.

No URLs or files are loaded. Resolve ``owl:imports`` explicitly with an in-memory
``imports={iri: RDFDocument(...)}`` map. Cycles are deduplicated. JSON-LD plugins,
XML DTDs/entities and implicit network import resolution are not supported.
``RDFImportLimits`` bounds cumulative bytes, triples and documents. These are
not an operating-system sandbox. ``strict=False`` returns diagnostics and
``complete=False`` on unsupported logic; compiling that report additionally
requires ``allow_incomplete=True``. Never represent such output as complete
OWL/RDFS reasoning. Standard label/comment/version annotations are counted and
ignored, not converted into logical facts.

Standalone example
------------------------

.. code-block:: python

   from gestaltdb.ontology import Domain, Ontology, SubClassOf, TYPE_PREDICATE, compile_ontology
   from gestaltdb.reasoning import DatalogEngine, Fact, ReasoningLimits

   plan = compile_ontology(Ontology("people", [
       Domain("worksFor", "Researcher"), SubClassOf("Researcher", "Person"),
   ]))
   result = DatalogEngine().evaluate(
       plan.program, [Fact("alice", "worksFor", "acme")],
       limits=ReasoningLimits(), collect_proofs=True,
   )
   person = Fact("alice", TYPE_PREDICATE, "Person")
   assert person in result
   explanation = result.explain(person)
   assert not explanation.truncated

Use ``result.match(subject=..., predicate=..., object=...)`` for immutable result
lookups. The compiled origin table maps exact witness rules back to source axioms.
The profile supports subclass/subproperty, named equivalence, domain/range,
inverse, symmetric and transitive properties. Cycles terminate under set semantics.
It is a selected ``gestalt-core-v1`` fragment, not full RDFS or OWL support.
Equality, datatype reasoning, disjointness, existential generation and negation
are not supported. Domain/range infer memberships rather than validate missing data.

Read-only temporal claims
--------------------------------

This source adapter never writes claims. The separate explicit, point-only
publication operation is documented in :doc:`ontology-publication`.

Install an optional backend for this example (``gestaltdb[lmdb]``). The existing
read view pins one valid-time point and visible commit prefix. The adapter verifies
its source, policy, mapping and ontology revision before returning a scoped result.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.ontology import Domain, Ontology, compile_ontology
   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection, PinnedClaimSource

   plan = compile_ontology(Ontology("people", [Domain("worksFor", "Person")]))
   with TemporaryDirectory() as directory:
       graph = GraphDB.create(directory, backend="lmdb", serializer="json")
       try:
           graph.assert_claim(subject="alice", predicate="worksFor", object="acme",
                              agent="registry", polarity="positive", valid=(0, 100))
           with graph.read_view(valid_time=50) as view:
               with PinnedClaimSource(view, world="default", ontology_revision=plan.revision_id,
                                      selection=ClaimSelection(), mapping=ClaimMapping()) as source:
                   scoped = source.evaluate(plan, limits=ReasoningLimits(), collect_proofs=True)
           assert scoped.context.ontology_revision == plan.revision_id
       finally:
           graph.close()

Only selected positive entity claims participate. Explicit negatives do not
suppress independent positive support; confidence does not become a probability.
Recognized derived claims are excluded unless deliberately opted in. The source
does not initialize identity, rebuild indexes or publish results. A point result
does not justify an unbounded validity interval. Raw evidence iteration alone is
not a context-bound atomic evaluation; use the adapter's ``evaluate`` method.

Limits and deployment
---------------------------

For explicit current-property-graph projection and derived-relation neighbor
sampling, see :doc:`ontology-graph`. This is separate from temporal claims.

Inference bounds fail closed; proof-node/edge bounds only truncate the witness
trace, explicitly. A trace stores one witness, not all independent supports.
Counters are not a wall-clock/RSS sandbox for arbitrary Python inputs. Neither
the native engine nor a solver swap makes temporal extraction free.
No JVM integration is supported. Clingo was faster on larger dense synthetic
closures, but does not provide the same work-counter/witness contract in this v1.
See :doc:`ontology-benchmarks` for measured trade-offs, startup/footprint caveats
and reproducible evidence. The development contract is :doc:`ontology-contract`.
