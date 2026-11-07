Ontology reasoning
========================

Before the APIs
---------------

An :term:`ontology` describes named classes and relations using semantic
:term:`axioms <axiom>`, such as “every Researcher is a Person”. Compiling those axioms
produces rules that derive additional :term:`facts <fact>` from supplied inputs.
A :term:`witness` explains one derivation by naming its rule and premises; it is
not every independent justification. Start with :doc:`reasoning-introduction`
for a complete facts/rules/explanation example and :doc:`glossary` for terminology.

In RDF/OWL descriptions, :term:`TBox` means the schema/terminology axioms and
:term:`ABox` means assertions about individuals. GestaltDB's optional RDF import
below accepts a selected schema fragment, not arbitrary OWL data or reasoning.
For a relation's **domain** and **range**, the relevant axioms infer class
memberships of its subject and object; they are not checks that reject records
with missing properties. **Subsumption** means inclusion in a more general class
or relation, as defined in :doc:`glossary`.

Supported layer
----------------

The ontology layer compiles immutable named-class/object-property axioms to a
standalone positive-Datalog program. It uses no database handle and adds no
mandatory solver dependency. It does not change asserted labels, relationships,
Cypher results or sampler membership. All database interactions are explicit
adapters; core and compiler depend only inward and on the standard library.

.. _standalone-example:

Classify a person and inspect the explanation
---------------------------------------------

**Standalone example, base install only.** The ontology says that subjects of
``worksFor`` are Researchers and every Researcher is a Person. Compilation turns
those axioms into a program; evaluation applies that program to Alice's employment
fact. No RDF parser, database backend or external solver is needed.

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
   assert Fact("alice", TYPE_PREDICATE, "Researcher") in result
   explanation = result.explain(person)
   assert not explanation.truncated
   assert {origin.axiom_id for origin in plan.origins} == {
       axiom.axiom_id for axiom in plan.ontology.axioms
   }

``TYPE_PREDICATE`` is the relation used for class membership, not a command to add
a stored node label. ``plan.origins`` maps exact generated rule IDs to their
source axioms. ``result.match(subject=..., predicate=..., object=...)`` provides
immutable result lookups. Proof retention is bounded and records one witness,
not every possible justification (:doc:`reasoning-proofs`).

Classification, validation and hierarchy expansion
----------------------------------------------------

.. list-table:: Similar-looking tasks have distinct contracts
   :header-rows: 1

   * - Task
     - Meaning
     - Route
   * - Classification
     - Derive memberships/relations from axioms and facts; domain/range infer types
     - This guide
   * - Validation
     - Report configured conflicts or structural violations; do not infer equality
       or repair source data automatically
     - :doc:`reasoning-validation`
   * - Hierarchy expansion
     - Expand explicitly mapped parent labels/relations for a query or draw;
       do not run the complete ontology program
     - The hierarchy adapter below

The compiler supports subclass/subproperty, named equivalence, domain/range,
inverse, symmetric and transitive properties, binary property chains, left-side
class conjunction/disjunction and scoped reflexivity. It is the selected
``gestalt-core-v1`` fragment, **not full RDFS/OWL**. Equality, datatype reasoning,
disjointness, existential generation and negation are not compiled by this
fragment. Separate equality, validation and typed-rule APIs have their own
contracts. Cycles terminate under set semantics; missing input is not false.

Optional offline RDF import
---------------------------

Install ``gestaltdb[rdf]`` and import from
``gestaltdb.ontology.adapters.rdf``. ``import_rdf(RDFDocument(text),
ontology_id="urn:my-ontology")`` returns an immutable import report; call its
``compile()`` method before reasoning. Turtle, N-Triples and UTF-8 RDF/XML are
supported. Named terms retain full IRIs. Class/object-property declarations and
the selected axiom forms are accepted, including binary ``owl:propertyChainAxiom``
lists, left-side ``owl:intersectionOf``/``owl:unionOf`` collections (with
intersection superclasses expanded to subclass pairs) and named class/property
forms; ABox statements, restrictions, builtin
class/datatype semantics and other unsupported logic fail strictly by default.
Right-side unions, unscoped ``owl:ReflexiveProperty``, longer chains and
unused collections are rejected or reported incomplete.

