:orphan:

Ontology v1 implementation contract
===================================

Development status
------------------

This is the ONTO-01 interface agreement for epic #131 on ``onto-dev``.
The immutable reasoning contracts, standalone Datalog evaluator and ontology
compiler now exist. Explanations and the claims adapter are subsequent work
packages; their descriptions below specify required behavior, not shipped features.
See ``ONTO-EPIC.md`` in the repository for priorities and dependencies.

Separation of concerns
----------------------

``gestaltdb.reasoning.model`` and ``gestaltdb.reasoning.protocols`` use only
the standard library. Future core and ontology implementation must not import
GraphDB, storage, serializers, Cypher, sampling or temporal version records.
Adapters translate into these values, never the reverse. The existing package
initializer still eagerly imports other subsystems; standalone architecture
does not mean separate installation or a minimal top-level import footprint.

``Fact(subject, predicate, object)`` contains three nonempty UTF-8 strings.
There are no literals, blank-node generation, confidence weights or implicit IRI
normalization. Local strings are opaque identifiers. Imported IRIs, if supported
later, must preserve full identity. ``Variable(name)`` is the only variable form;
``'?x'`` remains a constant string, including in predicates.

``Atom`` has a constant predicate and string/variable endpoint terms. A ``Rule``
has a nonempty ordered body and one head; all head variables must occur in the
body. Ground heads are allowed. ``Program`` rejects duplicate rule IDs and sorts
its catalog by ID. Its versioned structural digest includes rule IDs, variable
names and body order: it is not a logical-equivalence digest or ontology revision.
Nested collections are copied into immutable tuples.

``DatalogEngine`` implements ``Evaluator.evaluate(program, facts, *, limits)``.
Facts are an iterable, consumed under an input bound, not eagerly exhausted.
``EvaluationResult`` represents complete closure including input facts, with
distinct facts in canonical subject/predicate/object order. ONTO-04 will extend
results with proof data without introducing persistence coupling.

Ontology compilation
--------------------

