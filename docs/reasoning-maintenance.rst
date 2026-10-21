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
incremental repair. Deletion, final-support loss and rule-change
invalidation belong to RE-16; this slice covers insertion only, with
budgets (``max_facts``, ``max_candidate_rows``, ``max_supports``,
``max_iterations``) and cooperative cancellation enforced throughout.
