:orphan:

Experimental Polars reasoning
============================================================

``gestaltdb.reasoning.adapters.polars.PolarsDatalogPrototype`` is an explicit,
optional experiment for the existing binary, positive, named-entity Datalog
profile. Install ``gestaltdb[polars]``. The default remains ``DatalogEngine``;
there is no automatic backend selection, source extraction or publication.
The adapter/compiler import boundary remains inward; only this optional adapter
loads Polars and Arrow, on demand.

.. code-block:: python

   from gestaltdb.reasoning import Atom, Fact, Program, ReasoningLimits, Rule, Variable
   from gestaltdb.reasoning.adapters.polars import PolarsDatalogPrototype
   from gestaltdb.reasoning.protocols import InputContext

   x, y, z = Variable("x"), Variable("y"), Variable("z")
   program = Program([Rule("transitive", [Atom(x, "p", y), Atom(y, "p", z)], Atom(x, "p", z))])
   run = PolarsDatalogPrototype().evaluate(
       program, [Fact("a", "p", "b"), Fact("b", "p", "c")],
       context=InputContext("in-memory-example", "default", "none", "all", "identity"),
       limits=ReasoningLimits(), max_intermediate_rows=100_000,
   )
   assert Fact("a", "p", "c") in run.result
   assert run.result.proofs is None
   assert run.identity.executor_version == "polars-prototype-v1"
   assert run.metrics["join_calls"] > 0

``PolarsRun`` contains an ordinary complete ``EvaluationResult`` plus an exact
``CompilationIdentity`` and immutable telemetry. It is deliberately not the
existing ``Evaluator`` protocol: witnesses, all-support provenance, incremental
maintenance, authenticated source evaluation and persisted generations are not
implemented. ``collect_proofs=True`` fails before input consumption. ``Evidence``
inputs affect logical truth as facts only; references are not returned as proofs.
Do not silently substitute this prototype into claims/publication adapters.

Execution and semantic boundary
----------------------------------------

Input conversion is bounded Python work: deduplicate facts, intern full entity/
predicate/constant names with the RE-02 dictionary and transfer compact uint64
relations through Arrow. Final decoding iterates result rows in Python. These
boundary costs are measured, not described as zero-copy KV decoding.

Between boundaries, Polars performs filters, repeated-variable equality checks,
projection, exact equijoins, distinct, union and stable-minus-new anti-joins.
There are no Python per-row callbacks or dictionary conversions inside these
operators. Each body occurrence can serve as the designated delta relation;
identical repeated atoms need only one pivot. A common unit column preserves
ground-atom truth and disconnected-body joins. All new facts are visible in the
next synchronous round, never midway through an active round. Results have exact
set semantics and canonical external-ID ordering, independent of native row order.

Internal semi/anti-join primitives are existence/set operations, **not exposed
negation-as-failure semantics**. Literals, arbitrary-arity execution, negative
premises, aggregates and equality canonicalization remain unsupported. Safe
``Program`` construction and v1 digests are unchanged. Small finite grounding
oracles, generated programs, late-delta/repeated/ground/disconnected bodies and
analytical ontology closures test the selected semantics, not full OWL support.

The prototype rebuilds eager native expression/join plans per rule occurrence and
round. It does not cache lazy plans, reorder body joins by cost, implement SCC
scheduling or offer native proof propagation. Telemetry separates input,
conversion, dispatch planning, per-occurrence expression construction, operator
execution, join-count preflight, fixpoint and decoding. Nested timings overlap;
do not sum fixpoint and operator/preflight durations as independent stages.
``planning_s`` measures Python dispatch construction; ``expression_s`` measures
Python expression construction. Eager native query planning and kernel execution
remain combined in ``operator_s``/``preflight_s``; these are not native compiler
instrumentation or isolated join-kernel timings.

Limits are intentionally different
----------------------------------------

``ReasoningLimits.max_input_facts``, ``max_rules``, ``max_iterations`` and
``max_facts`` retain their input/output meaning. Duplicate evidence counts toward
input bounds; failures return no successful partial run and never publish data.

``max_candidate_rows`` instead counts conservative materialized/output row work:
atom input upper bounds, join outputs, projections, filter-join input upper bounds
and concatenations. This is **not** attempted-unification parity with the indexed
Python engine, and can fail earlier even for the same successful logical closure.
The benchmark reports actual counts separately; do not compare limited runs as
speedups. Proof budgets have no effect because proof mode is rejected.

Before an equijoin, a native grouped multiplicity query counts matching outputs.
``max_intermediate_rows`` is checked before allocating that output. Key-count
query state is bounded by input rows, not the potentially quadratic result.
Native count overflow fails explicitly. Selection/concatenation use conservative
row upper bounds. This protects obvious Cartesian explosions without claiming
exact allocator, hash-table, dictionary, string-size, RSS or disk limits. No spill
is implemented. A running native call is not cooperatively interrupted; use a
separate worker for hard deadlines and external memory controls.

Context identities are compatibility keys supplied by an already validated
caller, not authenticated database horizons. A new invocation rebuilds its state;
different contexts/dictionaries never share caches implicitly. No PyRex scan API,
storage dependency or JVM runtime is required by this prototype.

Measurements and decision
----------------------------------------

Extend the existing analytical comparison runner rather than introduce a second
oracle. For example::

   uv run --with pyarrow --with polars --with clingo python -m benchmarks.ontology --backends native polars clingo --sizes 16 128 --repeats 3 --threads 1 --temp-root /tmp/opencode --output /tmp/opencode/re03-comparison.json
   uv run --with pyarrow --with polars --with clingo python -m benchmarks.ontology --backends native polars clingo --workloads hierarchy recursive duplicates --sizes 1024 --repeats 3 --threads 4 --temp-root /tmp/opencode --output /tmp/opencode/re03-sparse.json

The runner now records thread caps for Polars/OpenMP/BLAS, per-stage telemetry,
runtime installed-file footprint and source digests. Every successful row checks
the identical analytical closure and digest. ``native`` retains the historical
benchmark name for indexed Python, not a compiled extension. Clingo remains a
benchmark-only dependency without witness/resource-accounting equivalence.

Clean-source ``b8571a2`` results (Python 3.13, three fresh workers per cell):

============================ ============== ============== ==============
Workload                     Indexed Python Polars         Clingo
============================ ============== ============== ==============
Dense N=128                  535.42ms       54.04ms        54.12ms
Sparse recursive N=1024      89.22ms        38.90ms        33.62ms
Hierarchy N=1024             17.56ms        20.91ms        18.07ms
Duplicate evidence N=1024    16.33ms        17.86ms        25.28ms
============================ ============== ============== ==============

These are median pipeline times including ontology compilation, initial fact
conversion, full prototype conversion/fixpoint/decoding, but excluding separately
measured package/runtime imports and fixture construction. Small N=16 cases were
consistently slower with Polars. Peak full-worker RSS in the comparison was about
127 MiB for Polars versus 54 MiB indexed Python. Four threads did not improve the
tested N=1024 sparse workloads; the one- and four-thread matrices were rerun
sequentially on the same shared host (not a dedicated CPU-isolated machine).

This supports continuing the columnar experiment, **not switching the default**.
No million-row, universal scaling, proof-mode or maintenance-performance claim is
made. Full RE-06 backend selection still needs representative user workloads,
source extraction, footprint, failure/cancellation and supported-mode evidence.
The compact Python batch container has no independent fixpoint executor to time;
its conversion/transfer costs are measured in the RE-02 report instead.
