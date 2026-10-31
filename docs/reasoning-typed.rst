:orphan:

Typed Datalog: literals, filters, aggregates
============================================================

``gestaltdb.reasoning.terms`` defines strict immutable literal values;
``gestaltdb.reasoning.typed`` evaluates finite n-ary positive Datalog over
entities and literals with kind-strict comparisons, normalized multi-head
rules, a single-pass stratified aggregation stratum and explicitly expanded
predicate variables. The binary entity engine, its digests and its public
behavior are unchanged: ``Literal`` never implicitly converts to an entity
string, and unexpanded predicate variables fail closed.

.. code-block:: python

   from gestaltdb.reasoning import (
       Comparison, GenericEngine, GenericProgram, GenericRule,
       Literal, ReasoningLimits, TypedAtom, TypedFact,
   )
   from gestaltdb.reasoning.model import Variable

   x, y = Variable("x"), Variable("y")
   adult = GenericRule("adult", [TypedAtom("age", [x, y])],
                       [Comparison(">=", y, Literal("integer", 18))],
                       TypedAtom("adult", [x]))
   result = GenericEngine().evaluate(
       GenericProgram([adult]),
       [TypedFact("age", ["alice", Literal("integer", 30)]),
        TypedFact("age", ["bob", Literal("integer", 12)])],
       limits=ReasoningLimits(),
   )
   assert TypedFact("adult", ["alice"]) in result
   assert TypedFact("adult", ["bob"]) not in result

Literals and normalization
----------------------------------------

Kinds are ``boolean``, ``integer``, ``float``, ``text``, ``bytes``,
``instant`` (integer microseconds) and ``null``. Construction validates
exact Python types — ``bool`` is not an integer, ``1.0`` is not an integer
literal — and ``from_lexical`` parses canonical forms strictly (``"007"``
is integer 7, ``"1e3"`` is float 1000.0, empty text is allowed, only the
empty lexical form is null). Anything else fails with ``ReasoningError``;
there is no silent coercion.

Equality is kind-strict: ``1`` (integer) never equals ``1.0`` (float) or
``"1"`` (text). Null is an explicit marker and equals itself, so grouping
and joins stay deterministic. NaN follows IEEE semantics and is unequal to
everything including itself. Ordering (``compare``) applies only within one
kind, excluding nulls and NaN; anything else is incomparable (``None``),
never a ranked guess.

Comparisons in rule bodies
----------------------------------------

``Comparison`` supports ``== != < <= > >=`` over literal operands. Variables
must be bound by relational body atoms (range-restriction is enforced at
construction); unbound variables fail closed. Filters are checked
incrementally as their variables bind, pruning the join before deeper
traversal. Incomparable pairs — cross-kind values, nulls, NaN involvement,
or entity bindings reaching a literal comparison — filter the row out
instead of erroring, consistent with the non-explosive open-world default.
A comparison against an entity is therefore never silently true.

Multi-head rules, aggregates and predicate variables
--------------------------------------------------------

``MultiHeadRule`` holds a finite non-empty head conjunction and normalizes
to equivalent single-head rules with deterministic ``rule_id#index`` IDs.
``AggregateRule`` computes one non-recursive stratum after the positive
fixpoint completes: ``count`` tallies rows per group; ``sum``/``min``/``max``
consume integer/float literals, skipping nulls, and fail closed on other
kinds, mixed kinds or NaN. Targets must be fresh variables, heads are sinks
no rule body may read, and aggregate bodies may not read other aggregate
heads — recursion through aggregation is rejected, not silently unrolled.
Groups with no aggregatable values produce no head fact for that group.

Predicate variables range only over an explicit finite domain via
``expand_predicate_variables`` (Cartesian product in first-occurrence
order, deterministic suffixed IDs). Unexpanded programs fail closed, as do
empty domains, non-string domains and variables shared between predicate
and term positions. There is no open-world predicate invention.

