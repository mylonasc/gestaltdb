Reasoning scale and resource boundaries
============================================================

Indexed Python performs bounded joins and fixpoint evaluation. Runtime cost
depends on join shape, closure size and update scope, not just input row count.
The repository profiler ``benchmarks/reasoning_joins.py`` and report
``benchmarks/results/reasoning-re26-joins.json`` compare join shapes and DRed
against full rebuild. These are reference workloads, not latency guarantees.

Measured profile
----------------------------------------

- Transitive chains: 65/230/860 facts close in ~0.6/1.6/6.1ms with
  11/21/41 iterations — linear fixpoint scaling, no specialized kernel
  needed at these shapes.
- Star multiway joins: 440 facts in ~3.1ms, 6,560 facts in ~52ms, one
  iteration — wide fan-in is join-bound, not iteration-bound.
- Dense small graph (8 nodes, 120 facts): ~1.6ms in 3 iterations.
- DRed vs rebuild on a 24-edge chain: single-edge 4.5ms vs 225ms
  (work 25 vs 152,376), two-edge 4.2ms vs 190ms, half-closure 2.1ms vs
  19.2ms. Incremental wins everywhere measured; rebuild converges only
  toward global changes. Peak RSS stays under 62MB throughout.

Available execution and resource policies
------------------------------------------------------------

- No bitmap, CSR, transitive-specialized or multiway native inference engine is
  provided. Optional prepared plans and closure-only Polars are explicit choices
  (:doc:`reasoning-executors`), not automatic replacements for indexed Python.
- Incremental maintenance uses bounded supports and DRed. Differential/signed
  update and backward/forward engines are not provided. Compare incremental work
  with a fresh rebuild on the same update shape (:doc:`reasoning-maintenance`).
- No executor implements spill or out-of-core closure. ``SpillBudget`` and
  ``WorkerPolicy`` describe explicit resource policies; constructing one does not
  enable spilling or parallel execution. Use external process limits for hard
  memory and time bounds.
