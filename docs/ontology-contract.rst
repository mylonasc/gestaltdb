:orphan:

Ontology v1 implementation contract
===================================

Development status
------------------

This is the ONTO-01 interface agreement for epic #131 on ``onto-dev``.
Only the immutable reasoning contracts exist in this slice. The evaluator,
ontology compiler, explanations and claims adapter are subsequent work packages;
the descriptions below specify their required behavior, not shipped features.
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

The future evaluator implements ``Evaluator.evaluate(program, facts, *, limits)``.
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
rule origins belong to ONTO-03; immutable revision identity is separate from the
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
