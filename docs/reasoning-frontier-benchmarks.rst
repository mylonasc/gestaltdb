:orphan:

Pinned reasoning pipeline decision: RE-342
================================================

Decision (2026-10-24): **retain indexed Python as default; do not automatically
adopt frontier demand, prepared execution, Polars or native extraction.** The
new frontier API is useful selective-read infrastructure, not a demonstrated
latency optimization on these authenticated temporal fixtures.

Evidence
-----------

``benchmarks/reasoning_frontier_pipeline.py`` runs one fixture loader and fresh
workers per method/cache/repeat. Six final reports at clean source
``a1cfceb9cbac2d6b8140bf03f55bcc19fd8354e7`` are committed under
``benchmarks/results/reasoning-re342-*.json``. The separate
``initial-limit-diagnostic`` report preserves the earlier aborted run, not an
additional successful measurement.

The final matrix has 144 observations: 126 parity-verified results and 18
bounded Polars no-result diagnostics. ``complete`` means every requested cell
was recorded; ``all_parity_verified`` is false in reports containing bounded
outcomes. Bound diagnostics are excluded from successful timing summaries.
Unexpected errors/timeouts leave a report incomplete and fail the command.

Fixtures use variable entity identifiers and 32--288 character provenance
payloads, one assertion per claim, fixed world/valid-time and 1000-write commits.
The selective case has an eight-edge reachable chain plus unrelated edges;
the sparse case adds disconnected three-edge chains. The denser-closure case
has a 128-edge chain plus distractors (1024 inputs, 10176 full-closure facts),
not a large dense graph certification. Closure goal answers are checked against
an analytical oracle. Full evaluators also check the entire analytical closure
and exact input version-ID digest. Proof measurements are separate.

Every sample records Python/platform/dependency versions, thread configuration,
stage timings, actual yielded claim/raw rows, goal/closure/evidence digests and
worker peak RSS. Three repetitions provide descriptive medians and nearest-rank
P95s (with three samples P95 is the maximum, not a robust tail estimate).
Polars/OMP thread settings are one. Cache labels mean first extraction on a
reopened handle versus same-method priming: **OS cache is uncontrolled; first
is not OS-cold**. Methods run in fixed order, so cache and host effects remain
limitations. Claims below apply only to this fixture matrix and host.

Measured result
-----------------

Median total pipeline seconds (imports, program construction, reopen, read-view
opening, extraction/authentication, preparation/conversion/evaluation and final
verification). Fixture ingestion, worker startup, warmup and parity validation
are excluded; separate worker-wall and stage fields retain those boundaries.

.. list-table:: Representative medians; first / primed
   :header-rows: 1

   * - Fixture
     - Indexed Python
     - Prepared indexed
     - Frontier
   * - PyRex selective, 100K
     - 10.729 / 7.244
     - 10.692 / 7.228
     - 23.898 / 20.299
   * - PyRex sparse, 100K
     - 11.428 / 8.196
     - 11.415 / 8.168
     - 23.828 / 20.128
   * - LMDB selective, 100K
     - 7.832 / 4.481
     - 7.816 / 4.533
     - 10.963 / 7.668
   * - LevelDB selective, 10K
     - 1.064 / 0.718
     - not measured
     - 1.712 / 1.362

Frontier queries yield eight asserted versions rather than 100K in the selective
fixtures, but repeated authentication/query setup outweighs avoided inference.
The source-verification contract remains intact; row pruning does not bound
internal index, history or authentication work. Worker peak RSS medians on
PyRex selective-first were about 445 MB frontier versus 632 MB indexed;
LMDB about 865 MB versus 1566 MB. These high-water marks include warmup (when
selected), mapped pages and oracle validation, not isolated query allocations.
They are not evidence of a hard RSS bound.

