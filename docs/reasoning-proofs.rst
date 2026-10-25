:orphan:

Bounded proofs, export interchange and checkpoints
============================================================

``gestaltdb.reasoning.explanations`` closes the roadmap: bounded
minimal/top-k proof contracts, a stable model-output interchange and
authenticated generation checkpoints. A proof is a chosen witness, never
the whole support set — ``export_proof`` labels the witness kind
alongside the complete alternatives so consumers never confuse the two,
and carries no scores or confidences (ranked probabilistic provenance
stays a separately gated calculus).

.. code-block:: python

   from gestaltdb.reasoning import export_proof, minimal_witness

   witness = minimal_witness(fact, supports)  # fewest premises, deterministic
   document = export_proof(fact, supports, witness)  # witness_kind + all_supports

Checkpoints and limits
----------------------------------------

``checkpoint_closure`` persists only authenticated generations — program
digest, fact horizon, support evidence and index digest bound together.
``resume_checkpoint`` rebuilds and re-validates instead of trusting
stored derivations; incompatible generations fail closed and incomplete
checkpoints are discarded explicitly, never silently repaired. Deep-tree
minimality is combinatorial and explicitly deferred: the contract covers
bounded one-step witnesses (``max_proof_bytes`` fails closed instead of
truncating), and exports state their complexity honestly.

Typed derivation tracking (#392)
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

.. code-block:: python

   from gestaltdb.reasoning import ReasoningLimits
   from gestaltdb.reasoning.typed import GenericEngine

   proven = GenericEngine().evaluate(program, facts, limits=ReasoningLimits(), collect_proofs=True)
   node = proven.proofs._by_fact[derived]
   assert node.rule.rule_id and node.premises
   explanation = proven.proofs.explain(derived)
   assert not explanation.truncated

Measured cost
----------------------------------------

On a 30-link chain (495 facts, 465 supports): witness selection plus
export in ~0.1ms (525 bytes), checkpoint in ~5.3ms, resume with full
re-validation in ~7.0ms. The export vocabulary is the interchange that
native proof trees (#126) consume — generation without the engine,
never proof without the evidence.