Bounded typed explanations (#392)
----------------------------------------

Pass ``collect_proofs=True`` to ``evaluate`` or ``evaluate_stratified`` to
collect one deterministic witness per retained fact. Positive premises stay
in rule-body order; comparisons record the grounded operands that held.
Stratified witnesses include every successful absence check and its explicit
closed-predicate scope. Aggregate witnesses record the operation, target,
group, result and contributing logical facts.

.. code-block:: python

   from gestaltdb.reasoning.model import ReasoningLimits, Variable
   from gestaltdb.reasoning.terms import Literal
   from gestaltdb.reasoning.typed import (
       Comparison, GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact,
   )

   x, age = Variable("x"), Variable("age")
   rule = GenericRule("eligible", [TypedAtom("age", [x, age])],
                      [Comparison(">=", age, Literal("integer", 18))],
                      TypedAtom("eligible", [x]), negated=[TypedAtom("banned", [x])])
   program = GenericProgram([rule], closed=["banned"])
   facts = [TypedFact("age", ["alice", Literal("integer", 30)])]
   run = GenericEngine().evaluate_stratified(
       program, facts, limits=ReasoningLimits(), collect_proofs=True)
   conclusion = TypedFact("eligible", ["alice"])
   explanation = run.proofs.explain(conclusion, max_nodes=10)
   assert not explanation.truncated
   witness = next(node for node in explanation.nodes if node.fact == conclusion)
   assert witness.rule.rule_id == "eligible"
   assert witness.premises == tuple(facts)
   assert witness.comparisons[0].holds({})
   assert witness.absence[0].scope == ("banned",)
   assert run.proofs.is_current_for(program, facts)
   # After adding a disqualifier, the old trace is historical, not current.
   changed = [*facts, TypedFact("banned", ["alice"])]
   assert not run.proofs.is_current_for(program, changed)

``max_proof_nodes`` and ``max_proof_edges`` bound retained witness records;
exhaustion sets ``trace.truncated`` without removing logical conclusions.
``explain(..., max_nodes=...)`` has a separate reachable-node budget. A fact
with no retained witness returns an empty, explicitly truncated explanation.
These budgets are not limits on total evaluator memory or input size.

Traces are immutable and bound to the program digest and input-fact horizon.
Call ``is_current_for`` before consuming them as current results, and
reevaluate after rule changes, support retraction or new disqualifiers. Old
traces remain historical explanations; they are not automatically maintained
all-support indexes. Aggregate operands identify contributing facts, not a
row-by-row arithmetic certificate or exact historical database versions.

Manually constructed witnesses fail explicitly on omitted, substituted or
ungrounded comparisons, missing/duplicate absence checks, incorrect rule
definitions or closed scopes, and inconsistent aggregate metadata. Result
validation also rejects absence contradicted by the full closure, even when
the retained trace is truncated. These are structural consistency checks,
not independent source authentication, complete closure certification or
independent recomputation of aggregate arithmetic. Adapters that declare
closure-only execution remain closure-only; core proof collection does not
silently extend their contracts.

Measured equivalence and cost
----------------------------------------

The generic engine reuses the proven delta algorithm — per-occurrence
pivots, smallest-bound-bucket ordering, set semantics — extended with
positional indexes and incremental filter pruning. Forty generated
programs (mixed arities, repeated atoms, constants, filters, recursion)
match an independent grounding oracle exactly, and repeated runs are
deterministic.

On the RE-01 analytical workloads translated 1:1 to binary typed rules
(entities stay entities; no literals or filters), both engines reach
identical closures in identical iteration counts: hierarchy N=256 completes
1280 facts in 5 rounds, recursive N=1024 completes 9227 facts in 6 rounds,
dense N=128 completes 8128 facts in 8 rounds. The generic engine costs
roughly 1.5–1.7x wall time there (3.7→5.6ms, 86→144ms, 535→918ms medians)
for positional indexing with no filter work to amortize it; see
``benchmarks/results/reasoning-re07-typed.json``. Those original closure-only
measurements do not measure the later optional typed proof collection.
The generic evaluator does not offer automatic incremental proof maintenance;
recursive and lattice aggregation stay explicitly deferred.

Scoped negation lives in :doc:`reasoning-negation`: explicit closed predicate
sets, stratified anti-joins and scope-carrying absence witnesses.
