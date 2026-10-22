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
  (imports copy and canonicalize; little-endian exports can share immutable
  column payloads, but wrappers allocate and consumers may copy).
* ``reasoning/plans.py`` — positive-predicate SCC schedules, delta sites,
  optional witness/telemetry (off by default).
* ``reasoning/adapters/polars.py`` — experimental closure-only Polars prototype;
  ``reasoning/executors.py`` keeps indexed Python the default and routes proofs
  to indexed engines.

.. math::

   I_0 = F, \qquad
   I_{n+1} = I_n \cup \{\, h\sigma \mid (B \Rightarrow h) \in P,\;
   B\sigma \subseteq I_n \,\}, \qquad
   C_P(F) = \bigcup_{n \ge 0} I_n

Here ``F`` is the input fact set, ``P`` the rules, and ``σ`` a substitution
of variables by constants. Starting from the empty set without injecting
``F`` would lose the assertions and derive nothing for our example.
With a finite constant vocabulary, fixed predicate arities, safe rules, and
no value-generating functions, only finitely many ground facts can be added.
Set semantics therefore gives termination for this positive fragment.
Demand evaluation
(``reasoning/demand.py``, magic-set specialization) answers bound goals without
full materialization; typed literals live in ``reasoning/terms.py`` /
``reasoning/typed.py``.

**Intuition.** Semi-naive evaluation considers rule-body joins in which at
least one body occurrence comes from the latest delta; other occurrences
can match the accumulated relation. It is not enough to join only new facts
with each other. Subtracting the existing closure produces the next delta.
For chapter 2: round 0 contains ``worksFor(alice, acme)``, round 1 adds
``type(alice, Researcher)``, round 2 adds ``type(alice, Person)``, and the next
round adds nothing. SCC plans (``reasoning/plans.py``) group mutually recursive rules
so each group reaches its own fixpoint before downstream rules fire; demand
evaluation works backwards from the question ("who is a ``Person``?") and
only explores rules that could produce an answer.

Theory and further reading
---------------------------

- `Datalog <https://en.wikipedia.org/wiki/Datalog>`_ fixes the language:
  function-free Horn rules with set semantics. `Prolog
  <https://en.wikipedia.org/wiki/Prolog>`_ relaxes it toward a full
  programming language. Its usual left-to-right, depth-first evaluation can
  diverge even for a logically valid recursive specification; cuts and negation
  as failure also need operational care. This tutorial instead uses bottom-up
  finite set semantics, not a Prolog execution model.
- `Least fixed point <https://en.wikipedia.org/wiki/Least_fixed_point>`_
  and the `Knaster–Tarski theorem
  <https://en.wikipedia.org/wiki/Knaster%E2%80%93Tarski_theorem>`_ justify
  the closure equation above: the inflationary consequence operator is
  monotone over the finite powerset of ground facts. Iterating from ``F``
  reaches the least model containing the assertions. Monotonicity guarantees
  a least fixed point; finiteness additionally makes this iteration terminate.
- `Magic sets <https://en.wikipedia.org/wiki/Magic_sets>`_ is the rewriting
  behind demand evaluation: bound arguments propagate sideways so the
  fixpoint only explores facts relevant to the query, with full-closure
  answer parity with full closure for the supported fragment. This does not
  promise lower work for every query or selective reads from the KV backend.
- `Stratified negation
  <https://en.wikipedia.org/wiki/Stratified_negation>`_ explains the scoping
  discipline of ``closed`` declarations — negation applies per completed
  stratum, never inside its own recursive loop.
