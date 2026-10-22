:orphan:

Scoped closed-world negation
============================================================

Stratified negation evaluates absence against completed lower strata — never
against facts still being derived. ``GenericRule.negated`` holds absence atoms
over constant predicates; a program-level ``closed`` tuple declares which
predicates are complete, and ``GenericProgram`` orders positive components so
every anti-join reads finished state. Undeclared predicates, unbound negation
variables and negative cycles fail at construction, not at query time.

.. code-block:: python

   from gestaltdb.reasoning import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
   from gestaltdb.reasoning.model import ReasoningLimits, Variable

   x = Variable("x")
   eligible = GenericRule("eligible", [TypedAtom("candidate", [x])], [],
                          TypedAtom("eligible", [x]), [TypedAtom("disqualified", [x])])
   run = GenericEngine().evaluate_stratified(
       GenericProgram([eligible], closed=["disqualified"]),
       [TypedFact("candidate", ["a"]), TypedFact("candidate", ["b"]),
        TypedFact("disqualified", ["b"])],
       limits=ReasoningLimits(), collect_negation_trace=True,
   )
   assert TypedFact("eligible", ["a"]) in run.result
   assert TypedFact("eligible", ["b"]) not in run.result
   assert run.witnesses[0].predicate == "disqualified"
   assert run.witnesses[0].scope == ("disqualified",)

Closed scopes, not closed worlds
----------------------------------------

Negation is available only inside an explicit ``closed`` declaration listing
the predicates treated as complete for this evaluation. Anything undeclared
stays open-world: querying its absence raises instead of silently assuming
empty. The declaration covers one evaluation horizon — the exact pinned input
fact set, whose digest travels in every blocker witness — and never leaks
across contexts, snapshots or runs. Swapping horizons requires a fresh,
explicit ``closed`` binding and re-evaluation.

Range restriction keeps negation decidable: every negated variable must occur
in the positive relational body, heads may only use positive-bound variables,
and negated predicates must be constant (no predicate variables under
negation). Filters combine freely with negation; aggregates evaluate after
the stratified positive fixpoint over the completed closure.

Strata, cycles and witnesses
----------------------------------------

Positive SCCs evaluate in dependency order; components with no rules still
occupy schedule positions with zero rounds. A negative edge inside one
component, or rankings that cannot order negative edges strictly downward,
fail closed as unstratifiable — there is no fallback to non-monotonic
semantics, and recursive negation is not attempted.

``StratifiedRun`` reports the component schedule, per-component rounds and
``iterations`` as total executed component delta rounds (the aggregate
stratum, when present, adds one round, exactly as in plain evaluation).
With ``collect_negation_trace=True``, each blocked binding yields a
``NegationWitness`` for its first present negated atom, carrying the rule,
body position, predicate, ground args, closed scope and horizon digest.
Its explicit ``outcome="present"`` identifies a positive blocking fact,
not an absent fact or a fabricated denial. Successful absence checks enabling
conclusions are not recorded. This is a *why-not* diagnostic, not a proof of
derived conclusions or an absence-dependency index for maintenance.
The trace is bounded by ``max_proof_nodes`` and sets ``truncated`` instead of
failing; truncation never changes logical closure.

Limits, cancellation and parity
----------------------------------------

Input, rule, iteration, fact and candidate budgets keep their positive-engine
meanings; ``max_iterations`` counts total component delta rounds. Cooperative
``cancel`` is honored between rounds and input chunks, raising
``ReasoningCancelledError`` without partial results. Plain ``evaluate``
rejects any program mentioning negation or a non-empty closed scope, so the
default binary-entity behavior is byte-for-byte unchanged.

Stratified closures match an independent stratum-by-stratum grounding oracle
exactly, and deterministic repeats agree. Base insert/delete recomputation
tests confirm updated horizons: adding a disqualifier retracts the conclusion
on recomputation, and removing the final support for a derived fact retracts
it as well. Incremental invalidation across updates stays in RE-16; here
recomputation is the correct explicit fallback.

Coordinated with `#125 <https://github.com/mylonasc/gestaltdb/issues/125>`_
(the broader negation proposal): this slice delivers the scoped stratified
core with exact anti-joins and scope-carrying blocker witnesses; that issue remains
the umbrella for any wider negation work.

GraphDB rule catalogs
---------------------

The temporal claim rule API supports the same closed-world contract through
``create_rule(..., not_when=[...], closed_world=[...])``. ``run_rules`` and
``maintain_truth`` stratify the active catalog, anti-join absence only against
completed lower strata, and persist grouped absence evidence without creating
negative claims. This is separate from the generic typed engine above: it
operates on binary entity-object claims, validity intervals and world-scoped
matches. See :doc:`temporal-versioning`.
