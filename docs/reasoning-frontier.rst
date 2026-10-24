:orphan:

Pinned frontier-demand queries
========================================

``answer_pinned_claims`` interleaves indexed claim reads and positive binary
rule evaluation under a caller-owned temporal ``GraphReadView``. Pass a binary
``Program`` (including ontology compiler output) and an ``Atom`` goal. This is
opt-in: existing demand, graph, session and executor entry points are unchanged.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.reasoning import Atom, Fact, Program, Rule, Variable, ReasoningLimits
   from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
   from gestaltdb.reasoning.adapters.frontier import answer_pinned_claims

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = Program([
       Rule("base", [Atom(x, "edge", y)], Atom(x, "reach", y)),
       Rule("step", [Atom(x, "reach", y), Atom(y, "edge", z)], Atom(x, "reach", z)),
   ])
   with TemporaryDirectory() as path:
       graph = GraphDB.create(path, backend="lmdb", serializer="json")
       try:
           for source, target in [("a", "b"), ("b", "c")]:
               graph.assert_claim(subject=source, predicate="storedEdge", object=target,
                                  polarity="positive", agent="example", valid=(0, 100))
           with graph.read_view(valid_time=50) as view:
               result = answer_pinned_claims(
                   view, program, Atom("a", "reach", y), world="default",
                   ontology_revision="example-v1", selection=ClaimSelection(),
                   mapping=ClaimMapping([("storedEdge", "edge")]),
                   limits=ReasoningLimits(),
               )
           assert result.answer.facts == (Fact("a", "reach", "b"), Fact("a", "reach", "c"))
           assert len(result.answer.evidence) == 2
       finally:
           graph.close()

Semantics and termination
---------------------------

The storage-neutral scheduler in ``reasoning/frontier.py`` reuses reference
indexed lookup/unification. Each demanded predicate/binding is read once for
assertions, including predicates that also have rule heads. Rules propagate
bindings through their bodies in declared order. Newly discovered bindings
schedule new reads; newly derived facts trigger another monotone round.
An unbound/disconnected body can require a whole-predicate read. Repeated
variables and constants are checked during matching, including on the goal.

Soundness follows from adding only supplied assertions and valid positive rule
instances. For completeness, at the fixed point every reachable demand has read
its assertions, every matching rule has been considered, and every available
body prefix has propagated its bindings and matched subsequent atoms. Induction
over finite derivations therefore covers every answer to the requested goal.
Recursion does not rely on a seed-only subject filter or path enumeration.

For a finite source and function-free program, terms come only from assertions
and program constants. There are finitely many endpoint-binding requests and
ground facts. Both sets grow monotonically and requests are deduplicated, so
unlimited evaluation terminates; explicit limits can fail earlier. There is no
persistent cache, publication, automatic executor selection or closure proof.

Supported scope and authentication
-----------------------------------

Only positive binary entity ``Program``/``Atom`` values are admitted. Typed
n-ary/literal programs, negation, aggregates and equality-rewriting modes are
not accepted. Use their existing complete evaluators explicitly. A ``sameAs``
predicate here is just an ordinary predicate, not implicit equality.

The adapter preserves one authenticated temporal prefix, world, valid-time
point, selection policy, alias mapping and exact fetched version IDs. It never
opens another database handle or repins after a write. This temporal-prefix
contract works on LMDB, LevelDB and PyRex; it is not a promise of a unified
mutable-state snapshot on LevelDB. The returned source context and caller
revision do not independently certify that a program implements an ontology.

Results contain complete goal answers, not a full world closure. Fetched
``Evidence`` references identify input versions, not all supports, minimal
explanations or independent proof verification.

Work, cancellation and performance
------------------------------------

``ReasoningLimits`` are cumulative across scheduler rounds: input rows count
every yielded evidence occurrence, even overlap between different requests;
``max_facts`` bounds distinct fetched/derived facts; ``max_iterations`` bounds
scheduler passes; ``max_candidate_rows`` bounds scheduler/rule/body visits and
indexed unification attempts. Its counter is intentionally not comparable to
the indexed full-closure evaluator's attempted-unification counter.
``max_requests`` separately bounds retained binding requests.

``ClaimSourceLimits.max_scanned_claims`` is cumulative across extraction calls,
including versions rejected by agent/source policy. It counts versions yielded
by claim queries, not internal index visits, commit authentication, decoding or
history reads. Predicate/subject filters use existing indexes; fully bound
triples can use statement lookup. Object-only restrictions are forwarded and
filtered by the existing query implementation, not a newly invented object
index. Alias inversion occurs before storage reads.

Cancellation is cooperative between scheduler units and source rows, not inside
storage calls/authentication or a blocked iterator. Any error/limit/cancellation
raises without a successful partial result; the borrowed view remains owned by
the caller. Row/work limits are not byte/RSS or hard deadline guarantees.

Fewer yielded rows do not imply lower wall time: repeated queries and source
authentication can dominate. The initial scheduler deliberately revisits rules
and prefixes; it is a correctness-first bounded reference strategy, not a native
join optimizer. Compare it with predicate-selective extraction on the same
authenticated source before claiming a workload-specific improvement. Larger
pipeline/executor/native comparisons are tracked in #342/#194/#255.
