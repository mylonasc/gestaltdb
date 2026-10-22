:orphan:

Temporal conformance, performance gates and audit workflows
============================================================

RE-E4 exits here. The supported temporal/profile matrix is explicit, every
supported combination verifies independently, and the when/why workflows
below say exactly which mode to reach for. Anything outside the matrix
rematerializes explicitly — failure never yields a partial interval result.

Supported combinations
----------------------------------------

- Positive typed rules + interval validity: full range closure, segment
  memoization, DRed-style maintenance over change segments, per-interval
  evidence, pinned catalogs and staged generations. Verified by
  ``verify_closure`` (independent re-segmentation, per-segment point
  evaluation, derivation and coverage checks).
- Stratified negation / aggregates / closed scopes: point evaluation only,
  via explicit per-point stratified runs. Range evaluation rejects these
  fragments; there is no range-negation mode.
- Equality: per-segment canonicalization with per-segment scopes. No
  global merges across segments or horizons.
- Storage backends: historical readback and orphan recovery run where the
  backend capability exists and skip otherwise; pure-core behavior never
  depends on a backend.

``verify_closure`` is the conformance gate: supported paths verify, and
any drift — stale segments, missing supports, uncovered validity — fails
closed demanding rematerialization.

Measured gates
----------------------------------------

``benchmarks/reasoning_temporal.py`` freezes the evidence in
``benchmarks/results/reasoning-re22-temporal.json`` (fragmented 20/60-edge
chains, 3 repeats): extraction is sub-millisecond (pure cut-point sweep),
inference ~3.5ms/11.6ms, single-validity updates ~0.9ms/5.7ms against
~3.4ms/12ms full rebuilds with 38/1 and 118/1 reuse ratios. Peak RSS stays
under 55MB. Limit breaches raise before publishing anything: failed runs
return no closure, no generation and no index.

When/why workflows
----------------------------------------

- *When did this hold?* Point-query ``closure.at(t)`` for instants;
  ``answers(fact)`` for the exact validity union with per-interval
  evidence from ``evidence(fact)``.
- *Why did this change?* ``update_range`` reports added/removed facts
  plus reuse diagnostics; every removal traces to changed premises.
- *Is this generation trustworthy?* ``verify_closure`` re-derives the
  segmentation, re-evaluates every segment and checks every derivation;
  pin the generation with ``RuleCatalog``/``PinnedGeneration`` and staged
  compare-and-publish at the storage boundary.
- *What broke the build?* Re-run the failing mode with
  ``incremental=False``: agreement implicates the memoization path,
  disagreement implicates the inputs.
