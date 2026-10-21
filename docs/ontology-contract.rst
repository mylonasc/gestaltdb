:orphan:

Ontology v1 implementation contract
===================================

Development status
------------------

This is the ONTO-01 interface agreement for epic #131 on ``onto-dev``.
The immutable reasoning contracts, standalone Datalog evaluator, ontology
compiler, bounded witness explanations and pinned claims adapter now exist on
the epic branch. They are not yet a main-branch release.
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

``DatalogEngine`` implements
``Evaluator.evaluate(program, facts, *, limits, collect_proofs=False)``.
Inputs are ``Fact`` or ``Evidence`` values in an iterable, consumed under an input
bound, not eagerly exhausted. Duplicate evidence does not multiply logical facts.
``EvaluationResult`` represents complete closure including input facts, with
distinct facts in canonical subject/predicate/object order. Optional proof data
does not introduce persistence coupling.

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
* ``PropertyChain(Q, R, P)``: ``Q(x, y) AND R(y, z) -> P(x, z)``.
* ``IntersectionSubClassOf((C1, C2), D)``: ``T(x, C1) AND T(x, C2) -> T(x, D)``.
* ``UnionSubClassOf((C1, C2), D)``: ``T(x, C1) -> T(x, D)`` and ``T(x, C2) -> T(x, D)``.
* ``Reflexive(P, C)``: ``T(x, C) -> P(x, x)``.

Multiple inheritance and cycles are legal and terminate under set semantics.
Named equivalence does not merge individuals. Multiple domain or range axioms
infer every stated class, not a union choice. Stable axiom identities and compiler
rule origins are available in ONTO-03; immutable revision identity is separate from the
kernel's program digest. Hierarchy schema facts themselves are not automatically
included in instance closure; future schema lookups must state their semantics.

Unsupported axiom shapes must fail before evaluation. Disjointness, equality,
functionality, cardinality, datatypes, negation and existential generation are
deferred, as are union superclasses and unscoped reflexivity. There is no claim
of complete RDFS/OWL-RL/OWL-EL/OWL-DL support. Domain and range infer types; they
do not validate required fields. Conflicts never entail arbitrary facts, and
missing is not false.

Limits, proofs and failure
--------------------------

``ReasoningLimits`` requires positive integers (not booleans):

* ``max_input_facts``: yielded input values, including duplicates. Consume at most
  the limit plus one value to detect overflow. Reject values other than Fact or
  Evidence explicitly. Evidence entries count separately, even for the same fact.
* ``max_rules``: input program size, checked before evaluation.
* ``max_iterations``: executed delta rounds. Zero rounds are allowed for inputs
  requiring no rule evaluation; exceeding the bound fails, not truncates.
* ``max_candidate_rows``: cumulative attempted candidate fact/partial-binding
  joins, including failed unifications, across all rounds. Check while joining,
  not after allocating an unbounded intermediate result.
* ``max_facts``: distinct asserted plus derived closure facts.
* ``max_proof_nodes``: stored fact-witness records. Truncate proofs explicitly if
  necessary without claiming incomplete logical closure.
* ``max_proof_edges``: stored ordered premise occurrences plus input-reference
  occurrences. This additional bound prevents one long rule body from exceeding
  the proof-storage budget even when the node count is small.

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
The adapter preserves a caller-provided pinned view during its own iteration
lifetime; it does not create or close the caller's graph/view. Iteration must not
repin or mix contexts. Each ``Evidence`` carries a ground fact and opaque immutable
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
still counts. Proof data is collected only when ``collect_proofs=True``; proof
budgets never change this closure or iteration semantics.

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

Result lookups and witness explanations (ONTO-04)
-------------------------------------------------

``fact in result`` performs binary-search membership without a second full fact
index. ``result.match(subject=..., predicate=..., object=...)`` returns immutable
canonical matches. A subject filter narrows the scan using binary-search bounds;
predicate/object-only lookups scan the result and are not index-backed.

