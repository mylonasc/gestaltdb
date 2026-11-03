:orphan:

Bounded proofs, export interchange and checkpoints
============================================================

What an explanation does—and does not—show
------------------------------------------

A :term:`witness` is one recorded route from premises to a conclusion: “this
rule used these facts to derive that fact”. Multiple independent :term:`supports
<support>` may justify the same conclusion. The retained route is therefore not
an all-support index or an independent certificate that the inputs are true.
See :doc:`reasoning-introduction` for a runnable example and generated witness
figure, and :doc:`glossary` for facts, rules, premises, closure and DAGs.

``gestaltdb.reasoning.explanations`` exposes bounded minimal/top-k witness
selection, export and generation checkpoints. “Minimal” here concerns a bounded
one-step selection contract, not globally smallest deep derivation trees.
``export_proof`` records the witness kind and supplied alternative supports;
it does not turn scores or confidence metadata into logical probabilities.

**Illustrative fragment:** ``fact`` and ``supports`` below come from a completed
supported maintenance/evaluation workflow; this is not standalone setup.

.. code-block:: python

   from gestaltdb.reasoning import export_proof, minimal_witness

   witness = minimal_witness(fact, supports)  # fewest premises, deterministic
   document = export_proof(fact, supports, witness)  # witness_kind + all_supports

Checkpoints and limits
----------------------------------------

``checkpoint_closure`` binds program digest, fact horizon, support evidence and
index digest together in a saved generation. This consistency binding does not
by itself authenticate arbitrary input facts against a database source.
``resume_checkpoint`` rebuilds and re-validates instead of trusting
stored derivations; incompatible generations fail closed and incomplete
checkpoints are discarded explicitly, never silently repaired. Deep-tree
minimality is combinatorial and explicitly deferred: the contract covers
bounded one-step witnesses (``max_proof_bytes`` fails closed instead of
truncating), and exports state their complexity honestly.

Typed derivation tracking
----------------------------------------

``GenericEngine.evaluate(..., collect_proofs=True)`` records one
``TypedFactWitness`` per closure fact: the exact rule, positive premises
in body order, grounded comparisons that held, successful absence checks
naming their closed scope (stratified only), and aggregate operations with
contributing facts. ``evaluate_stratified(..., collect_proofs=True)``
records the same. ``result.proofs.explain(fact)`` walks the bounded
derivation DAG; unknown facts explain to an explicitly truncated empty
result — never an invented derivation. Proof budgets truncate the trace,
never the closure. A trace is bound to its program digest and input
horizon: ``trace.is_current_for(program, facts)`` reports whether it still
describes the live closure, so retracted support is re-explained after
retract/rematerialize rather than misattributed.

**Illustrative fragment:** ``program``, ``facts`` and ``derived`` are a typed
program, its inputs and a selected conclusion. Use public explanation methods;
internal trace dictionaries are not application APIs.

.. code-block:: python

   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.typed import GenericEngine

   proven = GenericEngine().evaluate(program, facts, limits=ReasoningLimits(), collect_proofs=True)
   explanation = proven.proofs.explain(derived)
   assert not explanation.truncated

Measured cost
----------------------------------------

On a 30-link chain (495 facts, 465 supports): witness selection plus
export in ~0.1ms (525 bytes), checkpoint in ~5.3ms, resume with full
re-validation in ~7.0ms. The export vocabulary records evidence for explanation
consumers; it does not independently verify proofs.
