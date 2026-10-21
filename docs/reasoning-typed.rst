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
----------------------------------------

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
``benchmarks/results/reasoning-re07-typed.json``. The generic engine
offers no witnesses and no incremental maintenance; those remain with the
binary engine and its single-witness traces. Recursive and lattice
aggregation stay explicitly deferred.
