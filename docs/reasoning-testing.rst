Reasoning engine testing milestone
==================================

The 0.13.0rc1 candidate makes the reasoning implementation available for opt-in
user testing. Until its tagged prerelease is published, the commands below name
the intended release, not a claim of availability. This milestone does not
complete the entire roadmap or promise production-scale performance.

Install and isolate
-------------------

Use Python 3.10–3.13 and a fresh environment. The standalone core needs no
database backend, solver, JVM, Arrow or Polars:

.. code-block:: bash

   python -m venv .venv-reasoning
   . .venv-reasoning/bin/activate
   python -m pip install 'gestaltdb==0.13.0rc1'

For explicit persisted graph/claim workflows, select a backend, for example
``python -m pip install 'gestaltdb[lmdb]==0.13.0rc1'``. JSON and Pickle are built
in; optional binary serializers and backends are independent extras. Do not
install every benchmark client just to use the reasoner. See :doc:`installation`.

First standalone closure
------------------------

.. code-block:: python

   from gestaltdb.reasoning import (
       GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact,
   )
   from gestaltdb.reasoning.model import ReasoningLimits, Variable

   x, y = Variable("x"), Variable("y")
   program = GenericProgram([
       GenericRule("member-is-person", [TypedAtom("memberOf", [x, y])], [],
                   TypedAtom("person", [x])),
   ])
   result = GenericEngine().evaluate(
       program, [TypedFact("memberOf", ["alice", "research"])],
       limits=ReasoningLimits(),
   )
   assert TypedFact("person", ["alice"]) in result.facts

The result is a finite closure, not a write to a database. In-memory facts,
compiled ontologies, graph projections and temporal claim adapters are distinct
interfaces. Select the adapter explicitly rather than expecting Cypher to
materialize inferred data automatically.

Supported paths and deliberate boundaries
-----------------------------------------

- **Default execution:** indexed Python; reusable SCC plans are opt-in.
  :doc:`reasoning-executors` describes proof modes, cancellation and logical
  work limits. Hard wall-time/RSS limits need process isolation.
- **Typed rules:** strict literals, n-ary positive rules, scoped stratified
  absence and aggregates, with combination-specific admission rules. See
  :doc:`reasoning-typed` and :doc:`reasoning-negation`.
- **Selective answers:** positive-fragment demand specialization has full-closure
  answer parity. Unsupported negation/aggregate combinations fail explicitly;
  use complete evaluation and filter instead. See :doc:`reasoning-demand`.
- **Ontology, validation and equality:** named subsets, not full OWL RL/DL or
  SHACL. Ground denials are not signed rule premises, absence is not a denial,
  and equality never physically merges graph nodes. See :doc:`reasoning-profiles`,
  :doc:`reasoning-validation` and :doc:`reasoning-equality`.
- **Persistent integration:** pinned extraction, explicit generation admission,
  staged publication, exact assertion ledgers and scoped incremental sessions.
  Supported updates must match fresh recomputation; unsupported cases reject or
  require an explicit rebuild. See :doc:`reasoning-extraction`,
  :doc:`reasoning-generations`, :doc:`reasoning-sessions` and
  :doc:`reasoning-inferred-consumers`. The common identity contract does not
  silently retrofit every older API or make executors interchangeable.
- **Time ranges:** positive typed closure over change segments preserves gaps
  and evidence. Range negation/aggregates are not supported; equality is scoped
  per segment. See :doc:`reasoning-conformance` and :doc:`reasoning-ranges`.
- **Experimental/optional:** Polars is closure-only and never replaces the
  default or silently drops proofs. Arrow exchange is not universally zero-copy.
  Existential/EL experiments and context extensions are gated, not a full
  existential, probabilistic or cross-world reasoner. Native scan capabilities
  require the documented runtime version; they are not a native inference
  engine. No spill/out-of-core executor is promised.

Compatibility and safe testing
------------------------------

Keep a backup and test on a disposable copy before opening a valuable database
with a prerelease. The representative release fixture reopens LMDB/JSON graph
records and claims created by 0.12.0, then verifies reasoning transitions and
reopen parity. This does not certify every historical serializer/backend or
guarantee downgrade after new writes. Do not share an open directory between
different GestaltDB versions.

No implicit physical node merge, backend fallback or history migration is part
of enabling reasoning. Honor backend snapshot/publication capabilities; in
particular LevelDB does not expose a unified mutable graph snapshot. Keep
experimental outputs and managed inferred worlds separate from base claims.
Validate provenance and scopes when reusing plans, caches or sessions.

What to test and report
-----------------------

Start with installation and a small known closure, then test your representative
rules with duplicate assertions, corrections, final-support retractions and
rule changes. Compare incremental/session results with a fresh evaluation.
For temporal workflows, compare point answers at boundaries against range
answers, including gaps and overlapping evidence.

Report Python/GestaltDB/backend/serializer versions, selected profile/executor,
limits, a small reproducible rule/input set, expected versus actual answers,
and whether a fresh rebuild agrees. Include stage timings and graph shape for
performance reports, not unsupported universal speedup conclusions. Remove
credentials and private graph data from reports. Use the repository's GitHub
issues; release readiness is tracked in #156 and review follow-through in #200.

Remaining optimizer/native benchmark work, broader integration and research
extensions remain tracked. They do not postpone this testing milestone unless
they expose incorrect results, unsafe publication or compatibility regressions
in an advertised path. Stable promotion follows user feedback and fixes.
