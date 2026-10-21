:orphan:

Goal-directed queries and demand specialization
============================================================

``gestaltdb.reasoning.demand.answer`` evaluates an explicit bound goal with a
magic-set specialized program on the existing generic engine. Specialization
is deterministic, identity-bound and cached; answers carry full-closure
parity by construction. Anything outside the supported positive fragment —
negation, aggregates, closed scopes, predicate variables — fails closed
instead of silently falling back, with the complete strategy documented.

.. code-block:: python

   from gestaltdb.reasoning import BoundGoal, GenericProgram, GenericRule, TypedAtom, TypedFact, answer
   from gestaltdb.reasoning.model import ReasoningLimits, Variable

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([
       GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
       GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                   TypedAtom("reach", [x, z])),
   ])
   facts = [TypedFact("edge", ["a", "b"]), TypedFact("edge", ["b", "c"]),
            TypedFact("edge", ["u", "v"])]
   out = answer(program, facts, BoundGoal("reach", ["a", y]), limits=ReasoningLimits())
   assert {fact.values for fact in out.facts} == {("a", "b"), ("a", "c")}

How it works
----------------------------------------

``specialize`` adorns the program top-down from the goal: bound positions
propagate left-to-right through each body (the sideways information passing
strategy), generating one magic predicate per predicate/adornment pair plus a
seed fact. Rewritten rules carry a magic guard; magic rules derive exactly
the bindings the query can consume. Rules unreachable from the goal are
dropped — they cannot contribute to the answer. The specialized program keeps
full closure parity: every answer unifies with the goal, and every goal
instance derivable by exhaustive evaluation is derived.

Caching is safe by construction: ``SpecializationCache`` keys programs by
their structural digest plus the goal, so any rule or goal change
re-specializes instead of reusing a stale plan. The default engine stays the
executor; no new runtime is introduced.

Scope and unsupported combinations
----------------------------------------

Supported: positive rules with filters, constants, literals and repeated
variables over the typed fragment. Rejected with an explicit error, never a
silent fallback: negation (use stratified evaluation, then filter),
aggregates (use the stratified aggregate path, then filter), closed scopes,
predicate variables (expand first), inconsistent predicate arities and
goal-arity mismatches. These combinations keep the documented complete
strategy: run the full closure, then match the goal.

Measured selectivity
----------------------------------------

Demand evaluation shines when the goal touches a slice of the data: 200
independent 30-link chains with a query at the base of one chain derive 30
answers instead of the 99,000-fact full closure (~33ms vs ~912ms here), because
unreachable rules are dropped before the fixpoint starts. When the goal
covers the whole graph the specialized program costs about the same as full
closure plus the one-time specialization pass (memoized by
``SpecializationCache``) — selectivity, not a faster engine, is the win.
Dense recursive workloads where every fact is reachable show no advantage;
measure the goal before assuming one.
