:orphan:

Executor decision and production hardening
============================================================

RE-06 selects the production executor from the frozen RE-01 budgets and the
measured RE-03/RE-04/RE-05 evidence, without introducing a new execution
profile. ``gestaltdb.reasoning.executors.select_executor`` is deterministic and
stdlib-only: identical requests always agree, unknown profiles/modes/preferences
fail closed, and optional-runtime availability is caller-declared, never
probed by importing optionals here.

An authenticated complete mutable-graph consumer is available via
:doc:`reasoning-current-admission`; it owns source projection and exact
generation binding before calling the explicit dispatcher.

.. code-block:: python

   from gestaltdb.reasoning import ExecutorRequest, select_executor

   default = select_executor(ExecutorRequest())
   assert default.kind == "indexed-python-v1"
   explicit = select_executor(ExecutorRequest(preferred="polars-prototype", polars_available=True))
   assert explicit.kind == "polars-prototype-v1"

The decision
----------------------------------------

- **Default: ``indexed-python-v1``** (``DatalogEngine``). Reference behavior,
  smallest startup cost and worker RSS, fastest small/interactive pipelines,
  and the only engine with bounded native witness collection. It stays the
  default even where columnar alternatives are faster; speed alone does not
  switch it.
- **Opt-in: ``polars-prototype-v1``** for explicit closure-only requests with a
  declared-available runtime. It must never be selected implicitly: imports are
  heavy, RSS is higher, small workloads are slower, and it rejects proof
  collection.
- **Opt-in: ``indexed-scc-v1``** (``PlannedDatalogEngine``) for reusable plans
  and opt-in telemetry with the same indexed matcher and witness logic.
- **Proofs always route to indexed engines.** Requesting the Polars prototype
  with ``mode="proofs"`` fails explicitly instead of silently dropping
  provenance.
- The independent finite-grounding oracle used by the test suite remains the
  **reference backend** for soundness and completeness checks; it is a test
  oracle, not a shipped runtime, and native/columnar paths are measured
  against it rather than replacing it.

Measured basis (clean-source medians, Python 3.13, three fresh workers per
cell; full reports in ``benchmarks/results/reasoning-re03-*.json``):

- Dense N=128 pipeline: 534.73ms indexed vs 46.44ms Polars (Clingo 54.29ms).
- Sparse recursive N=1024: 90.37ms indexed vs 40.40ms Polars.
- Hierarchy/duplicates N=1024: indexed slightly faster (17.66/16.42ms vs
  21.27/21.12ms). All N=16 cases favor indexed Python.
- Peak full-worker RSS: ~54 MiB indexed vs ~127 MiB Polars. Four threads did
  not materially improve the tested sparse workloads.
- Prepared SCC plans add no throughput on the measured programs (dense N=128:
  550.95ms unprofiled vs 534.73ms default); profiling is measurably costly
  and stays opt-in.

These are small synthetic observations with identical independent evidence
wrappers, not production-scale guarantees. No million-row, spill or
maintenance workload has been measured; executor claims stop where the
evidence stops.

Deferred native kernels and spill
----------------------------------------

No bespoke C++/Rust kernel is implemented in this slice, deliberately. The
measured dense-closure gap is already covered by the optional Polars path, the
default keeps the smallest footprint, and keyed indexed joins handle the tested
scale. A native kernel becomes justified only with representative workloads
that exceed the frozen RE-01 budgets or maintenance evidence that indexed
rounds cannot meet; that work belongs to the benchmark-gated RE-26 track, not
to an implicit rewrite. This is a recorded defer decision, not a missing
delivery.

No executor implements spill: intermediate budgets fail closed before
materializing unbounded joins, and those counters are not exact allocator,
hash-table or RSS guarantees. Arrow exports share immutable payloads on
little-endian platforms (byte-swapped copies elsewhere); imports copy, sort
and validate. Use worker isolation with external memory limits for hard RSS
bounds.

Cancellation, limits and proof modes
----------------------------------------