Proof collection is disabled by default. Enable it explicitly; proof budgets
apply only to stored witnesses, independently of inference counters. Exceeding
an inference bound still raises and returns no partial successful result.

.. code-block:: python

   from gestaltdb.ontology.compiler import compile_ontology
   from gestaltdb.ontology.model import Domain, Ontology, SubClassOf, TYPE_PREDICATE
   from gestaltdb.reasoning.engine import DatalogEngine
   from gestaltdb.reasoning.model import Fact, ReasoningLimits
   from gestaltdb.reasoning.protocols import Evidence

   ontology = Ontology("organization", [
       Domain("worksFor", "Researcher"),
       SubClassOf("Researcher", "Person"),
   ])
   compiled = compile_ontology(ontology)
   result = DatalogEngine().evaluate(
       compiled.program,
       [Evidence(Fact("alice", "worksFor", "acme"), "claim-version:1")],
       limits=ReasoningLimits(max_proof_nodes=100, max_proof_edges=200),
       collect_proofs=True,
   )
   root = Fact("alice", TYPE_PREDICATE, "Person")
   explanation = result.explain(root, max_nodes=10)
   assert root in result
   assert result.match(subject="alice", predicate=TYPE_PREDICATE)
   assert not explanation.truncated
   assert len(explanation.nodes) == 3
   origins = {origin.rule_id: origin.axiom_id for origin in compiled.origins}
   axioms = {axiom.axiom_id: axiom for axiom in compiled.ontology.axioms}
   used_axioms = {
       axioms[origins[node.rule.rule_id]]
       for node in explanation.nodes if node.rule is not None
   }
   assert used_axioms == set(ontology.axioms)

The core never imports the ontology layer. A caller resolves witness rule IDs
through its compiled ontology's origin table and retains that source revision.
Named input evidence is an opaque caller-supplied reference, not an authenticated
claim version; source validation and snapshot context belong to ONTO-05.

``FactWitness`` stores a fact and either an asserted-input witness or its exact
``Rule`` and ordered premises. Premises retain original body positions even if
join planning reorders evaluation or a body repeats an atom. Only the first
deterministically discovered derivation is kept for a new fact. Asserted facts
are never replaced by cyclic derived support. Synchronous rounds guarantee that
a derived witness depends on earlier-round facts, producing a finite DAG.

For a named input fact, one lexicographically smallest supplied evidence reference
is retained, independent of stream order. Alternative sources and derivations are
intentionally not enumerated; omitting alternatives does not set ``truncated``.
This is a witness policy, never an all-supports index or deletion-maintenance API.
An ordinary Fact input can have an input witness without any external reference.

``ProofTrace`` retains its immutable program and canonical, topologically ordered
witness nodes, breaking ties by fact order. Constructors validate rule membership,
ground rule applications, unique facts and acyclicity. Rule values are shared with
the existing program, not copied per derivation. Source/reference strings do not
create any database access. The trace is bounded by both witness-node count and
premise/reference count; these remain counters, not a byte-accurate RSS bound.

Input witness selection uses a bounded streaming pool of the smallest facts.
If input facts fill the node budget, no derived witnesses may fit; increase the
budget or accept explicit truncation. When edge space runs out, named evidence
may be omitted and a derived witness may be absent entirely. Later stored
witnesses may refer to absent earlier witnesses, with explicit truncation.
Logical closure remains complete regardless of these proof omissions.

``result.proofs.truncated`` conservatively reports any storage omission and is
propagated to every explanation, even if a particular leaf was fully retained.
``result.explain(fact, max_nodes=...)`` also bounds the reachable subgraph's
fact-witness records using an iterative, deduplicated traversal. Query truncation
does not change the stored trace. A known fact with no stored witness yields an
empty, truncated explanation; a fact outside the closure raises ``KeyError``.
Explaining a known fact when collection was disabled raises ``ReasoningError``.
The lower-level trace alone does not establish that an unstored fact is entailed;
use the result wrapper for that distinction.