The profile is named ``gestalt-core-v1``. Class membership is encoded as
``Fact(entity, 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type', class_id)``.
The ontology compiler reserves that predicate for membership; object-property
axioms using it are rejected. The general Datalog kernel has no reserved terms.
Adapters must apply an explicit mapping; native labels and relationship types
are not automatically interpreted as ontology identifiers.

Let ``T(x, C)`` denote membership and ``P(x, y)`` an object-property assertion.
The supported compilation templates are:

* ``SubClassOf(C, D)``: ``T(x, C) -> T(x, D)``.
* ``SubPropertyOf(P, Q)``: ``P(x, y) -> Q(x, y)``.
* Named class/property equivalence: the two corresponding implications.
* ``Domain(P, C)``: ``P(x, y) -> T(x, C)``.
* ``Range(P, C)``: ``P(x, y) -> T(y, C)``.
* ``InverseOf(P, Q)``: ``P(x, y) -> Q(y, x)`` and the converse.
* ``Symmetric(P)``: ``P(x, y) -> P(y, x)``.
* ``Transitive(P)``: ``P(x, y) AND P(y, z) -> P(x, z)``.

Multiple inheritance and cycles are legal and terminate under set semantics.
Named equivalence does not merge individuals. Multiple domain or range axioms
infer every stated class, not a union choice. Stable axiom identities and compiler
rule origins are available in ONTO-03; immutable revision identity is separate from the
kernel's program digest. Hierarchy schema facts themselves are not automatically
included in instance closure; future schema lookups must state their semantics.

Unsupported axiom shapes must fail before evaluation. Property chains,
conjunction, disjointness, equality, functionality, cardinality, datatypes,
negation and existential generation are deferred. There is no claim of complete
RDFS/OWL-RL/OWL-EL/OWL-DL support. Domain and range infer types; they do not validate
required fields. Conflicts never entail arbitrary facts, and missing is not false.

Limits, proofs and failure
--------------------------

``ReasoningLimits`` requires positive integers (not booleans):

* ``max_input_facts``: yielded input values, including duplicates. Consume at most
  the limit plus one value to detect overflow. Reject non-Fact inputs explicitly.
* ``max_rules``: input program size, checked before evaluation.
* ``max_iterations``: executed delta rounds. Zero rounds are allowed for inputs
  requiring no rule evaluation; exceeding the bound fails, not truncates.
* ``max_candidate_rows``: cumulative attempted candidate fact/partial-binding
  joins, including failed unifications, across all rounds. Check while joining,
  not after allocating an unbounded intermediate result.
* ``max_facts``: distinct asserted plus derived closure facts.
* ``max_proof_nodes``: stored explanation nodes. Truncate proofs explicitly if
  necessary without claiming incomplete logical closure.

Inference-counter exhaustion raises ``ReasoningLimitError``. No partial
successful closure is returned and no publication occurs. Malformed input raises
``ReasoningError``. Limits are deterministic work/storage counters, not an exact
wall-clock or process-memory sandbox; arbitrary input iterators can themselves
perform unbounded work. Compilation and construction of already-materialized
program values are outside evaluator resource accounting.

Proofs must be shared finite DAGs linking facts, rules, axiom revisions and input
evidence. A bounded witness is not a complete support index for deletion; no
incremental maintenance guarantee is made in v1. Logical facts are deduplicated
independently of multiple evidence references.

Adapter lifecycle and temporal semantics
-----------------------------------------

``FactSource`` exposes one immutable ``InputContext`` and an evidence iterator.
The adapter owns and pins the read-view lifetime; iteration must not repin or
mix contexts. Each ``Evidence`` carries a ground fact and opaque immutable
reference ID. Many references may support one fact. The source adapter and
consumer must bound evidence consumption before collecting it in memory.

``InputContext`` identifies the input context, world, ontology revision, source
policy and mapping. These identifiers do not authenticate themselves; the adapter
must verify the underlying read provenance and reject incompatible snapshots.
A context identity must include its selected valid-time point and pinned system
visibility. The engine receives plain facts; it cannot establish that the caller
has honestly selected a compatible world or source policy.

The claims adapter is read-only, selecting positive entity-object claims at one
valid-time point in one world under a pinned visible commit prefix. Agent/source
selection is explicit and part of policy identity. Recognized engine-produced
claims are excluded by default to avoid treating derived output as independent
evidence. Opting into derived inputs must be deliberate and recorded in policy.
Explicit negative claims do not participate, but do not suppress positive support
for the same proposition. Confidence does not alter Boolean inference.

Results must remain tied to their context and ontology revision. They are not
validity intervals and are never automatically written as claims or current graph
records. Combining snapshots, worlds or temporal/current data planes requires a
separate explicit adapter policy. Publication, deletion maintenance and historical
interval inference are post-v1 work.

Standalone evaluator (ONTO-02)
------------------------------

The evaluator is stateless across calls and has no database, filesystem, clock,
ontology-catalog or evidence-source access. A minimal executable example:

.. code-block:: python

   from gestaltdb.reasoning.engine import DatalogEngine
   from gestaltdb.reasoning.model import (
       Atom, Fact, Program, ReasoningLimits, Rule, Variable,
   )

   x, y = Variable("x"), Variable("y")
   program = Program([
       Rule("parent-is-ancestor", [Atom(x, "parent", y)], Atom(x, "ancestor", y)),
   ])
   result = DatalogEngine().evaluate(
       program,
       [Fact("alice", "parent", "bob")],
       limits=ReasoningLimits(),
   )
   assert Fact("alice", "ancestor", "bob") in result.facts
   assert result.program_digest == program.digest

Evaluation uses synchronous semi-naive rounds: every join variant anchors one
body atom in the previous round's delta; other atoms use the current full closure.
Every distinct body atom is a possible anchor. Identical repeated atoms need only
one anchor, but retain their body positions. Facts from a round are added together
after all its variants finish, so catalog order does not change round semantics.
Variants can revisit the same logical binding; conclusion set semantics remove
duplicates, and all attempted candidate joins still count toward the work bound.

Predicate, predicate/subject and predicate/object indexes restrict candidates.
Fully bound atoms use exact membership. After the delta anchor, remaining atoms
are selected by the smallest indexed bucket, with original body order breaking
ties. Traversal is iterative depth-first; no unbounded intermediate join table
or recursive Python call chain is constructed. Index bucket order is deterministic
for a fixed logical input, independent of input ordering or hash seed.

One iteration means an executed delta round with at least one scheduled rule
variant. An exhausted input, empty program or delta with no relevant variants
requires no further round. A recursive final round that produces no new facts
still counts. No proof data is collected yet; ``max_proof_nodes`` is reserved
for ONTO-04 and has no effect on closure in this slice.

Input iterators and constructing already-materialized program/limit values remain
caller responsibilities. Exceptions raised by an input iterator propagate;
invalid yielded values raise ``ReasoningError``. Limit failures do not change the
program or input collections and do not affect subsequent calls on the same engine.

Immutable ontology compilation (ONTO-03)
---------------------------------------------

Import ``Ontology`` and the nine supported axiom classes from
``gestaltdb.ontology.model``. Import ``compile_ontology`` from
``gestaltdb.ontology.compiler``. Compilation does not invoke an evaluator, access
a database, install rules or merge entity identities.

.. code-block:: python

   from gestaltdb.ontology.compiler import compile_ontology
   from gestaltdb.ontology.model import Domain, Ontology, SubClassOf, TYPE_PREDICATE
   from gestaltdb.reasoning.engine import DatalogEngine
   from gestaltdb.reasoning.model import Fact, ReasoningLimits

   ontology = Ontology("organization", [
       Domain("worksFor", "Researcher"),
       SubClassOf("Researcher", "Person"),
   ])
   compiled = compile_ontology(ontology)
   result = DatalogEngine().evaluate(
       compiled.program,
       [Fact("alice", "worksFor", "acme")],
       limits=ReasoningLimits(),
   )
   assert Fact("alice", TYPE_PREDICATE, "Person") in result.facts
   assert compiled.revision_id == ontology.revision_id

``Ontology`` copies an explicit list/tuple of axioms, rejects unknown shapes
(including unimplemented subclasses), removes duplicate axioms and sorts them
canonically. Equivalence/inverse pairs normalize their endpoint order because
those axiom forms are symmetric. Identifiers otherwise retain their exact string
identity, including strings beginning with ``?``; the compiler uses typed variables
internally, so such identifiers cannot become variables.

Each axiom has a versioned SHA-256 content identity. The ontology revision digest
also includes the ontology ID and profile. Reordering or repeating axioms does not
change identity; changing ontology ID does change revision identity. Logically
equivalent but structurally different axiom sets need not have equal revision IDs.
These identities are content addresses, not persisted catalog versions or signed
provenance. There is no implicit system time, valid time or network import.

``CompiledOntology`` retains the immutable source ontology, its executable
``program`` and a canonical ``origins`` tuple. Every generated rule has exactly
one ``RuleOrigin(rule_id, axiom_id)``, and every source axiom is represented.
The source ontology's revision scopes that table; callers can resolve exact axiom
values without a storage lookup. Bidirectional axioms have separate forward and
reverse rule IDs, including reflexive cases, so provenance is not erased by a
logical simplification. Compilation currently emits templates directly rather
than materializing hierarchy closure or collapsing strongly connected components.
Cycles terminate through the evaluator's set semantics and resource bounds.
