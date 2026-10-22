:orphan:

Benchmark-gated scale decisions
============================================================

RE-26 profiles first and adopts nothing on speculation. The join-shape
profiler (``benchmarks/reasoning_joins.py``) freezes its evidence in
``benchmarks/results/reasoning-re26-joins.json``; the crossover comparison
measures DRed against full rebuild across change sizes; spill and worker
execution stay behind explicit ownership, cancellation and disk budgets.
No second runtime is adopted, no constant-memory or zero-copy promises
are made, and the common operator API is pinned.

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

Decision record
----------------------------------------

- **No bitmap/CSR/transitive-specialized/multiway engine**: the indexed
  Python path is linear on chains and join-bound (not iteration-bound)
  on stars; a bespoke kernel has no measured need. Revisit with a
  workload where iterations, not joins, dominate.
- **No differential/signed-update runtime and no B/F engine**: DRed
  beats rebuild by 9–50x from single-edge to half-closure changes; a
  B/F engine would need per-fact delta propagation to beat DRed on
  sub-edge-granularity updates with huge unaffected closures — that
  workload is documented, not observed.
- **No out-of-core execution**: spill requires caller-owned directories
  with explicit byte budgets (``SpillBudget`` fails closed otherwise);
  workers require fixed counts plus cooperative cancellation
  (``WorkerPolicy``); RSS/thread/I/O numbers accompany any future claim.
- **Common operator API preserved**: ``check_operator_api`` pins the
  ``v1`` entry points every engine must expose; operator drift fails
  closed.
