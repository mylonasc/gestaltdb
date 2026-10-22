:orphan:

Bounded EL feasibility: consequence-based classification
============================================================

``gestaltdb.reasoning.el`` is a dedicated experimental profile
(``el-experimental-v0``), not a production classifier. It closes
subsumption between named concepts with completion rules — no fresh
individuals, no anonymous nesting, no full OWL DL, no JVM. Every entry
point demands the exact profile string and fails closed otherwise.

.. code-block:: python

   from gestaltdb.reasoning import EL_PROFILE, check_proof, classify

   specs = [("sub", "Cat", "Mammal"), ("some-right", "Mammal", "hasParent", "Mammal")]
   out = classify(specs, profile=EL_PROFILE)
   assert out.holds("Cat", "Mammal")
   assert check_proof(specs, out, profile=EL_PROFILE) == len(out.derivations)

Witnesses, termination, oracles
----------------------------------------

Classification is evaluated separately from generating witnesses: each
non-trivial subsumption carries one derivation with the completion rule
and exact tagged grounds (``sub``, ``some``, ``axiom``), and
``check_proof`` replays every derivation in dependency order through an
independent checker. Termination is structural (finite vocabulary,
monotone growth) with strict derivation and round budgets on top.
``holds_in_all_models`` is the independent model-theoretic oracle:
brute-force finite interpretations that completion must agree with on
satisfiable inputs — unsatisfiable sizes return no verdict instead of a
vacuous one.

Explicitly deferred
----------------------------------------

Full OWL DL stays deferred research: no JVM adapter, no dependency of
earlier phases on it, dedicated experimental profiles only. Anonymous
constructions are rejected rather than silently approximated, and the
profile gate keeps experimental semantics out of every production path.