Pinned temporal claims (ONTO-05)
--------------------------------

Import ``PinnedClaimSource``, ``ClaimSelection``, ``ClaimMapping`` and
``ClaimSourceLimits`` from ``gestaltdb.reasoning.adapters.claims``. Only this
adapter layer depends on GraphDB and temporal/provenance records; the standalone
core and ontology compiler never import it.

The caller first obtains a temporal ``GraphReadView`` through the existing
``graph.read_view(valid_time=..., system_time=...)`` context manager. Database
creation/opening and any identity initialization happen outside extraction.
The adapter requires an already initialized, verifiable source, and does not
initialize identity, rebuild stale indexes or write any records or claims.

For example, this helper accepts an existing view and a compiled ontology:

.. code-block:: python

   from gestaltdb.reasoning.adapters.claims import (
       ClaimMapping, ClaimSelection, ClaimSourceLimits, PinnedClaimSource,
   )
   from gestaltdb.reasoning.model import ReasoningLimits

   def reason_over_claims(view, compiled, *, world):
       with PinnedClaimSource(
           view,
           world=world,
           ontology_revision=compiled.revision_id,
           selection=ClaimSelection(),  # Explicitly all agents and sources.
           mapping=ClaimMapping(),      # Preserve all claim predicates.
           limits=ClaimSourceLimits(max_scanned_claims=10_000),
       ) as source:
           return source.evaluate(
               compiled, limits=ReasoningLimits(), collect_proofs=True,
           )

``ClaimSelection`` normalizes explicit agent/source collections into immutable
canonical tuples. ``None`` means all, an empty collection means none, and
``sources=(None,)`` means only claims with no source. These distinct policies
have distinct fingerprints. ``include_derived=False`` excludes recognized
``gestaltdb:rules`` / ``gestaltdb.rules`` and ``gestaltdb:ontology`` /
``gestaltdb.ontology`` agent/derivation-marker pairs. Opt-in is explicit and
changes policy/context identity. These metadata conventions do not authenticate
the original asserting agent or defend against untrusted Python implementations.

Only positive entity-object claims participate, in exactly one selected world
at the view's valid-time point and commit prefix. A negative assertion does not
suppress independent positive support. Confidence is not propagated. Half-open
validity, corrections and retractions use existing claim/as-of semantics.
The adapter yields exact canonical historical version IDs as evidence; multiple
versions/sources for one proposition do not multiply logical facts. The core's
single-witness policy still retains at most one canonical evidence reference.

``ClaimMapping(predicate_aliases=((source, target), ...))`` maps predicates only.
The empty mapping preserves identifiers exactly; no native node label, edge type,
``IS_A`` predicate or other local spelling is implicitly assigned ontology meaning.
For a local membership predicate, explicitly map it to ``TYPE_PREDICATE``.
Unmapped predicates, subjects and objects remain unchanged. Duplicate alias
sources are rejected, even if their targets agree. Mapping identity records this
preserve-unmapped policy and canonical aliases.

``PinnedClaimSource.context`` fingerprints verified source provenance, world,
ontology revision, source policy and mapping. The adapter captures a validated
provenance copy and fixed point, never silently advancing to new commits. Later
appends/corrections may change the live graph but not this view. Mutating or
rebinding the original view's graph, provenance or default time is rejected.
Private read-view/identity accesses are confined to this adapter boundary.

Verification checks persisted identity as well as the handle's cached identity,
backend/serializer identity and the selected commit-visibility digest. It runs
before and after iteration and after inference. Cheap per-item checks catch view
rebinding while consuming records. This detects inconsistency with a source view;
tokens/content digests are not cryptographic attestations of caller-supplied data.