All engines accept an optional cooperative ``cancel`` callable and the three
source adapters pass it through to evaluation. Checks run before input
consumption, every 1024 input facts and between delta rounds (Polars: between
native rounds). Cancellation raises ``ReasoningCancelledError`` — a distinct
``ReasoningError``, not a limit error — with no partial closure and no
poisoned reuse. Non-callable tokens fail closed; token exceptions propagate.

Cooperative checks never interrupt a running native operator, and input/chunk
granularity means a single huge round still runs to completion. For hard
deadlines and RSS caps, run evaluations in isolated workers with
process-level timeouts and memory limits; the in-process counters
(``max_candidate_rows``, ``max_intermediate_rows``, ``max_facts`` and
limits) bound logical work, not wall time or bytes.

Proof-mode matrix: ``indexed-python-v1`` and ``indexed-scc-v1`` collect
bounded single witnesses (truncation never truncates closure);
``polars-prototype-v1`` rejects proof collection before consuming input.
Adapters never substitute the prototype into claims/publication paths.

Opt-in executor-aware evaluation
----------------------------------------

``gestaltdb.reasoning.adapters.execution.execute_closure`` is the single
opt-in entry that connects decisions to real engines. It verifies an
explicit ``ExecutorRequest`` (request mode must match proof collection),
optionally admits a bound ``GenerationIdentity`` with exact dictionary,
mode and executor compatibility, dispatches to the decided engine, and
returns the closure with uniform ``StageTelemetry`` (executor, mode,
rationale, admission/plan digests, input/output counts, proof flag).

Prepared-plan execution requires the caller to supply the matching
``PreparedProgram`` and an independently supplied ``InputContext``. Bound
generation execution also requires that context. Before input iteration the
dispatcher compares the actual program, context, profile, external-string
dictionary and selected executor with the supplied plan/admission identities.
These compatibility checks do not authenticate caller-supplied facts or prove
closure soundness. Compact-dictionary generations require explicit recoding;
they cannot be admitted to this external-string entry point.
Polars execution requires an explicit ``InputContext``
and a declared-available runtime. Omitting the preference keeps the indexed
reference even when Polars is installed; there is no automatic switching
and no dictionary recoding — mismatches fail closed. Request/program/type
errors, cancellations and unknown combinations fail before any engine runs.
``collect_proofs=True`` routes to indexed witnesses only.

The dispatcher buffers at most ``max_input_facts`` inputs, consuming one extra
value only to detect overflow. Duplicates count toward that limit. Cooperative
cancellation is checked before and after each iterator advance; it cannot
interrupt a blocked or long-running ``next()`` call. Iterator failures propagate
without a successful partial outcome. This is a row bound, not a byte/RSS cap.
Buffered values retain their original ``Fact``/``Evidence`` identity; indexed
witness engines retain exact references for their selected bounded witnesses,
not all independent supports. Polars consumes
the same evidence values but projects them to set-valued facts internally for
closure-only conversion. It does not return proofs or all-support provenance.
Actual pinned frontier/reference/prepared/Polars/native pipeline evidence and
the no-automatic-adoption decision are in :doc:`reasoning-frontier-benchmarks`.
In particular, installed Polars availability does not imply that an application's
intermediates fit the dispatcher's fixed prototype cap.

Installation isolation
----------------------------------------

The base wheel ships no optional dependencies. Missing ``pyarrow``,
``polars``, ``rdflib``, backend packages or ``pyrex`` fail at first use with
the requiring extra named (e.g. ``gestaltdb[polars]``,
``gestaltdb[rocksdb]``); the installed-wheel smoke tests assert exactly
that on every supported Python. CI exercises Python 3.10–3.13 with the
LMDB/LevelDB/Arrow/Polars/solver and PyRex 0.5.0 extras plus the base-wheel
smoke, so neither the default nor the opt-in paths can silently depend on an
undeclared package. See the phase exit evidence in the roadmap register.
