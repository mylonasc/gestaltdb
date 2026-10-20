:orphan:

Reusable indexed reasoning plans
============================================================

``gestaltdb.reasoning.plans`` adds an explicit standard-library-only prepared
execution path. It compiles positive predicate dependencies into deterministic
strongly connected components (SCCs), retains original rule/body positions and
reuses the existing indexed matcher and bounded witness collector. The default
``DatalogEngine`` and ``Program.digest`` remain unchanged in public behavior.
No new rule syntax, storage dependency, automatic cache or backend selection is
introduced.

.. code-block:: python

   from gestaltdb.reasoning import Atom, Fact, Program, ReasoningLimits, Rule, Variable
   from gestaltdb.reasoning.plans import PlannedDatalogEngine, PreparedProgram
   from gestaltdb.reasoning.protocols import InputContext
   from gestaltdb.reasoning.relational import CompilationIdentity

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = Program([Rule("t", [Atom(x, "p", y), Atom(y, "p", z)], Atom(x, "p", z))])
   identity = CompilationIdentity(
       program.digest, InputContext("in-memory-example", "default", "none", "all", "identity"),
       "external-strings-v1", "positive-binary-entity-v1", "indexed-scc-v1",
   )
   plan = PreparedProgram(program, identity)
   assert plan.components[0].recursive
   assert plan.explain_plan()["kind"] == "prepared-only"
   run = PlannedDatalogEngine().evaluate(
       plan, [Fact("a", "p", "b"), Fact("b", "p", "c")],
       identity=identity, limits=ReasoningLimits(),
       collect_proofs=True, collect_telemetry=True,
   )
   conclusion = Fact("a", "p", "c")
   assert conclusion in run.result
   assert not run.result.explain(conclusion).truncated
   assert run.candidate_rows == sum(site.actual_rows for site in run.operators)
   assert run.explain_plan()["execution"]["telemetry_enabled"]

Preparation and compatibility
----------------------------------------

``PreparedProgram`` is immutable and holds no input facts, source handles or mutable
evaluation state. Compilation is iterative, including SCC traversal and dependency
ordering, so long class/property hierarchies do not require Python recursion.
Canonical rule order, component tie-breaking and original occurrence positions
make plan identities repeatable. Identical body atoms share one delta-pivot site,
but all original body positions remain present for matching/proofs.
SCCs are over predicates, not entities or ontology class IDs. Class-membership
rules sharing the same type predicate can therefore belong to one SCC even when
the named class hierarchy is acyclic; no ontology-specific normalization shortcut
is implied.

``max_rules`` (default 10,000) and ``max_atoms`` (default 100,000, including heads)
bound plan construction. These are counts, not exact allocation/time guarantees.
The existing safe positive binary entity profile is the only executable profile;
arbitrary-arity/literal/negative/aggregate plans are not implied by this API.

The complete ``CompilationIdentity`` must match before input iteration begins:
program, context/horizon, world, ontology revision, mapping, source policy,
dictionary generation, profile and executor version. Strings are compatibility
keys, not source authentication: adapters still verify the actual pinned input.
This executor indexes external named strings; ``external-strings-v1`` labels that
representation in the example, not a compact uint64 implementation. Cache no
context-bound input/results across changed identities without explicit validation.

Plans cache dependency/SCC schedules, dispatch sites and access-path possibilities.
They do not cache fact indexes, bindings, results or witnesses. Reusing a plan
always builds fresh invocation-local state. ``PlannedRun`` is an explicit result
wrapper, not a transparent replacement for the existing adapter/Evaluator protocol.
No claim extraction, temporal history reads or publication occurs here.

Scheduling, indexes and limits
----------------------------------------

Complete predecessor SCCs before evaluating dependent rules. Each component's first
pass uses all stable facts as its seed delta; subsequent passes use only newly
derived facts. Writes to the in-memory closure become visible at round boundaries,
not halfway through a match. This computes the same finite positive least fixpoint
as the default evaluator; independent grounding/randomized tests verify parity.

The available physical paths are predicate scan, subject/object indexed nested
loops and exact hash membership. Both endpoint indexes are maintained in memory;
this is runtime access-path selection, not persistent-index creation or index
pruning. Remaining atoms are selected by current bound-bucket size, with ties
retaining body order. This is a **heuristic**, not a calibrated cost optimizer.
Sort-merge/hash-join operators, SCC-parallel execution and adaptive scheduling are
not enabled. Unknown strategy names fail explicitly instead of quietly falling
back. They remain benchmark-gated future executor work.