No URLs or files are loaded. Resolve ``owl:imports`` explicitly with an in-memory
``imports={iri: RDFDocument(...)}`` map. Cycles are deduplicated. JSON-LD plugins,
XML DTDs/entities and implicit network import resolution are not supported.
``RDFImportLimits`` bounds cumulative bytes, triples and documents. These are
not an operating-system sandbox. ``strict=False`` returns diagnostics and
``complete=False`` on unsupported logic; compiling that report additionally
requires ``allow_incomplete=True``. Never represent such output as complete
OWL/RDFS reasoning. Standard label/comment/version annotations are counted and
ignored, not converted into logical facts.

Sampling and Cypher type hierarchies
------------------------------------

``gestaltdb.ontology.adapters.hierarchy`` builds the standalone
``LabelHierarchy`` and ``RelationHierarchy`` values used by opt-in Cypher
matching and sampler expansion from an ontology's direct ``SubClassOf`` and
``SubPropertyOf`` axioms:

.. code-block:: python

   from gestaltdb.ontology import Ontology, SubClassOf, SubPropertyOf
   from gestaltdb.ontology.adapters.hierarchy import (
       label_hierarchy_from_ontology,
       relation_hierarchy_from_ontology,
   )

   ontology = Ontology("urn:example:freight", (
       SubClassOf("Ocean_Freight", "Transport_Edge"),
       SubClassOf("Refrigerated_Freight", "Ocean_Freight"),
       SubPropertyOf("Ocean_Freight", "Transport_Edge"),
   ))
   labels = label_hierarchy_from_ontology(ontology)
   relations = relation_hierarchy_from_ontology(ontology)
   assert labels.closure("Transport_Edge") == (
       "Transport_Edge", "Ocean_Freight", "Refrigerated_Freight")
   assert relations.closure("Transport_Edge") == (
       "Transport_Edge", "Ocean_Freight")

Pass ``labels`` to ``GraphDB.query(..., label_hierarchy=labels)`` and
``relations`` to ``SamplerSnapshot.expand_relations(...)``.

Only direct subsumption is flattened; the ontology revision is not modified and
no closure is persisted. Multiple superclasses are preserved, duplicate
declarations are deduplicated, and cycles or self-parenting fail closed.
``EquivalentClasses``, ``EquivalentProperties``, intersection and union
subsumption require a canonicalization policy that a strict hierarchy cannot
infer, so the adapter raises ``ValueError`` rather than silently dropping those
semantics. Resolve them explicitly or build the hierarchy directly.

Read-only temporal claims
--------------------------------

The :doc:`architecture` guide maps ontology compilation, source extraction,
and rule evaluation to entity-classification and knowledge-enrichment workflows.

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
           graph.epistemic.assert_claim(subject="alice", predicate="worksFor", object="acme",
                              agent="registry", polarity="positive", valid=(0, 100))
           with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
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
For bounded batched extraction with explicit budgets, capability probing and
per-batch verification, see :doc:`reasoning-extraction`.

Limits and deployment
---------------------------

For explicit current-property-graph projection and derived-relation neighbor
sampling, see :doc:`ontology-graph`. This is separate from temporal claims.

Inference bounds fail closed; proof-node/edge bounds only truncate the witness
trace, explicitly. A trace stores one witness, not all independent supports.
Counters are not a wall-clock/RSS sandbox for arbitrary Python inputs. Neither
the native engine nor a solver swap makes temporal extraction free.
No JVM integration or mandatory external solver is provided. Comparison tools
do not become application executors through their benchmark results. See
:doc:`ontology-benchmarks` for qualified timing/footprint evidence and
:doc:`ontology-contract` for exact interface and conformance boundaries.
For additive relational IR and structural plan inspection, see
:doc:`reasoning-execution` (no new execution semantics).

For standalone rule construction, start with :doc:`reasoning-introduction` and
:doc:`reasoning-typed-tutorial`. Continue with :doc:`reasoning-demand` for selective
answers, :doc:`reasoning-maintenance` for support-aware updates,
:doc:`reasoning-equality` for snapshot-local representatives and
:doc:`reasoning-validation` for reports rather than inferred memberships.