Prepared indexed execution is effectively tied with reference execution on
these cases. On the 1024-input denser closure, reference medians were
0.409 / 0.362 seconds, Polars 0.497 / 0.414, and frontier 2.922 / 2.888.
Polars conversion is included in the evaluation stage, not independently
isolated or free. In this recorded measurement cut, at 100K the real dispatcher Polars path raises
``max_intermediate_rows exceeded before materialization`` under its fixed
default intermediate cap; no production limit was raised to manufacture parity.
This limits application admission despite a declared installed runtime.
The 1024-input proof case passed for both indexed engines at roughly
0.345 / 0.309 seconds, with bounded witness semantics unchanged.

Native boundary and decision
------------------------------

Benchmark-only PyRex modes compare native temporal-record scan/decode and native
predicate-index locator scan followed by point materialization. Both retain
canonical commit authentication, exact version IDs, a writer guard and prefix
checks. Their caller-owned decoder is restricted to a quiescent assertion-only
fixture: it is **not** a production historical winner selector, correction/
retraction admission or concurrent read protocol. Raw bytes never become facts
without decoding/admission. Native Arrow batches still copy into Python rows;
copied bytes are explicitly uninstrumented, never assumed zero.

At 100K selective PyRex inputs, native decoded medians were 13.373 / 6.450
seconds; locator medians 13.816 / 6.949. The decoded primed result is about 11%
below reference total time, but first-read time is worse and worker peak RSS
is roughly 1025 MB versus 635 MB in the primed reference. This is not sufficient
to adopt a general native claim consumer. The small denser case showed no
material native pipeline win. No universal native-speedup claim follows.

The next performance investigation should safely amortize repeated source
authentication within a pinned extraction session, with corruption, rebinding,
concurrent-write and cancellation parity gates. Do not simply skip verification
or trust an opaque cached digest. Explicit intermediate-cap ownership is now
available through the dispatcher/current-graph consumer (see
:doc:`reasoning-executors`); the default remains unchanged, and these original
bounded failures are retained. Larger successful Polars pipeline measurements
remain a follow-up: raising the cap alone is not executor admission, a memory
guarantee or new performance evidence.
Broader #194/#249/#255 acceptance remains separate from this measurement cut.

Reproduction
---------------

Install backend/Polars extras and ``pyrex-rocksdb>=0.5.0``. Reports contain exact
versions and harness hashes. For example:

.. code-block:: console

   uv run --extra backends --extra polars --with 'pyrex-rocksdb>=0.5.0' python -m benchmarks.reasoning_frontier_pipeline --rows 100000 --shape selective --backend pyrex --methods frontier indexed-python indexed-scc polars-prototype native-decoded native-locator --repeats 3 --output /tmp/opencode/re342-selective.json
   uv run --extra backends --extra polars --with 'pyrex-rocksdb>=0.5.0' python -m benchmarks.reasoning_frontier_pipeline --rows 100000 --shape sparse --backend pyrex --repeats 3 --output /tmp/opencode/re342-sparse.json
   uv run --extra backends --extra polars --with 'pyrex-rocksdb>=0.5.0' python -m benchmarks.reasoning_frontier_pipeline --rows 1024 --shape dense --backend pyrex --methods frontier indexed-python indexed-scc polars-prototype native-decoded native-locator --repeats 3 --output /tmp/opencode/re342-dense.json
   uv run --extra backends --extra polars python -m benchmarks.reasoning_frontier_pipeline --rows 100000 --backend lmdb --repeats 3 --output /tmp/opencode/re342-lmdb.json
   uv run --extra backends --extra polars python -m benchmarks.reasoning_frontier_pipeline --rows 10000 --backend leveldb --methods frontier indexed-python --repeats 3 --output /tmp/opencode/re342-leveldb.json
   uv run --extra backends --extra polars python -m benchmarks.reasoning_frontier_pipeline --rows 1024 --backend lmdb --methods indexed-python indexed-scc --proofs --repeats 3 --output /tmp/opencode/re342-proofs.json
