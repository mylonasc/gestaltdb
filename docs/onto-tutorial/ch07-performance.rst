Performance characteristics (advanced)
============================================

Measure whole pipelines, not kernels. The roadmap (#156) freezes budgets per
slice before selecting executors; dense and sparse workloads are benchmarked
separately (quadratic closure is not constant-memory).

* Extraction: bounded batches, exact evidence IDs, capability probing; profile
  extraction separately from fixpoint (RE-05 evidence
  ``benchmarks/results/reasoning-re05-extraction.json``).
* Joins: indexed-Python nested-loop vs Polars equijoin prototype — dense N=128
  median 46ms Polars vs 535ms indexed Python, but small/sparse workloads were
  slower with higher memory (RE-03). No universal zero-copy claim; Arrow
  exchange copies and canonicalizes.
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
