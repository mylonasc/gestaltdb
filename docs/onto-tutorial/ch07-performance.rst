Performance characteristics (advanced)
============================================

Measure whole pipelines, not kernels. The roadmap (#156) freezes budgets per
slice before selecting executors; dense and sparse workloads are benchmarked
separately (quadratic closure is not constant-memory).

* Extraction: bounded batches, exact evidence IDs, capability probing; profile
  extraction separately from fixpoint (RE-05 evidence
  ``benchmarks/results/reasoning-re05-extraction.json``).
* Joins: indexed Python selects binding-index buckets; it is not an unindexed
  full Cartesian scan. The RE-03 closure-only comparison at source ``14ade5a``
  used Python 3.13 and three fresh workers per cell. Dense N=128 median pipeline
  times were 46.44ms Polars versus 534.73ms indexed Python; sparse recursive
  N=1024 also favored Polars (40.40ms versus 90.37ms), whereas hierarchy,
  duplicate-evidence, and small N=16 cases favored Python. Peak worker RSS was
  about 127 MiB versus 54 MiB. These are historical workload observations, not
  current-machine benchmarks or universal speedups. Pipeline timings include
  compilation, conversion, fixpoint, and decoding, but exclude package imports
  and fixture construction. See :doc:`../reasoning-polars` for the tables and
  reproduction commands; proof and maintenance modes are not equivalent.
  Arrow imports copy and canonicalize; little-endian exports can share column
  payloads. Neither means that KV extraction or all downstream operations are
  zero-copy (:doc:`../reasoning-batches`).
* Plans: SCC schedules + delta sites are reusable architecture, not a proven
  throughput gain on small programs (RE-04); telemetry off by default.
* Maintenance: DRed vs rebuild crossover measured with P50/P95, RSS, supports
  and write amplification (RE-18); redundant cycles are no-ops.
* Temporal: interval fragmentation, per-segment evidence, reuse ratios (RE-22);
  event-sweep scheduling is open (#254).

Always report: cold start, compile, extraction/auth, conversion, join/fixpoint,
proofs/supports, publication, decode, total pipeline, peak RSS, thread count,
wheel footprint. Cancellation is cooperative (``ReasoningCancelledError``,
no partial results); hard deadlines need worker isolation.

A useful cost model
-------------------

.. math::

   T_{\mathrm{total}} = T_{\mathrm{extract}} + T_{\mathrm{compile}}
       + T_{\mathrm{convert}} + T_{\mathrm{fixpoint}}
       + T_{\mathrm{evidence}} + T_{\mathrm{publish}}

These are conceptual stages, not an assertion that every executor exposes
separate timers for each. Speeding up joins cannot remove extraction or proof
cost. Materializing reachability on an ``n``-node directed chain produces
``n(n-1)/2`` distinct pairs: quadratic output is inherent even if joins become
faster. Compact binary rows use 16 payload bytes per pair before dictionaries,
Python staging objects, support indexes, and allocator overhead. Measure both
final payload and peak process RSS; they answer different questions.