``max_scanned_claims`` counts candidates yielded by the underlying positive,
entity-object, selected-world query, before agent/source/derived filtering.
It detects overflow after at most the limit plus one candidate, even when none
pass policy. ``ReasoningLimits.max_input_facts`` separately bounds yielded
evidence consumed by the engine, including duplicate logical facts. Backend-internal
index scans/candidate-ID materialization and commit-prefix verification are outside
these counters. Neither counter promises a total query-time or RSS ceiling.

Use ``source.evaluate(compiled, ...)`` for a complete result verified after
inference. A direct, partially consumed ``iter_evidence()`` stream is not an atomic
authenticated batch and does not itself bind an arbitrary external engine result
to context. Do not manually concatenate streams from incompatible contexts.

``ClaimReasoningResult`` retains immutable input context, provenance, compiled
ontology, policy/mapping and the core result. It checks their revision, policy,
mapping and program consistency; even two ontology IDs with identical generated
programs remain distinct contexts. ``facts``, membership, ``match`` and ``explain``
delegate to the core result. ``verify_source(graph)`` can recheck the original
source identity/prefix later; it does not rerun inference or authenticate a manually
constructed result. Results remain usable after the source is closed, have no
database handle, and make no interval-validity or materialization guarantee.

Closing the source invalidates its outstanding and future iteration/evaluation
without closing the borrowed graph/view. Keep the caller's graph open for the
duration of extraction/evaluation. Stale temporal indexes fail closed and require
an explicit caller-managed rebuild outside this read-only workflow.

Conformance and isolation gates (ONTO-06)
--------------------------------------------------

The conformance suite is ``tests/test_ontology_conformance.py``. It combines
focused hierarchy/property fixtures and twelve deterministic generated schemas.
Every case is checked against a finite grounding oracle that enumerates safe
substitutions independently of the indexed evaluator. It also checks proof origins,
input-order invariance, positive monotonicity and stateless recomputation after
removing all independent support from a cycle.

Optional differential tests use RDFLib/OWL-RL as an independent ontology oracle.
The fixtures encode the nine supported axiom forms as standard RDF/OWL triples,
with separate individual, class and property vocabularies. Comparison projects
onto named-individual property assertions and memberships in the fixture's named
classes. Extra vocabulary/schema, datatype and sameAs consequences are outside
that projection. There are no negative claims, equality-inducing axioms or
inconsistent ontologies in these oracle fixtures: the oracle is not a validator
for GestaltDB's temporal or conflict-tolerant semantics.

This is selected-fragment differential testing, not a complete W3C conformance
suite or a claim to implement OWL-RL. The independently grounded kernel tests,
single-axiom/mixed compiler tests and temporal adapter isolation/failure tests
remain required even when the optional oracle is absent.

Run the oracle gate explicitly (versions used for this slice)::

   uv run --with "owlrl==7.6.2" --with "rdflib==7.6.0" pytest tests/test_ontology_conformance.py -q

Without those packages, only the twenty oracle comparisons skip; the reference,
architecture and isolated execution checks still run. Do not count oracle skips
as an executed differential gate. These packages are test-only and are not added
to runtime requirements. Run the whole suite with the same ``--with`` options to
exercise the gate alongside regressions; add installed backend extras as needed.

``.github/workflows/ontology.yml`` runs these feature/reference/oracle gates on
Python 3.10-3.13 for relevant PRs and pushes to ``onto-dev`` and ``main``. It installs
the optional oracle and LMDB/LevelDB explicitly, so an absent oracle cannot silently
turn the differential gate into skips. The existing general test workflow targets
``main``; this dedicated workflow also covers epic integration before that release.
The optional PyRex cases may still skip when that backend is not installed.

Static import checks inspect every reasoning-core and ontology module, excluding
the explicit adapter subtree. They require standard-library or inward relative
imports and reject dynamic import hooks. An isolated ``python -I -S`` subprocess
loads the whole compile/evaluate/explain pipeline without site packages, storage,
the GestaltDB initializer or the adapter layer. This proves implementation-layer
independence, not separate packaging or removal of the existing eager root imports.
