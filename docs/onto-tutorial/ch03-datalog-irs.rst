Datalog IRs: terms, rules, plans, batches
==========================================

Core values (``reasoning/model.py``): ``Variable("x")`` (a plain ``"?x"``
string is a constant), ``Fact(subject, predicate, object)`` (ground binary
entity triple), ``Atom`` (predicate constant, subject/object terms),
``Rule(rule_id, body, head)`` (safe: every head variable occurs in the body),
``Program`` + ``ReasoningLimits``. The standalone ``DatalogEngine``
(``reasoning/engine.py``) does semi-naive fixpoint; witnesses come from
``reasoning/proofs.py``.

Execution IRs (roadmap #156, RE-01..RE-06):

* ``reasoning/relational.py`` — n-ary-ready relational vocabulary and
  ``explain_plan`` (structural only, not executable beyond binary entities).
* ``reasoning/batches.py`` — compact ``uint64`` symbol dictionaries + immutable
  column batches; optional Arrow exchange in ``reasoning/adapters/arrow.py``
  (copies, canonicalizes, never zero-copy-promised).
* ``reasoning/plans.py`` — positive-predicate SCC schedules, delta sites,
  optional witness/telemetry (off by default).
* ``reasoning/adapters/polars.py`` — experimental closure-only Polars prototype;
  ``reasoning/executors.py`` keeps indexed Python the default and routes proofs
  to indexed engines.

.. math::

   T_P(I) = I \cup \{\, h\sigma \mid (B \Rightarrow h) \in P,\;
   B\sigma \subseteq I \,\} \qquad
   \text{lfp}(T_P) = \bigcup_{n \ge 0} T_P^n(\emptyset)

Safety + set semantics give termination on finite inputs. Demand evaluation
(``reasoning/demand.py``, magic-set specialization) answers bound goals without
full materialization; typed literals live in ``reasoning/terms.py`` /
``reasoning/typed.py``.

**Intuition.** Semi-naive evaluation is "apply every rule to every new fact,
then repeat until a round adds nothing". The *delta* — only facts learned in
the last round — is what makes it semi-naive instead of re-deriving the world
each time. SCC plans (``reasoning/plans.py``) group mutually recursive rules
so each group reaches its own fixpoint before downstream rules fire; demand
evaluation works backwards from the question ("who is a ``Person``?") and
only explores rules that could produce an answer.

Theory and further reading
---------------------------

- `Datalog <https://en.wikipedia.org/wiki/Datalog>`_ fixes the language:
  function-free Horn rules with set semantics. `Prolog
  <https://en.wikipedia.org/wiki/Prolog>`_ relaxes it toward a full
  programming language (compound terms, cut, negation as failure) at the cost
  of declarative evaluation order — which is why gestaltdb compiles
  ontologies to Datalog, not Prolog.
- `Least fixed point <https://en.wikipedia.org/wiki/Least_fixed_point>`_
  and the `Knaster–Tarski theorem
  <https://en.wikipedia.org/wiki/Knaster%E2%80%93Tarski_theorem>`_ justify
  the ``lfp(T_P)`` equation above: ``T_P`` is monotone over the finite
  powerset lattice of ground facts, so iterating from ``∅`` converges to the
  least model. Finiteness (no function symbols) is what keeps the iteration
  from running forever.
- `Magic sets <https://en.wikipedia.org/wiki/Magic_sets>`_ is the rewriting
  behind demand evaluation: bound arguments propagate sideways so the
  fixpoint only explores facts relevant to the query, with full-closure
  parity by construction.
- `Stratified negation
  <https://en.wikipedia.org/wiki/Stratified_negation>`_ explains the scoping
  discipline of ``closed`` declarations — negation applies per completed
  stratum, never inside its own recursive loop.
