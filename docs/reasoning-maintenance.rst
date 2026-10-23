:orphan:

Bounded maintenance provenance and insertion propagation
============================================================

``gestaltdb.reasoning.maintenance`` tracks finite independent supports
separately from bounded witness DAGs: one ``Support`` per (fact, rule,
premise-tuple) derivation, indexed forward and backward in a
``DependencyIndex`` bound to an exact program digest and fact horizon.
``propagate_insertions`` extends a materialized closure by changed
predicates only, preserving every alternative source with duplicate
handling under support/output/work budgets.

.. code-block:: python

   from gestaltdb.reasoning import propagate_insertions
   from gestaltdb.reasoning.model import ReasoningLimits

   out = propagate_insertions(program, list(closure), [new_fact], limits=ReasoningLimits())
   assert set(out.facts) >= set(closure) | {new_fact}
   assert out.index.derived_from(new_fact)  # reverse map, no path enumeration

Supports are not proofs: they record which rule and premises produced a
fact, never a recursive derivation tree. The reverse map answers "what was
derived from this premise" without enumerating paths. Changed-predicate
analysis restricts rule firing to downstream consequences of inserted
predicates; insertions of unrelated predicates only extend the closure.

Stale generations fail closed: a ``DependencyIndex`` whose program digest
or horizon no longer matches demands explicit rematerialization instead of
incremental repair. Changed rules likewise rematerialize under the new
digest; the old index is never repaired across a ruleset change.

Recursive deletion (DRed)
----------------------------------------

``retract_facts`` implements DRed-style overdeletion with support-aware
filtering plus rederivation, so final-support loss in recursive SCCs is
exact — bare support counting would be unsound on cycles. Overdeletion
follows the reverse support map transitively (a safe over-approximation);
rederivation by full evaluation on the survivors reproduces exactly the
facts with remaining support. Duplicate evidence keeps a fact with its
remaining supports; complete fresh supports are discovered for rederived
facts, keeping the rebound index exact for future deletions. Changed
programs, negation, aggregates, closed scopes and equality splits fail
closed and demand explicit ``rematerialize``; B/F stays a documented
conditional alternative, not a second engine.

Measured crossover: on two independent 12-link chains (180-fact closure),
retracting the head edge of one chain costs work 13 (12 overdeleted
dependents plus rederivation) against ~48,700 for full rematerialization
with complete support discovery — incremental deletion wins while the
change is local. Dense or global retractions converge toward rebuild cost;
complete support discovery is cubic in chain length, so measure before
assuming an advantage on deep recursions.
