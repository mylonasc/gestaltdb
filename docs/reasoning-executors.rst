Executor selection and resource limits
============================================================

``gestaltdb.reasoning.executors.select_executor`` is deterministic and
stdlib-only: identical requests always agree, unknown profiles/modes/preferences
fail closed, and optional-runtime availability is caller-declared, never
probed by importing optionals here.

An authenticated complete mutable-graph consumer is available via
:doc:`reasoning-current-admission`; it owns source projection and exact
generation binding before calling the explicit dispatcher.

Positive typed application execution uses the distinct existing GenericEngine
kind, ``typed-indexed-python-v1``, with the same common telemetry and explicit
source-session admission. See :doc:`reasoning-typed-execution`. The binary
default remains indexed Python; optional engines require an explicit request.

.. code-block:: python

   from gestaltdb.reasoning import ExecutorRequest, select_executor

   default = select_executor(ExecutorRequest())
   assert default.kind == "indexed-python-v1"
   explicit = select_executor(ExecutorRequest(preferred="polars-prototype", polars_available=True))
   assert explicit.kind == "polars-prototype-v1"

Available executors
----------------------------------------

- **Default: ``indexed-python-v1``** (``DatalogEngine``). Indexed positive binary
  evaluation with optional bounded witness collection. It stays the default
  regardless of installed optional runtimes or workload size. Prepared indexed
  plans below also support bounded witnesses.
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

Qualified synthetic comparisons, including conversion/startup costs and worker
RSS, are retained in :doc:`reasoning-polars` and :doc:`reasoning-plans`.
End-to-end source and executor measurements are in
:doc:`reasoning-frontier-benchmarks`. These are evidence for the named workloads,
not production latency/RSS guarantees or an automatic selection policy.

Native kernels and spill
----------------------------------------

There is no bespoke C++/Rust inference kernel. Indexed Python remains the default;
the optional Polars path uses compiled columnar operations for its supported
closure-only fragment. Neither path is selected automatically from workload size.

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
opt-in entry for explicit executor dispatch. It verifies an
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
their measurement limits are in :doc:`reasoning-frontier-benchmarks`.
In particular, installed Polars availability does not imply that an application's
intermediates fit the dispatcher's default prototype cap.

Explicit prototype physical budget
----------------------------------------

``execute_closure(..., polars_max_intermediate_rows=N)`` and
``execute_current_graph(..., polars_max_intermediate_rows=N)`` expose the existing
prototype's physical intermediate-row bound. Omission retains **100,000**;
there is no automatic increase based on input size. A supplied value must be a
positive integer (not a Boolean), and is rejected before input/projection reads
unless the request explicitly selects ``polars-prototype``. Indexed execution
never ignores the option or switches engines to honor it.

.. code-block:: python

   from gestaltdb.reasoning import Atom, Fact, Program, Rule, Variable, ReasoningLimits
   from gestaltdb.reasoning.executors import ExecutorRequest
   from gestaltdb.reasoning.protocols import InputContext
   from gestaltdb.reasoning.adapters.execution import execute_closure

   x, y = Variable("x"), Variable("y")
   program = Program([Rule("r", [Atom(x, "edge", y)], Atom(x, "reach", y))])
   outcome = execute_closure(program, [Fact("a", "edge", "b")],
       executor_request=ExecutorRequest(preferred="polars-prototype", polars_available=True),
       context=InputContext("example", "world", "revision", "policy", "mapping"),
       limits=ReasoningLimits(), polars_max_intermediate_rows=200_000)
   assert Fact("a", "reach", "b") in outcome.result.facts
   assert outcome.polars_run.metrics["max_intermediate_rows"] == 200_000

The physical bound is not part of logical generation/program/catalog/dictionary
identity. All source/context/executor admission checks and the cumulative
``ReasoningLimits.max_candidate_rows`` and ``max_facts`` checks still apply.
Raising one budget does not disable another, add spill, interrupt native kernels,
authenticate caller facts, or guarantee completion/RSS. It only permits the
caller to make the existing closure-only experiment with an explicit bound.

``ExecutionOutcome.polars_run`` retains the native ``PolarsRun`` for successful
Polars invocations (``None`` for indexed engines). ``StageTelemetry`` remains
the same common schema; native metrics separately report the chosen cap,
observed conservative peak rows, cumulative output-row accounting and timings.
Those candidate rows are not indexed Python attempted-unification counts.
There is no successful partial outcome after cancellation or a bound failure.

Installation isolation
----------------------------------------

The base wheel ships no optional dependencies. Missing ``pyarrow``,
``polars``, ``rdflib``, backend packages or ``pyrex`` fail at first use with
the requiring extra named (e.g. ``gestaltdb[polars]``,
``gestaltdb[rocksdb]``). Install the extras needed by the selected workflow;
installing Polars does not change the default executor. See :doc:`installation`.
