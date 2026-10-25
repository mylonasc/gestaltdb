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
bounded failures are retained. The explicit-budget continuation below measures
successful larger Polars pipelines; raising the cap alone is not executor
admission or a memory guarantee. Broader #194/#249 acceptance remains separate.

Explicit-budget continuation: #255
----------------------------------

Four additional reports, ``benchmarks/results/reasoning-re255-*-100k.json``, bind
clean source ``b87ef1c035832425349ce3d426b226b3ce1ebde5`` and harness SHA256
``c09f11c2cc1d817a907caac5ef1fb49590a7af8d542cc42ed68de5f18fc3009d``.
All 48 observations pass exact assertion-version evidence, goal and analytical
full-closure parity (200028 selective / 300019 sparse facts). Collection ran
serially, with three repetitions per method/cache/backend/shape, outside the
checkout. No bounded/error/timeout observations occurred in this continuation;
the original 18 default-cap failures and initial diagnostic remain unchanged.

The caller explicitly chose ``polars_max_intermediate_rows=1000000``; the
production default is still 100000. This is physical configuration, not logical
generation identity or permission to bypass independent input/fact/cumulative
candidate limits. Both methods use the real dispatcher and authenticated pinned
extraction. Reports include Python, platform, dependencies and single-thread
configuration (Polars 1.41.2, Arrow 24.0.0, PyRex 0.6.0, LMDB 2.2.1).

.. list-table:: Total pipeline seconds: median / nearest-rank P95
   :header-rows: 1

   * - 100K fixture
     - Indexed first
     - Polars first
     - Indexed primed
     - Polars primed
   * - LMDB selective
     - 7.899 / 8.114
     - 7.654 / 7.740
     - 4.596 / 4.849
     - 4.190 / 4.202
   * - LMDB sparse
     - 8.737 / 8.776
     - 7.839 / 7.900
     - 5.451 / 5.485
     - 4.524 / 4.575
   * - PyRex selective
     - 10.781 / 11.025
     - 10.421 / 10.616
     - 7.356 / 7.545
     - 6.938 / 7.022
   * - PyRex sparse
     - 11.593 / 12.013
     - 10.767 / 10.770
     - 8.283 / 8.382
     - 7.164 / 7.172

Polars total median improvement is approximately 3--17% here. Evaluation with
conversion takes about 1.04--1.23s selective and 1.37--1.46s sparse, versus
indexed 1.45--1.48s and 2.34--2.36s respectively. Native metrics make conversion
cost visible: Polars conversion medians are 0.34--0.41s and output decode
0.48--0.86s; fixpoint medians are only 0.09--0.14s. These nested native timings
are not additive to the enclosing evaluation stage. Source extraction and
authentication still dominate total time, particularly on PyRex.

Conservative native intermediate peaks are 100028 rows selective and 200019
sparse; cumulative candidate accounting is 2200308 and 3466861 respectively.
These are Polars operator/preflight accounting units, not Python unifications,
RSS measurements or an all-work counter. Copied bytes remain uninstrumented.
Worker high-water RSS (decimal MB) spans about 1569--1616 across individual
LMDB samples and 627--679 across PyRex samples; there is no consistent memory
benefit. More precisely, paired group medians are 1575--1595 indexed versus
1577--1607 Polars on LMDB and 631--662 versus 633--668 on PyRex. RSS includes
mapped pages, priming and parity validation, not isolated engine allocation.

**Decision remains unchanged:** indexed Python stays the default. Explicit
closure-only Polars is a measured option for these bounded workloads with a
caller-owned cap and conversion/runtime prerequisites, not automatic adoption.
Three-sample P95 is merely the maximum; first is not OS-cold, fixed method order
and host effects limit ranking, and the matrix is neither a crossover threshold
search nor a general scale/concurrency/durability certification. Typed proof,
maintenance and range execution do not become Polars-capable through this data.
General native historical/correction/concurrent-source admission remains #194.

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

For the #255 continuation, repeat for ``lmdb`` / ``pyrex`` and
``selective`` / ``sparse`` from a clean committed tree, keeping all output files
outside the checkout until collection is finished:

.. code-block:: console

   uv run --extra backends --extra polars --with 'pyrex-rocksdb>=0.5.0' python -m benchmarks.reasoning_frontier_pipeline --rows 100000 --shape sparse --backend pyrex --methods indexed-python polars-prototype --caches first warm --repeats 3 --timeout 600 --polars-max-intermediate-rows 1000000 --output /tmp/opencode/re255-pyrex-sparse.json