Input/fact/rule/candidate/proof limits retain their existing meanings. The iteration
unit differs explicitly: ``max_iterations`` and ``run.result.iterations`` count
the **total executed component delta rounds**, including terminal active rounds
that derive nothing. They are not the default evaluator's global synchronous
rounds. This can exhaust the same numerical limit earlier across independent
components; the bound is not silently relaxed. ``component_rounds`` explains the
total. Candidate rows still count attempted indexed unifications, unlike the
separate Polars prototype's columnar work accounting.

Native witness collection remains optional and bounded. Join reordering retains
exact original rule/premise positions. Changing SCC scheduling can select a
different valid single witness than the default engine, but repeated runs with
the same inputs/plan are deterministic. Proof truncation never truncates closure;
inference failures return no successful partial run and publish nothing. A witness
is still not an all-support maintenance index or arbitrary-interval justification.

Opt-in execution inspection
----------------------------------------

``collect_telemetry=False`` is the default: detailed per-site observers and relation
statistics are not built. Global attempted-candidate counts and component rounds
remain available. With telemetry enabled, ``run.explain_plan()`` returns detached
JSON-safe data including:

- Rule IDs, original body positions, compiled delta sites and possible index paths.
- Chosen stable/delta index paths, lookup counts, estimated bucket rows, actual
  attempted rows and successful unifications, aggregated by site/path/source.
- Deduplicated first-emission counts per rule (not all-support justification counts).
- Input/output relation size, endpoint distinct counts and largest endpoint bucket.

Estimates are sums of **current selected bucket cardinalities**, not guesses of
final output size or elapsed time. Unprofiled/static body estimates remain ``None``;
profiled but unvisited sites have zero measured rows. Aggregates are bounded by
program body sites/access paths, not one event per candidate. This is not a full
per-binding chronological trace, native memory profiler, histogram catalog or
storage-I/O timer. The core reads no clock; outer benchmarks measure wall time/RSS.

Measured costs and reuse
----------------------------------------

The existing benchmark runner accepts explicit ``planned``, ``planned-proofs``,
``planned-profiled`` and ``planned-profiled-proofs`` backends. These do not enlarge
the default benchmark backend list. For example::

   uv run python -m benchmarks.ontology --backends native native-proofs planned planned-proofs planned-profiled planned-profiled-proofs --sizes 16 128 --repeats 3 --threads 1 --measure-reuse --temp-root /tmp/opencode --output /tmp/opencode/re04-plans.json

``pipeline_s`` includes first-run module import/preparation, conversion, evaluation
and decoding, but excludes separately measured common imports/fixture construction.
``prepare_s`` measures plan construction after module import. ``reuse_s`` measures
a second complete evaluation with the same already prepared plan/input; default
native modes also run twice for comparison. The second run is outside first-run
pipeline timing; full-worker peak RSS includes both evaluations and retained first
results. Every row checks exact analytical closure and repeat-result/witness parity.

This is reusable architecture and explicit inspection, **not a throughput-speedup
claim**. Profiling adds candidate-level overhead and is deliberately opt-in.
Reusing a plan does not reuse input indexes or avoid evaluating the closure.
Source extraction, persistent planning, native query operators and production
backend selection remain separate roadmap work.

The committed ``benchmarks/results/reasoning-re04-plans.json`` contains **144
successful rows** from clean source ``cb4351e`` (Python 3.13, three fresh workers
per workload/size/mode, one-thread runtime caps). Median closure-only N=128 first
pipeline / second evaluation timings were:

.. list-table:: First pipeline / second evaluation (milliseconds)
   :header-rows: 1

   * - Workload
     - Default indexed
     - Prepared, unprofiled
     - Prepared, profiled
   * - Hierarchy
     - 2.20 / 1.73
     - 5.81 / 1.76
     - 6.02 / 1.95
   * - Sparse recursive
     - 5.78 / 5.39
     - 9.38 / 5.41
     - 10.92 / 6.96
   * - Dense closure
     - 536.74 / 543.22
     - 550.95 / 562.51
     - 709.14 / 704.66

Preparing these small programs after import took roughly 0.05–0.07ms. Cold
prepared pipelines also include first import of the opt-in planning module;
the second invocation does not. Dense witness-enabled pipeline medians were
631.68ms default, 629.37ms prepared without profiling and 787.13ms with profiling.
Maximum full-worker RSS stayed below 58 MiB across these runs, including repeated
evaluation and retained first results. These synthetic shared-host results show
no material throughput gain from preparation on the measured programs; detailed
profiling has a visible cost. They justify optional inspection and reusable
architecture, not a default-mode change or production latency guarantee.
